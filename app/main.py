"""
SorinFlow Divar Scraper - Main Application
FastAPI backend for Divar.ir property scraper
"""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from fastapi import FastAPI, Request
from app.auth.dependencies import require_super_admin as _require_super_admin
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from pathlib import Path
from loguru import logger
import sys

from app.config import get_settings
from app.database import init_db, close_db, close_redis
from app.api.routes import router as api_router

# Configure logging
logger.remove()
logger.add(
    sys.stdout,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    level="INFO"
)
logger.add(
    "/app/logs/scraper.log",
    rotation="10 MB",
    retention="7 days",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
    level="DEBUG"
)

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager"""
    logger.info("Starting SorinFlow Divar Scraper...")

    if settings.secret_key == "your-super-secret-key-change-in-production":
        logger.warning("SECRET_KEY is using the insecure default — set a strong value in .env")
    if not settings.api_key:
        logger.warning("API_KEY is not set — all API endpoints are unprotected")
    if "sorinflow_secret_2024" in settings.database_url:
        logger.warning("DATABASE_URL is using default credentials — change in .env for production")

    # Initialize database
    await init_db()
    logger.info("Database initialized")

    # Start reminder background checker
    reminder_task = asyncio.create_task(_reminder_checker())

    # Start nightly backup scheduler (local snapshot + Telegram offsite copy)
    from app.services.backup_service import backup_scheduler
    backup_task = asyncio.create_task(backup_scheduler())

    # Rented leads come back as fresh files when the lease year ends
    lease_task = asyncio.create_task(_lease_expiry_checker())

    yield

    # Cleanup
    reminder_task.cancel()
    backup_task.cancel()
    lease_task.cancel()
    logger.info("Shutting down...")
    await close_db()
    await close_redis()
    logger.info("Shutdown complete")


# Create FastAPI application
app = FastAPI(
    title="SorinFlow Divar Scraper",
    description="""
    🏠 **SorinFlow Divar Scraper API**
    
    A comprehensive web scraping system for Divar.ir, Iran's largest classified ads platform.
    
    ## Features
    
    * 🚀 **High Performance**: Async scraping with Playwright
    * 🛡️ **Anti-Detection**: Built-in stealth measures
    * 📱 **Phone Number Extraction**: Login-based scraping for contact info
    * 🖼️ **Image Processing**: Automatic download of property images
    * 📊 **Analytics Dashboard**: Real-time insights and statistics
    * 🐳 **Docker Ready**: One-command deployment
    
    ## Authentication
    
    Use the `/api/auth/login` endpoint to authenticate with Divar.ir using your phone number.
    After receiving the OTP code, verify with `/api/auth/verify`.
    
    ## Scraping
    
    Start scraping jobs via `/api/scraper/start` endpoint.
    Monitor progress with `/api/scraper/jobs/{job_id}`.
    """,
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan
)

# CORS middleware
_cors_origins = (
    [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    if settings.cors_origins != "*"
    else ["*"]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# API Key authentication middleware
@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    public_paths = {"/health", "/", "/favicon.svg", "/favicon.ico", "/api/public/stats", "/api/docs", "/api/redoc", "/api/openapi.json", "/api/info", "/api/config",
                    "/api/users/token", "/api/users/token/verify-totp", "/api/users/me"}
    is_dashboard = request.url.path.startswith("/dashboard") or request.url.path.startswith("/images")
    is_public = request.url.path in public_paths or is_dashboard

    is_preflight = request.method == "OPTIONS"
    has_bearer = request.headers.get("Authorization", "").startswith("Bearer ")
    if not is_public and not is_preflight and not has_bearer and settings.api_key:
        provided = (
            request.headers.get("X-API-Key")
            or request.query_params.get("api_key")
        )
        if provided != settings.api_key:
            return JSONResponse(status_code=401, content={"detail": "Invalid or missing API key"})

    return await call_next(request)


# Request logging + basic security headers
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = datetime.now()
    response = await call_next(request)
    process_time = (datetime.now() - start_time).total_seconds()
    logger.debug(f"{request.method} {request.url.path} - {response.status_code} - {process_time:.3f}s")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    _apply_panel_cache_policy(request, response)
    return response


# The panel's own markup and code must never be served from cache without
# checking first. Nothing set Cache-Control here, so browsers fell back to
# heuristic freshness and reused an app.js they already had: a deploy would go
# out green while the panel in front of the user kept running the previous
# build. index.html tried to cover that with a hand-written «?v=…» per asset,
# which only works while someone remembers to bump it — and it had gone stale.
#
# «no-cache» does not mean "do not cache", it means "revalidate before reuse".
# StaticFiles already sends an ETag, so the usual answer is a bodyless 304 and
# a deploy lands on the next reload. Images stay cacheable; the CDN's own
# versioned assets are untouched.
_REVALIDATE_SUFFIXES = (".html", ".js", ".css")


def _apply_panel_cache_policy(request: Request, response) -> None:
    path = request.url.path
    if not path.startswith("/dashboard"):
        return
    # /dashboard and /dashboard/ resolve to index.html, which has no suffix
    if path.rstrip("/").endswith("/dashboard") or path.endswith(_REVALIDATE_SUFFIXES):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    else:
        response.headers.setdefault("Cache-Control", "public, max-age=86400")


# Include API routes
app.include_router(api_router, prefix="/api")


# ─── Reminder background checker ─────────────────────────────────────────────
async def _reminder_checker():
    """Every 60 s: fire due reminders (send SMS if channel=sms, mark as sent)."""
    while True:
        try:
            await asyncio.sleep(60)
            await _fire_due_reminders()
            await _fire_due_event_sms()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Reminder checker error: {e}")


async def _fire_due_event_sms():
    """Text attendees whose appointment is coming up.

    Only appointments still ahead of us are texted: after downtime, a
    reminder for a visit that already happened is noise, so those are
    marked as handled without sending.
    """
    from app.database import async_session_maker
    from app.models.crm_models import CalendarEvent, SmsLog
    from app.services.sms_service import send_sms
    from sqlalchemy import select
    from datetime import timedelta

    async with async_session_maker() as session:
        now = datetime.now()
        # widest lead time any row can ask for, so the DB does the filtering
        horizon = now + timedelta(days=7)
        rows = (await session.execute(
            select(CalendarEvent).where(
                CalendarEvent.sms_reminder == True,      # noqa: E712
                CalendarEvent.sms_sent == False,         # noqa: E712
                CalendarEvent.status == "scheduled",
                CalendarEvent.start_at <= horizon,
            )
        )).scalars().all()

        fired = 0
        for event in rows:
            due_at = event.start_at - timedelta(minutes=event.remind_before or 0)
            if due_at > now:
                continue                      # not yet time
            event.sms_sent = True             # one attempt per appointment
            if event.start_at < now:
                logger.info(f"Skipped SMS for past event {event.id}")
                continue

            targets = event.sms_targets()     # مالک / مشتری / کارشناس فروش
            if not targets:
                logger.warning(f"Event {event.id} wants an SMS but has no phone")
                continue

            for role, _name, phone in targets:
                message = event.sms_text(role)
                res = await send_sms(phone, message)
                session.add(SmsLog(
                    to_number=phone, message=message,
                    status="sent" if res.get("success") else "failed",
                    provider=res.get("provider", "kavenegar"),
                    response=str(res.get("response", ""))[:2000],
                    contact_id=event.contact_id,
                ))
                fired += 1
                logger.info(f"Event {event.id} SMS → {role} {phone}: {res.get('success')}")

        if rows:
            await session.commit()
        if fired:
            logger.info(f"Sent {fired} appointment reminder(s)")


async def _fire_due_reminders():
    from app.database import async_session_maker
    from app.models.crm_models import Reminder
    from app.services.sms_service import send_sms
    from sqlalchemy import select
    from datetime import timedelta

    async with async_session_maker() as session:
        now = datetime.now()
        result = await session.execute(
            select(Reminder).where(
                Reminder.remind_at <= now,
                Reminder.is_sent == False,
            )
        )
        reminders = result.scalars().all()
        for reminder in reminders:
            if reminder.channel == "sms" and reminder.sms_to:
                res = await send_sms(reminder.sms_to, reminder.title)
                logger.info(f"Reminder SMS sent to {reminder.sms_to}: {res['success']}")
            reminder.is_sent = True
            # Reschedule repeating reminders
            if reminder.repeat == "daily":
                reminder.remind_at = reminder.remind_at + timedelta(days=1)
                reminder.is_sent = False
            elif reminder.repeat == "weekly":
                reminder.remind_at = reminder.remind_at + timedelta(weeks=1)
                reminder.is_sent = False
            elif reminder.repeat == "monthly":
                from dateutil.relativedelta import relativedelta
                try:
                    reminder.remind_at = reminder.remind_at + relativedelta(months=1)
                    reminder.is_sent = False
                except Exception:
                    pass
        if reminders:
            await session.commit()
            logger.info(f"Processed {len(reminders)} due reminder(s)")


# ─── Rented-lease expiry checker ─────────────────────────────────────────────
async def _lease_expiry_checker():
    """Every 6h: leads marked اجاره شده whose lease year is over go back to
    the fresh pool (status=new) so the file resurfaces automatically."""
    while True:
        try:
            await asyncio.sleep(6 * 3600)
            await _reactivate_expired_leases()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Lease expiry checker error: {e}")


async def _reactivate_expired_leases():
    from datetime import timedelta
    from app.database import async_session_maker
    from app.models.lead import Lead
    from sqlalchemy import select

    cutoff = datetime.now() - timedelta(days=365)
    async with async_session_maker() as session:
        rows = (await session.execute(
            select(Lead).where(Lead.status == "rented", Lead.rented_at <= cutoff)
        )).scalars().all()
        for lead in rows:
            lead.status = "new"
            lead.rented_at = None
            stamp = datetime.now().strftime("%Y-%m-%d")
            note = f"[{stamp}] پایان مدت اجاره — فایل به‌صورت خودکار به فایل‌های جدید برگشت."
            lead.notes = f"{lead.notes}\n{note}" if lead.notes else note
        if rows:
            await session.commit()
            logger.info(f"Lease expiry: {len(rows)} rented lead(s) returned to the fresh pool")


# Health check endpoint
@app.get("/health")
async def health_check():
    """Basic health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "version": settings.app_version
    }


