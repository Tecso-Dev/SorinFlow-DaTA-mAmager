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
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, Response
import hmac
import time
import uuid
from html import escape as html_escape
from pathlib import Path
from loguru import logger
import sys

from app.config import get_settings
from app.database import init_db, close_db, close_redis
from app.api.routes import router as api_router

# Configure logging
#
# Both sinks share the redaction filter. Container stdout is persisted to the
# node's disk by containerd and the file sink writes to a volume, so a Divar
# session cookie or a customer's phone number logged here is at rest on the
# server — the filter is what stops a new call site leaking one by forgetting
# to mask.
#
# diagnose=False matters as much as the filter: with it on, loguru prints local
# variable values inside a traceback, which is how a connection error turns into
# DATABASE_URL and its password appearing in the log.
from app.log_redaction import redact_filter

logger.remove()
logger.add(
    sys.stdout,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    level="INFO",
    filter=redact_filter,
    backtrace=False,
    diagnose=False,
)
# File logging is best-effort. The path used to be hardcoded to the container's
# /app/logs, so importing the app anywhere else — a test run, a shell, a
# read-only root filesystem — died at import time before a single line of the
# application ran. Stdout logging above is the one that must always work.
try:
    logger.add(
        str(Path(get_settings().logs_path) / "scraper.log"),
        rotation="10 MB",
        retention="7 days",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        level="INFO",
        filter=redact_filter,
        backtrace=False,
        diagnose=False,
        compression="gz",      # a 10MB text log compresses to well under 1MB
        enqueue=True,          # the scraper and the API both write to this file
    )
except Exception as _log_err:  # pragma: no cover - environment dependent
    logger.warning(f"file logging disabled ({_log_err})")

settings = get_settings()


