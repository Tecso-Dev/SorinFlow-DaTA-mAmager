"""
SorinFlow Divar Scraper - Scraper API Routes
"""
import re
import json
import time
from fastapi import Request, APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, false
from typing import Optional, List
from datetime import datetime
import asyncio
import sys
import os
import uuid
from loguru import logger

from app.database import get_db, get_redis
from app.models.scraping_job import ScrapingJob
from app.scraper.divar_scraper import DivarScraper
from app.config import get_settings, CITIES, CATEGORIES
from app.schemas import ScrapingJobCreate, ScrapingJobResponse, ScrapingJobList
from app.auth.dependencies import get_current_user_optional
from app.models.user import User

router = APIRouter()

# The phone-side SMS forwarder's two endpoints. `router` above is included
# with dependencies=_perm("scraper") — every route on it needs a logged-in
# user — and a phone is not a user. These live on their own router, mounted
# at the same prefix without that gate; their auth is the HMAC check inside
# each route. Found live: signed and unsigned alike answered «Not
# authenticated» from the permission dependency before the handler ran.
machine_router = APIRouter()
settings = get_settings()

# Store active scraping job IDs for tracking
active_tasks = {}


async def run_scraping_job(
    job_id: str,
    city: str,
    category: str,
    max_items: Optional[int],
    download_images: bool,
    db_url: str,
    divar_phone: str = None,
    min_price: int = None,
    max_price: int = None,
    min_deposit: int = None,
    max_deposit: int = None,
    min_rent: int = None,
    max_rent: int = None,
    min_price_per_meter: int = None,
    max_price_per_meter: int = None,
    min_area: int = None,
    max_area: int = None,
    min_rooms: int = None,
    max_rooms: int = None,
    has_images: bool = None,
    has_elevator: bool = None,
    has_parking: bool = None,
    has_storage: bool = None,
    has_balcony: bool = None,
    advertiser_type: str = None,
    max_age_hours: int = None,
    posted_date: str = None,
    rotate_every: Optional[int] = None,
):
    """Background task to run scraping job"""
    # Import here to avoid circular imports and ensure fresh event loop
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    from sqlalchemy.pool import NullPool
    
    # Critical: Create a completely isolated event loop context for background task
    logger.info(f"[{job_id}] Background task started with fresh event loop")
    
    engine = None
    scraper = None
    
    try:
        logger.info(f"[{job_id}] Creating dedicated database engine")
        
        # Create a dedicated engine with NullPool to avoid any connection pooling issues
        engine = create_async_engine(
            db_url,
            echo=False,
            poolclass=NullPool,
            future=True
        )
        
        # Create session maker for this engine
        async_session_maker = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autocommit=False,
            autoflush=False
        )
        
        # Create session
        session = async_session_maker()
        
        try:
            logger.info(f"[{job_id}] Database session created")
            
            # Create scraper with the session
            scraper = DivarScraper(
                db_session=session,
                proxy_enabled=settings.proxy_enabled,
                headless=settings.scraper_headless
            )
            
            logger.info(f"[{job_id}] Initializing Playwright browser (divar_phone={divar_phone or 'auto'})")
            # Ask Divar about every stored session before choosing one. The
            # pool draws from is_valid flags that may be an hour old; a minute
            # here means the first account picked — and every rotation after
            # it — is one Divar accepted moments ago.
            try:
                from app.services import divar_session as _ds
                _sw = await _ds.sweep(reason="pre-run")
                from app.services import job_log as _jl
                await _jl.record(job_id, _jl.SESSION,
                                 f"بررسی نشست‌ها پیش از شروع: {_sw['alive']} فعال، "
                                 f"{_sw['dead']} باطل، {_sw['unknown']} نامشخص",
                                 **_sw)
            except Exception as _e:
                logger.warning(f"[session] pre-run sweep skipped: {_e}")
            initialized = await scraper.initialize(phone_number=divar_phone)
            
            if not initialized:
                logger.warning(f"[{job_id}] Browser initialization incomplete, continuing anyway...")
            
            logger.info(f"[{job_id}] Starting main scraping task")
            
            # This is the main work
            result = await scraper.start_scraping_job(
                job_id=job_id,
                city=city,
                category=category,
                max_items=max_items,
                download_images=download_images,
                min_price=min_price,
                max_price=max_price,
                min_deposit=min_deposit,
                max_deposit=max_deposit,
                min_rent=min_rent,
                max_rent=max_rent,
                min_price_per_meter=min_price_per_meter,
                max_price_per_meter=max_price_per_meter,
                min_area=min_area,
                max_area=max_area,
                min_rooms=min_rooms,
                max_rooms=max_rooms,
                has_images=has_images,
                has_elevator=has_elevator,
                has_parking=has_parking,
                has_storage=has_storage,
                has_balcony=has_balcony,
                advertiser_type=advertiser_type,
                max_age_hours=max_age_hours,
                posted_date=posted_date,
                rotate_every=rotate_every,
            )
            
            logger.info(f"[{job_id}] Job completed: {result.new_items} new, {result.failed_items} failed, Status={result.status}")
            
        except Exception as e:
            logger.exception(f"[{job_id}] Error during scraping: {e}")
            
            # Attempt to mark job as failed in database
            try:
                result = await session.execute(
                    select(ScrapingJob).where(ScrapingJob.job_id == job_id)
                )
                job = result.scalar_one_or_none()
                if job:
                    job.status = "failed"
                    job.error_message = str(e)[:500]
                    job.completed_at = datetime.now()
                    await session.commit()
                    logger.info(f"[{job_id}] Updated job status to failed in database")
            except Exception as db_e:
                logger.error(f"[{job_id}] Could not update job in database: {db_e}")
        
        finally:
            # Close and cleanup scraper
            if scraper:
                try:
                    await scraper.close()
                    logger.info(f"[{job_id}] Scraper closed")
                except Exception as e:
                    logger.error(f"[{job_id}] Error closing scraper: {e}")
            
            # Close session
            try:
                await session.close()
                logger.info(f"[{job_id}] Session closed")
            except Exception as e:
                logger.error(f"[{job_id}] Error closing session: {e}")
    
    except Exception as e:
        logger.exception(f"[{job_id}] Fatal error in background task: {e}")
    
    finally:
        # Dispose engine
        if engine:
            try:
                await engine.dispose()
                logger.info(f"[{job_id}] Engine disposed")
            except Exception as e:
                logger.error(f"[{job_id}] Error disposing engine: {e}")
        
        # Cleanup tracking
        if job_id in active_tasks:
            del active_tasks[job_id]
            logger.info(f"[{job_id}] Removed from active tasks")
        
        logger.info(f"[{job_id}] Background task completed")


