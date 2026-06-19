"""
SorinFlow Divar Scraper - Statistics API Routes
"""
import json
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from datetime import datetime, timedelta

from app.database import get_db, get_redis
from app.models.property import Property, City, Category
from app.models.scraping_job import ScrapingJob
from app.models.cookie import Cookie
from app.models.user import User
from app.schemas import DashboardStats, SystemHealth
from app.config import get_settings
from app.auth.dependencies import get_current_user

router = APIRouter()
settings = get_settings()

_DASHBOARD_CACHE_TTL = 60  # seconds


@router.get("/dashboard", response_model=DashboardStats)
async def get_dashboard_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get dashboard statistics (cached 60s in Redis, isolated per user's divar_phone)."""
    redis = await get_redis()

    # Filter by user's linked Divar phone; None = show all (super_admin without phone set)
    phone_filter = current_user.divar_phone or None
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

    # Daily scraping (last 7 days)
    daily_scraping = []
    for i in range(7):
        day = datetime.now() - timedelta(days=i)
        day_start = day.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)

        day_result = await db.execute(
            select(func.count(Property.id)).where(
                _base([
                    Property.scraped_at >= day_start,
                    Property.scraped_at < day_end,
                ])
            )
        )
        count = day_result.scalar() or 0
        daily_scraping.append({
            "date": day_start.strftime("%Y-%m-%d"),
            "count": count
        })

    daily_scraping.reverse()

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
        phone_to_check = current_user.divar_phone or settings.divar_phone_number
        query = select(Cookie).where(Cookie.is_valid == True)
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


@router.get("/logs")
async def get_recent_logs(
    lines: int = 200,
    grep: str = "",
    current_user: User = Depends(get_current_user),
):
    """Return last N lines from the scraper log file (admin only)."""
    import os
    log_path = "/app/logs/scraper.log"
    try:
        if not os.path.exists(log_path):
            return {"lines": [], "note": "log file not found"}
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        if grep:
            all_lines = [l for l in all_lines if grep.lower() in l.lower()]
        tail = all_lines[-lines:]
        return {"lines": [l.rstrip() for l in tail], "total_returned": len(tail)}
    except Exception as e:
        return {"error": str(e)}


@router.get("/property-trends")
async def get_property_trends(
    days: int = 30,
    db: AsyncSession = Depends(get_db)
):
    """Get property trends over time"""
    
    trends = []
    
    for i in range(days):
        day = datetime.now() - timedelta(days=i)
        day_start = day.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        
        # Count properties scraped
        count_result = await db.execute(
            select(func.count(Property.id)).where(
                and_(
                    Property.scraped_at >= day_start,
                    Property.scraped_at < day_end
                )
            )
        )
        count = count_result.scalar() or 0
        
        # Count with phone numbers
        phone_result = await db.execute(
            select(func.count(Property.id)).where(
                and_(
                    Property.scraped_at >= day_start,
                    Property.scraped_at < day_end,
                    Property.phone_number.isnot(None)
                )
            )
        )
        phone_count = phone_result.scalar() or 0
        
        trends.append({
            "date": day_start.strftime("%Y-%m-%d"),
            "total": count,
            "with_phone": phone_count
        })
    
    trends.reverse()
    return {"trends": trends}