# Manual backup trigger (super admin) — nightly run is automatic
@app.post("/api/backup/run")
async def run_backup_now(current_user=_require_super_admin):
    from app.services.backup_service import run_backup
    return await run_backup()


# Public landing-page stats (no auth; cached 60s in Redis)
@app.get("/api/public/stats")
async def public_stats():
    import json as _json
    from app.database import get_redis, async_session_maker
    from sqlalchemy import select, func
    from app.models.property import Property
    from app.models.lead import Lead
    from app.models.crm_models import DailyPerformance

    redis = await get_redis()
    cached = await redis.get("stats:public")
    if cached:
        return _json.loads(cached)

    async with async_session_maker() as db:
        total_properties = (await db.execute(
            select(func.count(Property.id)).where(Property.is_active == True)
        )).scalar() or 0
        with_phone = (await db.execute(
            select(func.count(Property.id)).where(
                Property.is_active == True, Property.phone_number.isnot(None))
        )).scalar() or 0
        total_leads = (await db.execute(select(func.count(Lead.id)))).scalar() or 0

        today = datetime.now().date()
        dpa_rows = (await db.execute(
            select(DailyPerformance).where(func.date(DailyPerformance.created_at) == today)
        )).scalars().all()
        dpa_top = max((d.scores()["total_score"] for d in dpa_rows), default=0)

    data = {
        "total_properties": total_properties,
        "total_leads": total_leads,
        "phone_rate": round(with_phone * 100 / total_properties) if total_properties else 0,
        "dpa_today_top": dpa_top,
    }
    await redis.set("stats:public", _json.dumps(data), ex=60)
    return data


