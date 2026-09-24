"""
SorinFlow Divar Scraper - Statistics API Routes
"""
import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from datetime import datetime, timedelta, timezone

from app.database import get_db, get_redis
from app.models.property import Property, City, Category
from app.models.scraping_job import ScrapingJob
from app.models.cookie import Cookie
from app.models.user import User
from app.schemas import DashboardStats, SystemHealth
from app.config import get_settings
from app.auth.dependencies import get_current_user

router = APIRouter()

# Logs name job ids, Divar accounts and customer-facing errors — admin only.
from app.auth.dependencies import require_admin as _require_admin  # noqa: E402
settings = get_settings()

_DASHBOARD_CACHE_TTL = 60  # seconds

# Fixed +03:30 — the production image has no tz database (see app/crm/digest.TEHRAN).
TEHRAN = timezone(timedelta(hours=3, minutes=30), "Asia/Tehran")


def _tehran_day_col(dialect_name: str, column):
    """The Tehran calendar day `column` (a UTC timestamp) falls on, as one
    SQL expression — the GROUP BY key that replaces a per-day COUNT loop.

    Postgres: AT TIME ZONE by NAME, not by literal offset. '+03:30' would be
    read under POSIX sign rules and mean UTC-03:30 — the wrong direction —
    and the database server (unlike the Python image) carries tzdata, so the
    zone name resolves. sqlite has no AT TIME ZONE at all: scraped_at is
    written by func.now(), which on sqlite is CURRENT_TIMESTAMP — a naive
    UTC string — so the same shift is explicit interval arithmetic on it
    before truncating to a date.
    """
    if dialect_name == "postgresql":
        return func.date(func.timezone("Asia/Tehran", column))
    return func.date(column, "+3 hours", "+30 minutes")


def _tehran_today_and_utc_cutoff(days: int, dialect_name: str = "postgresql"):
    """Today in Tehran (a date), and the UTC instant that Tehran day started
    `days` ago — the WHERE-clause range bound the GROUP BY runs inside.

    Postgres: kept tz-aware. asyncpg sends an aware datetime as an
    unambiguous instant; a NAIVE one is instead interpreted in the
    connection's own `TimeZone` session setting, which is not guaranteed to
    be UTC (a local Homebrew Postgres can default to the machine's zone) —
    binding naive-and-assumed-UTC silently shifted this cutoff by that
    session's offset.
    sqlite: naive, because that is how scraped_at is actually stored there
    (func.now() is sqlite's CURRENT_TIMESTAMP, a plain UTC string with no
    offset) — a tz-aware bind would compare a "+00:00"-suffixed string
    against sqlite's plain one and silently match nothing.
    """
    today = datetime.now(timezone.utc).astimezone(TEHRAN).date()
    start_day = today - timedelta(days=days - 1)
    cutoff = datetime.combine(start_day, datetime.min.time(), tzinfo=TEHRAN).astimezone(timezone.utc)
    if dialect_name != "postgresql":
        cutoff = cutoff.replace(tzinfo=None)
    return today, cutoff