@router.post("/start", response_model=ScrapingJobResponse)
async def start_scraping_job(
    job_config: ScrapingJobCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """Start a new scraping job"""
    
    active = {k: v for k, v in job_config.model_dump().items() if v is not None and k not in ('city', 'category', 'max_items', 'download_images', 'divar_phone')}
    logger.info(f"Scraping job request — city={job_config.city} category={job_config.category} max_items={job_config.max_items} images={job_config.download_images} filters={active}")
    
    # Validate city and category
    if job_config.city not in CITIES:
        logger.error(f"Invalid city: {job_config.city}")
        raise HTTPException(status_code=400, detail=f"Invalid city: {job_config.city}")
    
    if job_config.category not in CATEGORIES:
        logger.error(f"Invalid category: {job_config.category}")
        raise HTTPException(status_code=400, detail=f"Invalid category: {job_config.category}")
    
    # Check for existing running jobs
    result = await db.execute(
        select(ScrapingJob).where(ScrapingJob.status == "running")
    )
    running_jobs = result.scalars().all()
    
    if len(running_jobs) >= 3:
        logger.warning(f"Too many running jobs: {len(running_jobs)}")
        raise HTTPException(
            status_code=429,
            detail="Too many running jobs. Please wait for existing jobs to complete."
        )
    
    # Create job record
    job = ScrapingJob(
        status="pending",
        divar_phone=job_config.divar_phone or None,
        created_at=datetime.now()
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    job_id = str(job.job_id)
    logger.info(f"Created scraping job: {job_id} (divar_phone={job_config.divar_phone or 'auto'})")

    # A fresh job re-enables OTP prompts for itself (a previous dismissal
    # shouldn't silently suppress phone extraction on the next run). Scoped to
    # this job so starting one scrape does not lift a dismissal the user just
    # made on another one that is still running.
    from app.scraper import otp_store
    otp_store.reset_cancel(job_id)

    # Store a placeholder to track active jobs
    active_tasks[job_id] = {"status": "starting", "city": job_config.city, "category": job_config.category}

    # Use background_tasks to run the job
    background_tasks.add_task(
        run_scraping_job,
        job_id,
        job_config.city,
        job_config.category,
        job_config.max_items,
        job_config.download_images,
        settings.database_url,
        job_config.divar_phone or None,
        job_config.min_price,
        job_config.max_price,
        job_config.min_deposit,
        job_config.max_deposit,
        job_config.min_rent,
        job_config.max_rent,
        job_config.min_price_per_meter,
        job_config.max_price_per_meter,
        job_config.min_area,
        job_config.max_area,
        job_config.min_rooms,
        job_config.max_rooms,
        job_config.has_images,
        job_config.has_elevator,
        job_config.has_parking,
        job_config.has_storage,
        job_config.has_balcony,
        job_config.advertiser_type,
        job_config.max_age_hours,
        job_config.posted_date,
        job_config.rotate_every,
    )
    
    logger.info(f"Started background task for job {job_id}")
    
    return ScrapingJobResponse(
        id=job.id,
        job_id=job_id,
        divar_phone=job.divar_phone,
        status="pending",
        created_at=job.created_at
    )


@router.get("/jobs", response_model=ScrapingJobList)
async def get_scraping_jobs(
    status: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Get list of scraping jobs"""
    from app.models.property import City, Category
    query = select(ScrapingJob).order_by(ScrapingJob.created_at.desc())

    if status:
        query = query.where(ScrapingJob.status == status)
    if category:
        # Match nothing when the name does not resolve, rather than dropping the
        # filter. Silently dropping it returned every job of every category —
        # which is what «اجاره ویلا» did, because that category was missing from
        # the table while the dropdown offered it. Collecting ids also handles a
        # duplicate name, where scalar_one_or_none() would have raised.
        cat_ids = (await db.execute(
            select(Category.id).where(Category.name == category))).scalars().all()
        query = query.where(ScrapingJob.category_id.in_(cat_ids) if cat_ids else false())

    # Isolate jobs by the user's linked Divar phone (admins see all jobs)
    # root included — it outranks super_admin everywhere else, so it must not
    # be the one account that gets its job list filtered down to a phone.
    is_privileged = current_user and current_user.role in ("root", "super_admin", "admin")
    if not is_privileged and current_user and current_user.divar_phone:
        query = query.where(ScrapingJob.divar_phone == current_user.divar_phone)

    query = query.limit(limit)
    result = await db.execute(query)
    jobs = result.scalars().all()

    # id → name lookups so the UI can show/filter by city & category
    city_map = {c.id: c.name for c in (await db.execute(select(City))).scalars().all()}
    cat_map = {c.id: c.name for c in (await db.execute(select(Category))).scalars().all()}

    return ScrapingJobList(
        items=[ScrapingJobResponse(
            id=j.id,
            job_id=str(j.job_id),
            city_id=j.city_id,
            category_id=j.category_id,
            city_name=city_map.get(j.city_id),
            category_name=cat_map.get(j.category_id),
            status=j.status,
            total_pages=j.total_pages,
            scraped_pages=j.scraped_pages,
            total_items=j.total_items,
            scraped_items=j.scraped_items,
            new_items=j.new_items,
            updated_items=j.updated_items,
            failed_items=j.failed_items,
            error_message=j.error_message,
            progress=j.progress,
            started_at=j.started_at,
            completed_at=j.completed_at,
            created_at=j.created_at
        ) for j in jobs],
        total=len(jobs)
    )


async def _job_uuid_from(job_id: str, db: AsyncSession):
    """Accept either the job UUID or the integer row id the panel also holds."""
    try:
        return uuid.UUID(job_id)
    except ValueError:
        job = (await db.execute(
            select(ScrapingJob).where(ScrapingJob.id == int(job_id))
        )).scalar_one_or_none() if job_id.isdigit() else None
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return job.job_id


@router.get("/jobs/{job_id}/events")
async def get_job_events(
    job_id: str,
    level: Optional[str] = None,
    limit: int = 500,
    db: AsyncSession = Depends(get_db)
):
    """What happened during one run, in order.

    Exists because scraper.log cannot answer it: no line in that file carries a
    job id, so two runs in a day interleave with no way to separate them, and
    it rotates at 10 MB with seven days of retention — so the run somebody
    wants to understand is often the one that has already aged out.

    These rows are written by the scraper itself on its own connection, so a
    run that dies still leaves its account of what it was doing.
    """
    from app.services import job_log

    try:
        job_uuid = uuid.UUID(job_id)
    except ValueError:
        # The panel also holds integer ids from the jobs table.
        job = (await db.execute(
            select(ScrapingJob).where(ScrapingJob.id == int(job_id))
        )).scalar_one_or_none() if job_id.isdigit() else None
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        job_uuid = job.job_id

    rows = await job_log.events_for(db, job_uuid, limit=limit, level=level)
    return {
        "job_id": str(job_uuid),
        "count": len(rows),
        "items": [{
            "id": r.id,
            "level": r.level,
            "stage": (r.details or {}).get("stage"),
            "message": r.message,
            "details": {k: v for k, v in (r.details or {}).items() if k != "stage"},
            "created_at": r.created_at.isoformat() if r.created_at else None,
        } for r in rows],
    }


@router.get("/jobs/{job_id}/skipped")
async def get_job_skipped(
    job_id: str,
    reason: Optional[str] = None,
    limit: int = 1000,
    db: AsyncSession = Depends(get_db)
):
    """The listings this run saw and did not save, with their Divar links.

    The finish line already says how many went and why — «۳۲ خارج از
    دسته‌بندی، ۲ ودیعه، ۳ ناموفق» — but a count can only be checked for
    arithmetic. This is the listings themselves, so they can be looked at and,
    where the run was wrong to drop one, scraped again on their own.
    """
    from app.services import skipped_listings

    job_uuid = await _job_uuid_from(job_id, db)
    rows = await skipped_listings.for_job(db, job_uuid, limit=limit, reason=reason)
    counts = await skipped_listings.counts_for_job(db, job_uuid)

    from app.scraper.divar_scraper import DivarScraper
    labels = DivarScraper._FILTER_LABELS_FA

    return {
        "job_id": str(job_uuid),
        "count": len(rows),
        # {bucket: {label, count}} — the panel groups by this without having
        # to know the scraper's vocabulary.
        "by_reason": {k: {"label": labels.get(k, k), "count": v}
                      for k, v in sorted(counts.items(), key=lambda kv: -kv[1])},
        "items": [{
            "id": r.id,
            "divar_id": r.divar_id,
            "url": r.url,
            "title": r.title,
            "reason": r.reason,
            "reason_label": labels.get(r.reason, r.reason),
            "detail": r.detail,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        } for r in rows],
    }


@router.get("/jobs/{job_id}", response_model=ScrapingJobResponse)
async def get_scraping_job(
    job_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Get scraping job status"""
    
    # Try to parse as UUID first
    try:
        job_uuid = uuid.UUID(job_id)
        result = await db.execute(
            select(ScrapingJob).where(ScrapingJob.job_id == job_uuid)
        )
    except ValueError:
        # If not UUID, treat as internal id
        try:
            internal_id = int(job_id)
            result = await db.execute(
                select(ScrapingJob).where(ScrapingJob.id == internal_id)
            )
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid job identifier")
    
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    return ScrapingJobResponse(
        id=job.id,
        job_id=str(job.job_id),
        city_id=job.city_id,
        category_id=job.category_id,
        status=job.status,
        total_pages=job.total_pages,
        scraped_pages=job.scraped_pages,
        total_items=job.total_items,
        scraped_items=job.scraped_items,
        new_items=job.new_items,
        updated_items=job.updated_items,
        failed_items=job.failed_items,
        error_message=job.error_message,
        progress=job.progress,
        started_at=job.started_at,
        completed_at=job.completed_at,
        created_at=job.created_at
    )


# A job that has already stopped has nothing left to cancel. Everything else —
# including «paused», which is what waiting for an SMS-OTP code looks like —
# must be cancellable: that state can last minutes and the dashboard offers the
# stop button for it, so refusing anything but "running" left the user pressing
# a button that answered "Job is not running".
_FINISHED_JOB_STATUSES = {"completed", "cancelled", "failed"}
_STATUS_FA = {"completed": "تکمیل شده", "cancelled": "لغو شده", "failed": "ناموفق"}


@router.post("/jobs/{job_id}/cancel")
async def cancel_scraping_job(
    job_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Cancel a scraping job that has not finished yet."""
    result = await db.execute(
        select(ScrapingJob).where(ScrapingJob.job_id == job_id)
    )
    job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(status_code=404, detail="تسک یافت نشد")

    if job.status in _FINISHED_JOB_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"این تسک قبلاً تمام شده است ({_STATUS_FA.get(job.status, job.status)})")

    was = job.status
    job.status = "cancelled"
    job.completed_at = datetime.now()
    await db.commit()

    # If it was blocked on an SMS-OTP code, drop the request: the scraper wakes
    # out of its wait on the cancelled status, and the prompt in the dashboard
    # would otherwise sit there collecting a code nobody is listening for.
    from app.scraper import otp_store
    freed = otp_store.clear_job(job_id)

    # Remove from active tasks tracking
    # The scraper will check job status in the database and stop
    if job_id in active_tasks:
        del active_tasks[job_id]
    logger.info(f"Job {job_id} marked for cancellation (was {was}, otp cleared={freed})")

    return {"message": "Job cancelled successfully", "was": was, "otp_cleared": freed}


@router.get("/estimate")
async def estimate_matching_posts(
    city: str,
    category: Optional[str] = None,
    advertiser_type: Optional[str] = None,
    has_images: Optional[bool] = None,
    min_price: Optional[int] = None,
    max_price: Optional[int] = None,
    min_deposit: Optional[int] = None,
    max_deposit: Optional[int] = None,
    min_rent: Optional[int] = None,
    max_rent: Optional[int] = None,
    min_area: Optional[int] = None,
    max_area: Optional[int] = None,
    min_rooms: Optional[int] = None,
    max_rooms: Optional[int] = None,
    has_elevator: Optional[bool] = None,
    has_parking: Optional[bool] = None,
    has_storage: Optional[bool] = None,
    min_price_per_meter: Optional[int] = None,
    max_price_per_meter: Optional[int] = None,
):
    """How many ads Divar has for these filters, before scraping any of them.

    The same number Divar prints above its own results. Costs one request,
    where finding out by scraping costs opening every ad in the city.
    """
    from app.services import divar_count as dc

    form = dc.build_form_data(
        category,
        advertiser_type=advertiser_type, has_images=has_images,
        min_price=min_price, max_price=max_price,
        min_deposit=min_deposit, max_deposit=max_deposit,
        min_rent=min_rent, max_rent=max_rent,
        min_area=min_area, max_area=max_area,
    )
    count, error = await dc.fetch_post_count(city, form)
    # Filters Divar will not narrow on are still applied by the scraper after
    # it opens each ad, so the real yield is at most this number.
    ignored = dc.unsupported_filters(
        min_rooms=min_rooms, max_rooms=max_rooms,
        has_elevator=has_elevator, has_parking=has_parking, has_storage=has_storage,
        min_price_per_meter=min_price_per_meter, max_price_per_meter=max_price_per_meter,
    )
    return {"count": count, "error": error, "applied_by_divar": sorted(form),
            "applied_after_scrape": ignored}


@router.get("/cities")
async def get_available_cities():
    """Get list of available cities for scraping"""
    return [
        {"slug": slug, "name": info["name"], "province": info["province"]}
        for slug, info in CITIES.items()
    ]


@router.get("/categories")
async def get_available_categories():
    """Get list of available categories for scraping"""
    return [
        {"slug": slug, "name": info["name"], "type": info["type"]}
        for slug, info in CATEGORIES.items()
    ]


from pydantic import BaseModel

# ─── OTP passthrough endpoints ────────────────────────────────────────────────

@router.get("/otp-pending")
async def get_otp_pending():
    """Return jobs currently waiting for Divar SMS-OTP code.

    `timeout` and each entry's `remaining` come from the server so the
    dashboard's countdown cannot promise more time than the scraper will
    actually wait.
    """
    from app.scraper import otp_store
    return {"forwarders": await list_forwarders(), "pending": otp_store.get_pending(), "timeout": otp_store.wait_window()}


class OtpSubmitRequest(BaseModel):
    code: str


@router.post("/otp/{key:path}")
async def submit_otp_code(key: str, body: OtpSubmitRequest):
    """Submit SMS-OTP code that the browser is waiting for."""
    from app.scraper import otp_store
    ok = otp_store.submit(key, body.code.strip())
    if not ok:
        raise HTTPException(status_code=404, detail="No pending OTP request for this key")
    return {"success": True}


# ── automatic OTP intake from a phone-side SMS forwarder ───────────────────
#
# A phone with the Divar SIM runs a forwarder app. Every SMS from sender
# «Divar» is POSTed here within seconds, signed with a shared secret. The code
# is handed to the waiting extractor exactly as a hand-typed one would be —
# same otp_store.submit, same event — so the browser types it without anybody
# opening the panel. Manual entry keeps working; this is a faster path onto
# the same rail, not a replacement.

_PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_CODE_RE = re.compile(r"Code:\s*(\d{6})")
_ANY6_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")


def extract_otp_code(text: Optional[str]) -> Optional[str]:
    """Six digits after «Code:», Persian digits normalised first. Falls back
    to any standalone six-digit run — Divar's wording has moved before."""
    t = (text or "").translate(_PERSIAN_DIGITS)
    m = _CODE_RE.search(t) or _ANY6_RE.search(t)
    return m.group(1) if m else None


def detect_otp_kind(text: Optional[str]) -> Optional[str]:
    """«اطلاعات تماس» is the contact-info challenge; «کد تایید» (either
    spelling of the hamza) is a login code."""
    t = (text or "").replace("أ", "ا").replace("ٔ", "")
    if "اطلاعات تماس" in t:
        return "contact"
    if "کد تایید" in t or "کد تاييد" in t:
        return "login"
    return None


def _inbound_secret() -> str:
    return (getattr(settings, "otp_inbound_secret", "") or "").strip()


async def _verify_forwarder(request: Request, raw: bytes) -> None:
    """HMAC-SHA256 of the raw body in X-Signature, or the secret itself in
    X-OTP-Secret. Constant-time on both. 503 when nothing is configured —
    that is «feature off», not «bad credentials»."""
    import hashlib
    import hmac as _hmac
    secret = _inbound_secret()
    if not secret:
        raise HTTPException(status_code=503, detail="OTP_INBOUND_SECRET is not configured")
    sig = (request.headers.get("X-Signature") or "").strip().lower()
    if sig:
        want = _hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
        if _hmac.compare_digest(sig, want):
            return
    plain = request.headers.get("X-OTP-Secret") or ""
    if plain and _hmac.compare_digest(plain.encode("utf-8"), secret.encode("utf-8")):
        return
    raise HTTPException(status_code=401, detail="bad signature")


async def _forwarder_rate_limit(request: Request, limit: int = 20) -> None:
    """~20 requests a minute per source IP. A phone sends a handful an hour;
    anything faster is a misconfigured retry loop or somebody probing."""
    try:
        from app.database import get_redis
        r = await get_redis()
        ip = request.client.host if request.client else "?"
        key = f"otp_inbound:rl:{ip}"
        n = await r.incr(key)
        if n == 1:
            await r.expire(key, 60)
        if n > limit:
            raise HTTPException(status_code=429, detail="too many requests")
    except HTTPException:
        raise
    except Exception as e:
        # Redis down must not turn the forwarder off.
        logger.warning(f"[otp-inbound] rate limit unavailable: {e}")


class OtpInbound(BaseModel):
    kind: Optional[str] = None
    account: Optional[str] = None
    code: Optional[str] = None
    text: Optional[str] = None
    sim: Optional[str] = None
    sentStamp: Optional[int] = None
    receivedStamp: Optional[int] = None
    battery: Optional[int] = None
    network: Optional[str] = None


def _mask_code(code: Optional[str]) -> str:
    c = code or ""
    return ("*" * max(len(c) - 2, 0)) + c[-2:] if c else ""


@machine_router.post("/otp-inbound")
async def otp_inbound(request: Request):
    """A Divar SMS, forwarded from the phone that holds the SIM."""
    from app.scraper import otp_store
    from app.services import sms_log

    raw = await request.body()
    await _verify_forwarder(request, raw)
    await _forwarder_rate_limit(request)
    try:
        body = OtpInbound.model_validate_json(raw)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"bad body: {type(e).__name__}")

    now_ms = int(time.time() * 1000)
    # `code` is trusted only if it IS a code. A stock forwarder that does not
    # expand %Regex=…% sends the placeholder text itself, and handing that to
    # the browser would type «%Regex=Code:\s*(\d{6})%» into Divar's modal.
    _given = (body.code or "").translate(_PERSIAN_DIGITS).strip()
    code = _given if (_given.isdigit() and 4 <= len(_given) <= 8) else extract_otp_code(body.text)
    kind = body.kind if body.kind in ("contact", "login", "test") else detect_otp_kind(body.text)
    ip = request.client.host if request.client else "?"
    latency_ms = (now_ms - int(body.sentStamp)) if body.sentStamp else None

    matched = False
    matched_key = "no_pending"
    reason = None

    if kind == "test":
        reason = "test"
    elif not code:
        reason = "no_code_in_text"
    elif kind == "login":
        otp_store.put_login_code(body.account, code)
        reason = "parked_for_login"
    elif kind == "contact":
        hit = otp_store.find_pending_for_account(body.account)
        if not hit:
            reason = "no_pending_for_account"
        else:
            key, entry = hit
            # A late FIRST code must not overwrite a fresh resend: the
            # request's clock restarts on every resend, so a stamp older than
            # it belongs to an SMS the extractor already gave up on. 10s of
            # slack for the phone's clock.
            if body.sentStamp and (int(body.sentStamp) / 1000.0) < (entry["ts"] - 10):
                reason = "stale_code"
            elif otp_store.submit(key, code, sent_stamp_ms=body.sentStamp, source="forwarder"):
                matched, matched_key, reason = True, key, "matched"
            else:
                reason = "already_answered"
    else:
        reason = "unknown_kind"

    await sms_log.record(
        sms_log.INBOUND,
        (f"کد {kind or '?'} از {otp_store._digits(body.account) or '؟'} — "
         + ("به اسکرپر داده شد" if matched else f"استفاده نشد ({reason})")),
        level="info" if matched or kind in ("login", "test") else "warning",
        route="forwarder", actor=f"forwarder@{ip}",
        account=otp_store._digits(body.account) or None, kind=kind,
        code=_mask_code(code), sent_stamp=body.sentStamp, received_stamp=body.receivedStamp,
        server_ms=now_ms, matched_key=matched_key, reason=reason, latency_ms=latency_ms,
        sim=body.sim, battery=body.battery, network=body.network,
    )
    logger.info(f"[otp-inbound] kind={kind} account={otp_store._digits(body.account)} "
                f"{reason} latency_ms={latency_ms}")
    return {"matched": matched, "kind": kind, "reason": reason, "latency_ms": latency_ms}


class ForwarderHeartbeat(BaseModel):
    account: Optional[str] = None
    battery: Optional[int] = None
    network: Optional[str] = None
    version: Optional[str] = None


_HB_TTL = 900          # a phone that has not spoken in 15 min is forgotten
_HB_ONLINE = 600       # ...and reads as offline after 10


@machine_router.post("/forwarder-heartbeat")
async def forwarder_heartbeat(request: Request):
    from app.scraper import otp_store
    raw = await request.body()
    await _verify_forwarder(request, raw)
    await _forwarder_rate_limit(request)
    try:
        body = ForwarderHeartbeat.model_validate_json(raw)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"bad body: {type(e).__name__}")
    acct = otp_store._digits(body.account)
    if not acct:
        raise HTTPException(status_code=422, detail="account is required")
    try:
        from app.database import get_redis
        r = await get_redis()
        await r.set(f"forwarder:{acct}", json.dumps({
            "account": acct, "battery": body.battery, "network": body.network,
            "version": body.version, "last_seen": time.time(),
        }), ex=_HB_TTL)
    except Exception as e:
        logger.warning(f"[forwarder] heartbeat not stored: {e}")
        raise HTTPException(status_code=503, detail="store unavailable")
    return {"ok": True}


async def list_forwarders() -> dict:
    """{account: {online, battery, network, version, last_seen}}."""
    out = {}
    try:
        from app.database import get_redis
        r = await get_redis()
        now = time.time()
        async for k in r.scan_iter(match="forwarder:*"):
            v = await r.get(k)
            if not v:
                continue
            d = json.loads(v)
            d["online"] = (now - float(d.get("last_seen") or 0)) < _HB_ONLINE
            out[d.get("account")] = d
    except Exception as e:
        logger.debug(f"[forwarder] list unavailable: {e}")
    return out


@router.get("/forwarders")
async def get_forwarders():
    """Which phones are forwarding, and whether each is alive."""
    return {"forwarders": await list_forwarders(), "online_after_seconds": _HB_ONLINE}


@router.get("/login-code/{account}")
async def take_login_code(account: str):
    """A forwarded LOGIN code parked for this account, consumed on read, so
    the login form can fill itself in instead of the person re-typing what
    the phone already sent. None if nothing arrived in the last 3 minutes."""
    from app.scraper import otp_store
    return {"code": otp_store.take_login_code(account)}


@router.post("/otp/{key}/resend")
async def resend_otp_code(key: str):
    """Ask Divar to send the code again, for a prompt that is still open.

    The click happens in the parked browser — Divar's «ارسال مجدد» is on the
    page it is sitting on — so this only raises the flag; the wait loop acts
    on it within a couple of seconds and restarts the countdown.

    Capped: every press is a real SMS Divar sends on the account's behalf.
    """
    from app.scraper import otp_store
    result = otp_store.ask_resend(key)
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result.get("message"))
    return result


@router.post("/otp-cancel")
async def cancel_otp(key: Optional[str] = None, job_id: Optional[str] = None):
    """Dismiss a pending OTP prompt.

    A key clears that single prompt. Otherwise OTP is suppressed for the rest of
    the run so the scraper stops blocking on every phone that needs a code —
    scoped to one job when the caller can name it, because up to three scrapes
    run at once and dismissing one prompt should not silently stop the others
    collecting phone numbers.
    """
    from app.scraper import otp_store
    if key:
        otp_store.clear(key)
        return {"success": True, "cleared": 1}

    # The key carries the job («{job_id}:{divar_id}»), so a dismissal aimed at
    # one prompt can be scoped even when only the job is known.
    target = job_id or None
    cleared = otp_store.cancel_all(target)
    return {"success": True, "cleared": cleared,
            "suppressed": True, "scope": target or "all jobs"}


class SingleScrapeRequest(BaseModel):
    url: str

@router.post("/scrape-single")
async def scrape_single_property(
    request: SingleScrapeRequest,
    db: AsyncSession = Depends(get_db)
):
    """Scrape a single property by URL"""
    url = request.url
    
    if "divar.ir/v/" not in url:
        raise HTTPException(status_code=400, detail="Invalid Divar property URL")
    
    scraper = DivarScraper(
        db_session=db,
        proxy_enabled=settings.proxy_enabled,
        headless=settings.scraper_headless
    )
    
    try:
        await scraper.initialize()
        property_data = await scraper.scrape_property_detail(url)
        
        if property_data:
            saved = await scraper.save_property(property_data)
            if saved:
                return {"success": True, "property": saved.to_dict()}
        
        return {"success": False, "message": "Failed to scrape property"}
        
    finally:
        await scraper.close()


@router.get("/active-tasks")
async def get_active_tasks():
    """Get list of currently active scraping tasks"""
    return {
        "active_count": len(active_tasks),
        "task_ids": list(active_tasks.keys())
    }