async def _release_orphaned_jobs() -> None:
    """Close out scrapes this process was running when it last stopped.

    Runs once at startup, before anything can create a new job, so every
    running/paused row it finds necessarily belongs to a dead process.
    Failures here must not stop the app booting — a stale row is a cosmetic
    problem, a pod that will not start is not.
    """
    try:
        from sqlalchemy import update, or_
        from app.database import async_session_maker
        from app.models.scraping_job import ScrapingJob

        async with async_session_maker() as db:
            result = await db.execute(
                update(ScrapingJob)
                .where(or_(ScrapingJob.status == "running",
                           ScrapingJob.status == "paused"))
                .values(
                    status="failed",
                    completed_at=datetime.now(),
                    finish_reason=(
                        "سرور در میانهٔ اجرا ری‌استارت شد — این تسک ادامه پیدا "
                        "نکرد. آگهی‌های ذخیره‌شده سر جایشان هستند؛ برای بقیه "
                        "دوباره اجرا کنید"
                    ),
                )
            )
            await db.commit()
            if result.rowcount:
                logger.warning(
                    f"{result.rowcount} scraping job(s) were left running by a "
                    "previous process and have been marked failed")
    except Exception as e:
        logger.warning(f"Could not release orphaned scraping jobs: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager"""
    logger.info("Starting SorinFlow Divar Scraper...")

    # Startup warnings, in the order they matter. Each says what is actually at
    # risk — a warning that overstates the danger gets ignored, and then so do
    # the ones that do not.
    if settings.secret_key == "your-super-secret-key-change-in-production":
        logger.warning(
            "SECRET_KEY is the published default — anyone can forge a "
            "super-admin token. Set a strong value before serving traffic.")
    if "CHANGE_ME" in settings.database_url:
        logger.warning("DATABASE_URL still holds the placeholder password — set it for real")
    if not settings.api_key:
        # This used to read "all API endpoints are unprotected", which was
        # false and is the kind of false that trains people to skim warnings.
        # Every dashboard router sits behind require_permission(), so a request
        # without a valid JWT is refused whether or not this key is set. The
        # key is an outer gate in front of that, not the thing holding the door.
        logger.info(
            "API_KEY is not set — the supplemental API-key gate is off. "
            "Routes still require a JWT and their permission; set API_KEY to "
            "add the outer layer.")
    if not settings.metrics_token:
        logger.info("METRICS_TOKEN is not set — /metrics is disabled and answers 404")

    # Initialize database
    await init_db()
    logger.info("Database initialized")

    # A scrape lives in an asyncio task inside this process. When the process
    # goes — a deploy, a restart, the node rebooting — the task dies and the
    # row it was updating is left saying «running» forever, at whatever
    # percentage it had reached. It is indistinguishable on screen from a
    # scrape that is genuinely working, so the panel shows a job that will
    # never move and offers a stop button that stops nothing.
    #
    # Nothing can resume it: the browser, its Divar session and its place in
    # the feed are all gone. So say what happened and let it be re-run.
    await _release_orphaned_jobs()

    # Start reminder background checker
    reminder_task = asyncio.create_task(_reminder_checker())

    # Start nightly backup scheduler (local snapshot + Telegram offsite copy)
    from app.services.backup_service import backup_scheduler
    backup_task = asyncio.create_task(backup_scheduler())

    # Rented leads come back as fresh files when the lease year ends
    lease_task = asyncio.create_task(_lease_expiry_checker())

    # Google Cloud export. Returns immediately when disabled, which is the
    # shipped default — and when enabled on a host that cannot reach Google it
    # backs off rather than retrying every interval.
    from app.services.gcp import pipeline as gcp_pipeline
    if settings.gcp_enabled:
        logger.add(gcp_pipeline.sink, level="INFO", filter=redact_filter,
                   backtrace=False, diagnose=False)
    gcp_task = asyncio.create_task(gcp_pipeline.exporter_loop())

    yield

    # Cleanup
    reminder_task.cancel()
    backup_task.cancel()
    lease_task.cancel()
    gcp_task.cancel()
    from app.services.gcp import gcp_client as _gcp
    await _gcp.close()
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


# ─── حالت تعمیر ──────────────────────────────────────────────────────────────
# Starlette runs the last-registered middleware first, so this one sits closest
# to the routes — which is all it needs: it answers before any handler does.
def render_maintenance_page(state=None, message: str = None) -> str:
    """Fill the closed-site page.

    Contact details go in as a JSON blob the script reads, not as markup: an
    operator-typed phone number or address is untrusted like anything else, and
    json.dumps escaping is what keeps it from becoming part of the page.
    """
    import json
    from app import error_pages
    from app.services import maintenance as mt

    text = message or (state.message if state else mt.DEFAULT_MESSAGE)
    cfg = {
        "seconds_left": (state.seconds_left if state else None),
        "phone": (state.phone if state else None),
        "email": (state.email if state else None),
    }
    # </script> inside a JSON string would end the block early; escaping the
    # slash keeps the payload inert wherever it lands.
    blob = json.dumps(cfg, ensure_ascii=False).replace("</", "<\\/")
    return error_pages.render_maintenance(text, blob)


async def _maintenance_allows(request: Request) -> bool:
    """Whether this particular request gets through a closed site.

    Three ways in, and only three: a path that must never close, the bypass
    cookie, or a bearer token belonging to a super_admin (or root).
    """
    from app.services import maintenance as mt

    if mt.is_open_path(request.url.path) or request.method == "OPTIONS":
        return True

    from app.database import async_session_maker
    async with async_session_maker() as db:
        enabled, _message, bypass = await mt.get_state(db)
        if not enabled:
            return True
        if bypass and request.cookies.get(mt.BYPASS_COOKIE) == bypass:
            return True

        token = request.headers.get("Authorization", "")
        if token.startswith("Bearer "):
            try:
                from app.auth.jwt import decode_token, is_access_token
                from app.models.user import User
                from sqlalchemy import select
                payload = decode_token(token[7:])
                # A token issued before the TOTP step is not a login yet, so it
                # must not open a site that has been deliberately closed.
                username = payload.get("sub") if is_access_token(payload) else None
                if username:
                    user = (await db.execute(select(User).where(
                        User.username == username))).scalar_one_or_none()
                    if user and user.is_active and user.role in ("root", "super_admin"):
                        return True
            except Exception:
                pass
    return False


async def _maintenance_allows(request: Request) -> bool:
    """Whether this particular request gets through a closed site.

    Three ways in, and only three: a path that must never close, the bypass
    cookie, or a bearer token belonging to a super_admin (or root).
    """
    from app.services import maintenance as mt

    if mt.is_open_path(request.url.path) or request.method == "OPTIONS":
        return True

    from app.database import async_session_maker
    async with async_session_maker() as db:
        enabled, _message, bypass = await mt.get_state(db)
        if not enabled:
            return True
        if bypass and request.cookies.get(mt.BYPASS_COOKIE) == bypass:
            return True

        token = request.headers.get("Authorization", "")
        if token.startswith("Bearer "):
            try:
                from app.auth.jwt import decode_token, is_access_token
                from app.models.user import User
                from sqlalchemy import select
                payload = decode_token(token[7:])
                # A token issued before the TOTP step is not a login yet, so it
                # must not open a site that has been deliberately closed.
                username = payload.get("sub") if is_access_token(payload) else None
                if username:
                    user = (await db.execute(select(User).where(
                        User.username == username))).scalar_one_or_none()
                    if user and user.is_active and user.role in ("root", "super_admin"):
                        return True
            except Exception:
                pass
    return False


@app.middleware("http")
async def maintenance_middleware(request: Request, call_next):
    try:
        if await _maintenance_allows(request):
            return await call_next(request)
    except Exception as e:
        # Never let a fault here close a site that was not put into maintenance.
        logger.warning(f"[maintenance] check failed, letting the request through: {e}")
        return await call_next(request)

    from app.services import maintenance as mt
    from app.database import async_session_maker
    state = None
    message = mt.DEFAULT_MESSAGE
    try:
        async with async_session_maker() as db:
            state = await mt.get_state(db)
            message = state.message
    except Exception:
        pass

    # 503 so crawlers treat it as temporary; Retry-After keeps them off.
    # no-store matters more than it looks: a cached maintenance page would keep
    # showing after the site reopens, and the reopening is the part nobody would
    # think to debug.
    head = {"Retry-After": "3600", "Cache-Control": "no-store, must-revalidate"}
    if request.url.path.startswith("/api"):
        return JSONResponse(status_code=503,
                            content={"detail": message, "maintenance": True},
                            headers=head)
    return HTMLResponse(render_maintenance_page(state, message),
                        status_code=503, headers=head)


# API Key authentication middleware
@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    public_paths = {"/health", "/", "/favicon.svg", "/favicon.ico", "/api/public/stats", "/api/docs", "/api/redoc", "/api/openapi.json", "/api/info",
                    "/api/users/token", "/api/users/token/verify-totp", "/api/users/me",
                    # Visitor sign-up. Unauthenticated by nature, so the API key
                    # cannot gate it — each of these is rate-limited in
                    # app/services/verification.py instead, and the whole group
                    # 404s while PUBLIC_AUTH_ENABLED is off.
                    "/api/public/auth/register", "/api/public/auth/verify",
                    "/api/public/auth/resend", "/api/public/auth/login",
                    "/api/public/auth/status",
                    # حالت تعمیر: this middleware runs outside the maintenance
                    # one, so anything it rejects never reaches that logic at
                    # all — including the link meant to get back in. The POST
                    # to /api/maintenance is still super_admin-only by its own
                    # dependency; it is only exempt from the API-key check.
                    "/api/maintenance", "/maintenance-access"}
    is_dashboard = (request.url.path.startswith("/dashboard")
                    or request.url.path.startswith("/images")
                    or request.url.path == "/portal")
    is_public = request.url.path in public_paths or is_dashboard

    is_preflight = request.method == "OPTIONS"
    has_bearer = request.headers.get("Authorization", "").startswith("Bearer ")
    if not is_public and not is_preflight and not has_bearer and settings.api_key:
        provided = (
            request.headers.get("X-API-Key")
            or request.query_params.get("api_key")
        )
        if provided != settings.api_key:
            # A browser asking for a page it mistyped is not an authentication
            # problem — it is a missing page, and answering 401 JSON is how the
            # styled 404 became unreachable in production. Locally API_KEY is
            # empty so this branch never ran and the page looked fine.
            #
            # 404 rather than 401 on purpose: for a path outside /api there is
            # nothing here to authenticate against, and saying "not found"
            # reveals less than "you guessed a real path but lack the key".
            if not request.url.path.startswith("/api") and \
                    "text/html" in request.headers.get("accept", ""):
                from app import error_pages
                return HTMLResponse(error_pages.render_not_found(request.url.path),
                                    status_code=404)
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


# ─── metrics ────────────────────────────────────────────────────────────────
# Registered last, which in Starlette means it runs FIRST. That is deliberate:
# the API-key and maintenance middleware below reject requests without ever
# reaching a route, and those rejections — a credential-stuffing run, a closed
# site still being hammered — are the ones most worth counting. An inner
# placement would never see them.
@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    from app import metrics as mx

    if request.url.path == "/metrics":
        token = settings.metrics_token
        if not token:
            # not configured: behave as though the endpoint does not exist
            return JSONResponse(status_code=404, content={"detail": "Resource not found"})
        provided = (request.headers.get("X-Metrics-Token")
                    or request.query_params.get("token") or "")
        # compare_digest raises TypeError on non-ASCII str, and a token is
        # operator-supplied — encode both sides first.
        if not hmac.compare_digest(provided.encode("utf-8"), token.encode("utf-8")):
            return JSONResponse(status_code=401, content={"detail": "Invalid metrics token"})
        mx.sample_disk(settings.images_path)
        body, content_type = mx.render()
        return Response(content=body, media_type=content_type)

    route = mx.route_label(request.url.path)
    started = time.perf_counter()
    status = "500"          # an unhandled route error arrives here as a raised
    try:                    # exception, never as a response — so default to 500
        response = await call_next(request)
        status = str(response.status_code)
        return response
    except RuntimeError as exc:
        # Starlette raises this exact string from BaseHTTPMiddleware when the
        # downstream finished without sending anything AND without raising —
        # which happens when the browser hangs up mid-request (a reload, a
        # navigation, a closed tab). Nothing is wrong with the server and
        # nobody is listening any more, but it used to reach the 500 handler
        # and get logged as "Internal error", which is noise that teaches
        # people to skim past real errors.
        #
        # A genuine route failure cannot land here wearing this message: when
        # the app raises, Starlette re-raises *that* exception instead.
        if str(exc) != "No response returned.":
            raise
        status = "disconnected"
        logger.debug(f"[disconnect] client went away during {request.url.path}")
        return Response(status_code=499)      # nginx's code for it; unread
    finally:
        mx.http_latency.labels(route).observe(time.perf_counter() - started)
        mx.http_requests.labels(route, request.method, status).inc()


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


@app.get("/api/maintenance", tags=["Maintenance"])
async def maintenance_status(request: Request):
    """Whether the site is closed. Readable by anyone — the login page needs it."""
    from app.services import maintenance as mt
    from app.database import async_session_maker
    async with async_session_maker() as db:
        state = await mt.get_state(db, fresh=True)
    holds_bypass = bool(state.bypass and request.cookies.get(mt.BYPASS_COOKIE) == state.bypass)
    return {**state.to_dict(), "bypass_active": holds_bypass}


@app.post("/api/maintenance", tags=["Maintenance"])
async def set_maintenance(payload: dict, request: Request,
                          current_user=_require_super_admin):
    """Close or reopen the site. Super-admin only.

    Turning it on returns a bypass link. Opening that link in any browser marks
    it as allowed through — which is how a phone or a second machine gets in
    without signing in first.
    """
    from app.services import maintenance as mt
    from app.database import async_session_maker

    enabled = bool(payload.get("enabled"))
    message = payload.get("message")
    # hours is what the dashboard sends ("close it for 72 hours"); until is the
    # explicit form, kept for anyone driving this from a script
    hours = payload.get("hours")
    try:
        hours = float(hours) if hours not in (None, "") else None
    except (TypeError, ValueError):
        hours = None

    async with async_session_maker() as db:
        state = await mt.set_state(
            db, enabled=enabled, message=message,
            hours=hours, until=payload.get("until") or None,
            phone=payload.get("contact_phone"), email=payload.get("contact_email"),
            actor=getattr(current_user, "username", None))
    enabled, bypass = state.enabled, state.bypass

    base = str(request.base_url).rstrip("/")
    body = {**state.to_dict(),
            "bypass_url": f"{base}/maintenance-access?key={bypass}" if bypass else None}
    response = JSONResponse(body)
    if enabled and bypass:
        # the admin who threw the switch should not lock themselves out
        response.set_cookie(mt.BYPASS_COOKIE, bypass, max_age=30 * 24 * 3600,
                            httponly=True, samesite="lax")
    else:
        response.delete_cookie(mt.BYPASS_COOKIE)
    return response


@app.get("/maintenance-access", response_class=HTMLResponse, include_in_schema=False)
async def maintenance_access(key: str = ""):
    """Trade a valid bypass key for the cookie that gets a browser through."""
    from app.services import maintenance as mt
    from app.database import async_session_maker
    async with async_session_maker() as db:
        state = await mt.get_state(db, fresh=True)
    enabled, bypass = state.enabled, state.bypass

    if not enabled:
        return HTMLResponse('<meta http-equiv="refresh" content="0; url=/dashboard" />')
    if not key or not bypass or key != bypass:
        return HTMLResponse(
            render_maintenance_page(message="لینک دسترسی معتبر نیست"),
            status_code=403, headers={"Cache-Control": "no-store"})

    response = HTMLResponse('<meta http-equiv="refresh" content="0; url=/dashboard" />',
                            headers={"Cache-Control": "no-store"})
    response.set_cookie(mt.BYPASS_COOKIE, bypass, max_age=30 * 24 * 3600,
                        httponly=True, samesite="lax")
    return response


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


@app.get("/portal", response_class=HTMLResponse, include_in_schema=False)
async def portal_page():
    """The visitor-facing page. 404 while public auth is off, so a closed
    sign-up does not sit there half-alive for the public to poke at."""
    if not settings.public_auth_enabled:
        return HTMLResponse('<meta http-equiv="refresh" content="0; url=/dashboard" />')
    page = Path("frontend/portal.html")
    if page.exists():
        return HTMLResponse(page.read_text(encoding="utf-8"),
                            headers={"Cache-Control": "no-cache, must-revalidate"})
    return HTMLResponse("portal not found", status_code=404)


@app.get("/favicon.svg", include_in_schema=False)
async def favicon_svg():
    return FileResponse("frontend/favicon.svg", media_type="image/svg+xml", headers=_FAVICON_HEADERS)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon_ico():
    # Browsers that blindly request .ico get the SVG (all modern ones accept it)
    return FileResponse("frontend/favicon.svg", media_type="image/svg+xml", headers=_FAVICON_HEADERS)


# Error handlers
def _wants_html(request: Request) -> bool:
    """Whether this caller should get a page rather than JSON.

    An /api path always gets JSON — the dashboard and any script call those and
    would break on HTML. Everything else follows the Accept header, so a browser
    gets the page and curl still gets something parseable.
    """
    if request.url.path.startswith("/api"):
        return False
    return "text/html" in request.headers.get("accept", "")


@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    from app import error_pages
    if _wants_html(request):
        return HTMLResponse(error_pages.render_not_found(request.url.path),
                            status_code=404)
    return JSONResponse(status_code=404, content={"detail": "Resource not found"})


@app.exception_handler(500)
async def internal_error_handler(request: Request, exc):
    from app import error_pages
    # A short id, logged next to the traceback and shown on the page, so a
    # report of "the site broke" can be matched to a specific line.
    ref = uuid.uuid4().hex[:8]
    logger.error(f"[{ref}] Internal error on {request.url.path}: {exc}")
    if _wants_html(request):
        return HTMLResponse(error_pages.render_server_error(ref), status_code=500)
    return JSONResponse(status_code=500,
                        content={"detail": "Internal server error", "ref": ref})


# Mount static files for frontend
try:
    app.mount("/dashboard", StaticFiles(directory="frontend", html=True), name="frontend")
    Path(settings.images_path).mkdir(parents=True, exist_ok=True)
    app.mount("/images", StaticFiles(directory=settings.images_path), name="images")
except Exception:
    logger.warning("Frontend directory not found, skipping static file mount")


# The old /api/config returned {"api_key": ...} to anonymous callers, which
# handed out the very secret the api_key middleware exists to check. The panel
# never used it — it authenticates with a bearer token — so it is gone rather
# than protected.


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