@router.get("/dashboard", response_model=DashboardStats)
async def get_dashboard_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get dashboard statistics (cached 60s in Redis; all users see the same totals)."""
    redis = await get_redis()

    # All roles see stats over every scraped property
    phone_filter = None
    cache_key = f"stats:dashboard:{phone_filter or 'all'}"
    cached = await redis.get(cache_key)
    if cached:
        return DashboardStats(**json.loads(cached))

    # Build base filter condition
    def _base(extra=None):
        conds = [Property.is_active == True]
        if phone_filter:
            conds.append(Property.owner_phone == phone_filter)
        if extra:
            conds.extend(extra if isinstance(extra, list) else [extra])
        return and_(*conds)

    # Total properties
    total_result = await db.execute(
        select(func.count(Property.id)).where(_base())
    )
    total_properties = total_result.scalar() or 0

    # Properties with phone
    phone_result = await db.execute(
        select(func.count(Property.id)).where(
            _base([Property.phone_number.isnot(None)])
        )
    )
    properties_with_phone = phone_result.scalar() or 0

    # Total cities
    cities_result = await db.execute(
        select(func.count(City.id)).where(City.is_active == True)
    )
    total_cities = cities_result.scalar() or 0

    # Total categories
    categories_result = await db.execute(
        select(func.count(Category.id)).where(Category.is_active == True)
    )
    total_categories = categories_result.scalar() or 0

    # Active jobs
    active_jobs_result = await db.execute(
        select(func.count(ScrapingJob.id)).where(ScrapingJob.status == "running")
    )
    active_jobs = active_jobs_result.scalar() or 0

    # Properties today
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_result = await db.execute(
        select(func.count(Property.id)).where(
            _base([Property.scraped_at >= today])
        )
    )
    properties_today = today_result.scalar() or 0

    # Properties this week
    week_ago = datetime.now() - timedelta(days=7)
    week_result = await db.execute(
        select(func.count(Property.id)).where(
            _base([Property.scraped_at >= week_ago])
        )
    )
    properties_this_week = week_result.scalar() or 0

    # City distribution
    city_dist_result = await db.execute(
        select(
            Property.city_name,
            func.count(Property.id).label('count')
        ).where(_base())
        .group_by(Property.city_name)
        .order_by(func.count(Property.id).desc())
        .limit(10)
    )
    city_distribution = [
        {"city": row[0] or "Unknown", "count": row[1]}
        for row in city_dist_result.all()
    ]

    # Category distribution
    cat_dist_result = await db.execute(
        select(
            Property.category_name,
            func.count(Property.id).label('count')
        ).where(_base())
        .group_by(Property.category_name)
        .order_by(func.count(Property.id).desc())
        .limit(10)
    )
    category_distribution = [
        {"category": row[0] or "Unknown", "count": row[1]}
        for row in cat_dist_result.all()
    ]

    # Daily scraping (last 7 days), by Tehran calendar day — one grouped
    # query instead of seven, each of which used to re-run the same
    # is_active filter.
    dialect = db.bind.dialect.name
    day_col = _tehran_day_col(dialect, Property.scraped_at)
    today, cutoff = _tehran_today_and_utc_cutoff(7, dialect)

    daily_result = await db.execute(
        select(day_col.label("day"), func.count(Property.id).label("count"))
        .where(_base([Property.scraped_at >= cutoff]))
        .group_by(day_col)
    )
    counts_by_day = {str(row.day)[:10]: row.count for row in daily_result.all()}
    daily_scraping = []
    for i in range(6, -1, -1):
        date_str = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        daily_scraping.append({
            "date": date_str,
            "count": counts_by_day.get(date_str, 0),
        })

    result = DashboardStats(
        total_properties=total_properties,
        properties_with_phone=properties_with_phone,
        total_cities=total_cities,
        total_categories=total_categories,
        active_jobs=active_jobs,
        properties_today=properties_today,
        properties_this_week=properties_this_week,
        city_distribution=city_distribution,
        category_distribution=category_distribution,
        daily_scraping=daily_scraping
    )
    await redis.setex(cache_key, _DASHBOARD_CACHE_TTL, result.model_dump_json())
    return result


@router.get("/health", response_model=SystemHealth)
async def get_system_health(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get system health status"""
    
    # Check database
    db_status = "healthy"
    try:
        await db.execute(select(func.count(Property.id)))
    except Exception as e:
        db_status = f"unhealthy: {str(e)}"
    
    # Check Redis
    redis_status = "healthy"
    try:
        redis_client = await get_redis()
        await redis_client.ping()
    except Exception as e:
        redis_status = f"unhealthy: {str(e)}"
    
    # Check scraper (browser availability)
    scraper_status = "ready"
    try:
        from playwright.async_api import async_playwright
        # Just check if playwright is importable
    except Exception as e:
        scraper_status = f"unavailable: {str(e)}"
    
    # Check cookie status — prefer user's permanent phone, then active session, then any valid
    cookie_status = "no session"
    try:
        # The caller's own sessions only — never the configured default or
        # «any valid row», which reported a colleague's session as yours.
        phone_to_check = current_user.divar_phone
        query = select(Cookie).where(Cookie.is_valid == True,  # noqa: E712
                                     Cookie.owner_user_id == current_user.id)
        if phone_to_check:
            query = query.where(Cookie.phone_number == phone_to_check)
        else:
            query = query.order_by(Cookie.updated_at.desc()).limit(1)
        result = await db.execute(query)
        cookie = result.scalar_one_or_none()
        if cookie:
            if cookie.expires_at:
                # Handle timezone-aware vs naive datetime comparison
                expires_at = cookie.expires_at
                now = datetime.utcnow()
                # Make comparison timezone-naive if needed
                if hasattr(expires_at, 'tzinfo') and expires_at.tzinfo is not None:
                    expires_at = expires_at.replace(tzinfo=None)
                
                if expires_at > now:
                    days_left = (expires_at - now).days
                    cookie_status = f"valid ({days_left} days left)"
                else:
                    cookie_status = "expired"
            else:
                cookie_status = "valid (no expiry)"
    except Exception:
        pass
    
    # Overall status
    overall = "healthy"
    if "unhealthy" in db_status or "unhealthy" in redis_status:
        overall = "degraded"
    
    return SystemHealth(
        status=overall,
        database=db_status,
        redis=redis_status,
        scraper=scraper_status,
        cookie_status=cookie_status,
        uptime="N/A"  # Could implement with process start time
    )


