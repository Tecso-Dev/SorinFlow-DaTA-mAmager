"""
SorinFlow Divar Scraper - Scraper API Routes
"""
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
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

    # A fresh job re-enables OTP prompts (a previous dismissal shouldn't
    # silently suppress phone extraction on the next run).
    from app.scraper import otp_store
    otp_store.reset_cancel()

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
    is_privileged = current_user and current_user.role in ("super_admin", "admin")
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
    return {"pending": otp_store.get_pending(), "timeout": otp_store.wait_window()}


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


@router.post("/otp-cancel")
async def cancel_otp(key: Optional[str] = None):
    """Dismiss a pending OTP prompt. With a key, clears that one; without,
    clears every pending OTP request (used by the 'close' button so a stale
    prompt from a dead job stops re-popping)."""
    from app.scraper import otp_store
    if key:
        otp_store.clear(key)
        return {"success": True, "cleared": 1}
    # No key = the "close" button: suppress OTP for the rest of the run so
    # the scraper stops blocking ~120s on every phone that needs a code.
    pending = otp_store.get_pending()
    otp_store.cancel_all()
    return {"success": True, "cleared": len(pending), "suppressed": True}


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