# Root: public landing page (dashboard lives at /dashboard)
_landing_cache = {"mtime": 0.0, "html": ""}


@app.get("/", response_class=HTMLResponse)
async def root():
    """Serve the marketing landing page (cached; re-read only when the file changes)"""
    landing = Path("frontend/landing.html")
    if landing.exists():
        mtime = landing.stat().st_mtime
        if mtime != _landing_cache["mtime"]:
            _landing_cache["html"] = landing.read_text(encoding="utf-8")
            _landing_cache["mtime"] = mtime
        return HTMLResponse(
            _landing_cache["html"],
            headers={"Cache-Control": "public, max-age=300"},
        )
    return HTMLResponse('<meta http-equiv="refresh" content="0; url=/dashboard" />')


_FAVICON_HEADERS = {"Cache-Control": "public, max-age=86400"}


@app.get("/favicon.svg", include_in_schema=False)
async def favicon_svg():
    return FileResponse("frontend/favicon.svg", media_type="image/svg+xml", headers=_FAVICON_HEADERS)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon_ico():
    # Browsers that blindly request .ico get the SVG (all modern ones accept it)
    return FileResponse("frontend/favicon.svg", media_type="image/svg+xml", headers=_FAVICON_HEADERS)


# Error handlers
@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    return JSONResponse(
        status_code=404,
        content={"detail": "Resource not found"}
    )


@app.exception_handler(500)
async def internal_error_handler(request: Request, exc):
    logger.error(f"Internal error: {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"}
    )


# Mount static files for frontend
try:
    app.mount("/dashboard", StaticFiles(directory="frontend", html=True), name="frontend")
    Path(settings.images_path).mkdir(parents=True, exist_ok=True)
    app.mount("/images", StaticFiles(directory=settings.images_path), name="images")
except Exception:
    logger.warning("Frontend directory not found, skipping static file mount")


# Frontend config endpoint
@app.get("/api/config")
async def frontend_config():
    return {"api_key": settings.api_key}


# API information endpoint
@app.get("/api/info")
async def api_info():
    """Get API information"""
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "server_ip": settings.server_ip,
        "domain": settings.domain,
        "endpoints": {
            "docs": "/api/docs",
            "properties": "/api/properties",
            "scraper": "/api/scraper",
            "auth": "/api/auth",
            "stats": "/api/stats",
            "proxies": "/api/proxies"
        }
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.debug,
        workers=1,
    )