@router.get("/today")
async def get_today(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """What is waiting for somebody, right now.

    The dashboard counted things — how many listings exist, how many carry a
    number — and answered no question anyone opens it with. The morning
    question is «what needs me today», and every part of that answer already
    existed: calls due, matches nobody has decided, price drops nobody has
    read, what came in since midnight. It sat three clicks inside the CRM,
    and the assistant in Telegram could say it while the panel could not.

    The counts come from the assistant's own queue reader rather than a second
    copy of the same five queries — one definition of «due», so the dashboard
    and the assistant can never disagree about it. Tehran midnight, like
    everything else the office calls «today».
    """
    from app.ai.assistant import tool_queue_status
    return await tool_queue_status(db)


@router.get("/jobs-summary")
async def get_jobs_summary(
    db: AsyncSession = Depends(get_db)
):
    """Get scraping jobs summary"""
    
    # Jobs by status
    status_result = await db.execute(
        select(
            ScrapingJob.status,
            func.count(ScrapingJob.id).label('count')
        ).group_by(ScrapingJob.status)
    )
    by_status = {row[0]: row[1] for row in status_result.all()}
    
    # Recent jobs
    recent_result = await db.execute(
        select(ScrapingJob)
        .order_by(ScrapingJob.created_at.desc())
        .limit(5)
    )
    recent_jobs = [j.to_dict() for j in recent_result.scalars().all()]
    
    # Total scraped
    total_result = await db.execute(
        select(func.sum(ScrapingJob.new_items))
    )
    total_scraped = total_result.scalar() or 0
    
    return {
        "by_status": by_status,
        "recent_jobs": recent_jobs,
        "total_scraped": total_scraped
    }


def _tail_log(path: str, want: int, needle: str, level: str) -> dict:
    """Read the last matching lines by walking backwards in fixed blocks.

    The previous version did f.readlines() on the whole file and filtered
    afterwards. The log rotates at 20MB, so a search for a rare word read 20MB
    into memory to return 200 lines — and it did it synchronously inside the
    event loop, so every other request waited on the disk. This reads from the
    end and stops as soon as it has enough, which for the common case (recent
    lines, no filter) touches a few kilobytes.
    """
    import os

    if not os.path.exists(path):
        return {"lines": [], "note": "log file not found", "total_returned": 0}

    needle = (needle or "").lower()
    level = (level or "").upper()
    block = 64 * 1024
    found, leftover = [], b""

    with open(path, "rb") as fh:
        pos = fh.seek(0, os.SEEK_END)
        scanned = 0
        # 8MB is the point past which this stops being a tail and starts being
        # a full scan; better to return a partial answer than to stall the box.
        while pos > 0 and len(found) < want and scanned < 8 * 1024 * 1024:
            step = min(block, pos)
            pos -= step
            fh.seek(pos)
            chunk = fh.read(step) + leftover
            scanned += step
            parts = chunk.split(b"\n")
            leftover = parts.pop(0) if pos > 0 else b""
            for raw in reversed(parts):
                if not raw.strip():
                    continue
                line = raw.decode("utf-8", errors="replace").rstrip()
                if needle and needle not in line.lower():
                    continue
                if level and f"| {level}" not in line.upper():
                    continue
                found.append(line)
                if len(found) >= want:
                    break

    found.reverse()          # back into chronological order
    return {"lines": found, "total_returned": len(found)}


@router.get("/logs")
async def get_recent_logs(
    lines: int = Query(200, ge=1, le=1000),
    grep: str = Query("", max_length=200),
    level: str = Query("", max_length=10),
    _: User = _require_admin,
):
    """Recent log lines, newest last. Admin only — logs name jobs and accounts.

    Runs in a worker thread: it is file I/O, and doing it on the event loop
    blocks every other request for the duration of the read.
    """
    from starlette.concurrency import run_in_threadpool
    from app.config import get_settings

    path = str(Path(get_settings().logs_path) / "scraper.log")
    try:
        return await run_in_threadpool(_tail_log, path, lines, grep, level)
    except Exception as e:
        logger.warning(f"[logs] read failed: {e}")
        raise HTTPException(status_code=500, detail="could not read the log")


@router.get("/property-trends")
async def get_property_trends(
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db)
):
    """Property trends over time, by Tehran calendar day.

    Used to be two COUNT queries per day in a Python loop — up to 730 round
    trips for a year of history. One grouped query now does both counts at
    once (func.count(phone_number) skips NULLs the same way .isnot(None)
    did), and the days nothing was scraped are filled in afterwards so the
    response shape — oldest day first, {date, total, with_phone} — is
    unchanged for the chart.
    """
    dialect = db.bind.dialect.name
    day_col = _tehran_day_col(dialect, Property.scraped_at)
    today, cutoff = _tehran_today_and_utc_cutoff(days, dialect)

    rows = (await db.execute(
        select(
            day_col.label("day"),
            func.count(Property.id).label("total"),
            func.count(Property.phone_number).label("with_phone"),
        )
        .where(Property.scraped_at >= cutoff)
        .group_by(day_col)
    )).all()
    by_day = {str(row.day)[:10]: (row.total, row.with_phone) for row in rows}

    trends = []
    for i in range(days - 1, -1, -1):
        date_str = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        total, with_phone = by_day.get(date_str, (0, 0))
        trends.append({"date": date_str, "total": total, "with_phone": with_phone})

    return {"trends": trends}
