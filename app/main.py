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
from starlette.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, Response
import hmac
import re
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
from app.log_redaction import redact_filter, request_id_var, inject_request_id

logger.remove()
# Every record, from every sink — including the GCP one added later in
# lifespan — carries the request id in scope right now ("-" outside a
# request, such as a background loop). See log_redaction.inject_request_id
# and the request_id_middleware below, which is what sets the var.
logger.configure(patcher=inject_request_id)
logger.add(
    sys.stdout,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{extra[request_id]}</cyan> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
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
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[request_id]} | {name}:{function}:{line} - {message}",
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
                        "نکرد. آگهی‌های ذخیره‌شده سر جایشان هستند؛ با دکمهٔ "
                        "«ادامه» از همان‌جا دنبال می‌شود"
                    ),
                )
            )
            await db.commit()
            if result.rowcount:
                logger.warning(
                    f"{result.rowcount} scraping job(s) were left running by a "
                    "previous process and have been marked failed")

                # Say it in the run log too, not only in finish_reason.
                #
                # The گزارش timeline is where anyone looks first when a run
                # stops, and a job killed by a deploy otherwise ends with its
                # last ordinary event — which reads as though the scraper gave
                # up on its own. It did not; the pod it was running in was
                # replaced. That has now happened twice, both times during an
                # unrelated deploy.
                try:
                    from app.services import job_log
                    from app.models.scraping_job import ScrapingJob as _SJ
                    from sqlalchemy import select as _select
                    rows = (await db.execute(
                        _select(_SJ.job_id).where(
                            _SJ.finish_reason.like("سرور در میانهٔ اجرا%"))
                        .order_by(_SJ.id.desc()).limit(result.rowcount)
                    )).scalars().all()
                    for jid in rows:
                        await job_log.record(
                            jid, job_log.ERROR,
                            "سرور در میانهٔ این اسکرپ ری‌استارت شد (استقرار نسخهٔ "
                            "جدید یا ری‌استارت سرویس) — تسک ادامه پیدا نکرد",
                            level="error")
                except Exception as e:
                    logger.warning(f"could not log the orphan reason: {e}")
    except Exception as e:
        logger.warning(f"Could not release orphaned scraping jobs: {e}")


# SECRET_KEY values printed in this repository: the default, the examples and
# the placeholders. Shorter ones are caught by the length check.
_PUBLISHED_SECRET_KEYS = {
    "your-super-secret-key-change-in-production",                     # config.py, docker-compose.yml
    "your-super-secret-key-change-in-production-with-random-string",  # .env.example
    "local-dev-only-secret-key-do-not-use-in-prod",                   # .env.local.example
    "replace-with-a-long-random-value",                               # README
    "ci-only-not-a-real-secret-0123456789",                           # deploy.yml, CLAUDE.md
    "0123456789abcdef0123456789abcdef",                               # the test suite's default
    "test-secret-key-0123456789abcdef",                               # tests/test_dr_roundtrip.py
    "ai-eval-harness-fake-secret-0123456789",                         # scripts/ai_eval.py
}


async def _users_table_empty() -> bool:
    """Whether _seed_super_admin is about to create the first account.

    False when the database cannot be asked: init_db fails on that next, and
    with the real reason rather than this one.
    """
    from sqlalchemy import inspect, text
    from app.database import engine, _guard
    try:
        async with engine.connect() as conn:
            await _guard(conn)
            if not await conn.run_sync(lambda c: inspect(c).has_table("users")):
                return True
            return (await conn.execute(text("SELECT 1 FROM users LIMIT 1"))).first() is None
    except Exception as e:
        logger.warning(f"could not tell whether the users table is empty: {e}")
        return False


async def _refuse_default_secrets() -> None:
    """Production does not start on a credential anyone can read in this repo.

    SECRET_KEY signs every token: a published or short one lets anybody mint a
    root token. The seed password is refused only when it is about to be used.
    The live Secret has no SUPER_ADMIN_PASSWORD and the live users table has
    rows, so refusing the placeholder outright would have taken the site down
    at its next deploy over a value it never reads.
    """
    if settings.environment != "production":
        return
    why = []
    key = settings.secret_key or ""
    if key in _PUBLISHED_SECRET_KEYS or len(key) < 32:
        why.append("SECRET_KEY is a published default or shorter than 32 characters, "
                   "so anyone could sign a root token")
    if settings.super_admin_password == "CHANGE_ME" and await _users_table_empty():
        why.append("SUPER_ADMIN_PASSWORD is unset and the users table is empty, so the "
                   "first account would be created with the published placeholder")
    if why:
        for reason in why:
            logger.critical(f"Refusing to start in production: {reason}")
        raise RuntimeError("Refusing to start in production: " + "; ".join(why))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager"""
    logger.info("Starting SorinFlow Divar Scraper...")

    # Before init_db, which would seed the placeholder password.
    await _refuse_default_secrets()

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

    # The audit trail (app/services/audit.py) is kept deliberately, not by
    # however long disk happens to last.
    audit_retention_task = asyncio.create_task(_audit_retention_checker())

    # Keep the panel's session state true. is_valid is only a belief until
    # somebody asks Divar, and for a day and a half nobody did.
    from app.services.divar_session import verifier_loop
    session_task = asyncio.create_task(verifier_loop())
    # The proxy pool is re-tested on a schedule for the same reason sessions
    # are: «working» is a belief, and one only corrected by a button is wrong
    # most of the time.
    from app.services.proxy_pool import refresh_loop as _proxy_refresh_loop
    proxy_task = asyncio.create_task(_proxy_refresh_loop())
    # A forwarder fails silently by nature: the phone reports success to
    # itself and the panel shows a prompt nobody answers. This tells the
    # device's owner, once per outage, before a run needs the code.
    from app.services.forwarder_watch import watch_loop as _fw_watch
    forwarder_task = asyncio.create_task(_fw_watch())

    # Keeps a copy of the forwarder APK on this site, because the phone that
    # needs it is the one that cannot reach GitHub's download host from Iran.
    from app.services.apk_mirror import mirror_loop as _apk_mirror
    apk_task = asyncio.create_task(_apk_mirror())

    # Saved scrapes fire at their hour, as their owner.
    from app.services.scrape_scheduler import scheduler_loop as _sched
    schedule_task = asyncio.create_task(_sched())

    # Every new listing is scored against the customers' criteria; the fits
    # land on the call queue and in the Telegram chat.
    from app.crm.match_engine import engine_loop as _match_loop
    match_task = asyncio.create_task(_match_loop())

    # A listing whose price came down is announced, and re-matched — it may
    # fit a budget it did not fit last week.
    from app.crm.price_watch import watch_loop as _price_loop
    price_task = asyncio.create_task(_price_loop())

    # One message a day to the same chat: what came in overnight, what fits
    # whom, what got cheaper, how many calls wait, whether the backup arrived.
    from app.crm.digest import digest_loop as _digest_loop
    digest_task = asyncio.create_task(_digest_loop())

    # The AI agents (app/ai/), each a background pass over the listings that
    # arrived since its last one, all through app/services/llm.py and all
    # gated on MATCH_ENGINE like the engine: what the ad's text says, the
    # text as a vector (semantic search, duplicates), what the photos show.
    from app.ai.listing_reader import reader_loop as _reader_loop
    reader_task = asyncio.create_task(_reader_loop())
    from app.ai.embeddings import embed_loop as _embed_loop
    embed_task = asyncio.create_task(_embed_loop())
    from app.ai.photo_tagger import photo_loop as _photo_loop
    photo_task = asyncio.create_task(_photo_loop())
    # «سورین»: the office asks its own database in Telegram — the backup's
    # bot, the backup's route, the backup's chats; read-only tools.
    from app.ai.assistant import assistant_loop as _assistant_loop
    assistant_task = asyncio.create_task(_assistant_loop())

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
    assistant_task.cancel()
    photo_task.cancel()
    embed_task.cancel()
    reader_task.cancel()
    digest_task.cancel()
    price_task.cancel()
    match_task.cancel()
    schedule_task.cancel()
    apk_task.cancel()
    reminder_task.cancel()
    backup_task.cancel()
    lease_task.cancel()
    audit_retention_task.cancel()
    session_task.cancel()
    proxy_task.cancel()
    forwarder_task.cancel()
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

# CORS. The panel, the portal and the landing page are served from this same
# origin and need none, and the forwarder app is not a browser — so by default
# no other origin may call across. Only origins CORS_ORIGINS names may, and
# "*" never with credentials: Starlette answers a credentialed request under
# "*" by echoing the caller's Origin, which lets any site read the response.
# Harmless while logins are bearer tokens; not once they are cookies.
def _cors_config(value: str):
    """(allowed origins, whether credentials may ride along) for CORS_ORIGINS."""
    origins = [o.strip() for o in (value or "").split(",") if o.strip()]
    if "*" in origins:
        return ["*"], False
    return origins, bool(origins)


_cors_origins, _cors_credentials = _cors_config(settings.cors_origins)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_credentials,
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


@app.middleware("http")
async def maintenance_middleware(request: Request, call_next):
    # Only the check is guarded. call_next used to sit inside the try as well,
    # so a route that raised was run a second time against a body already
    # consumed — the client waited forever and the log blamed maintenance.
    try:
        allowed = await _maintenance_allows(request)
    except Exception as e:
        # Never let a fault here close a site that was not put into maintenance.
        logger.warning(f"[maintenance] check failed, letting the request through: {e}")
        allowed = True
    if allowed:
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
    public_paths = {"/health", "/ready", "/", "/favicon.svg", "/favicon.ico", "/api/public/stats",
                    "/api/public/client-error", "/api/docs", "/api/redoc", "/api/openapi.json", "/api/info",
                    "/api/users/token", "/api/users/token/verify-totp", "/api/users/me",
                    # The rest of the login and recovery flow. Unauthenticated
                    # by nature — there is no token to send yet, which is the
                    # whole point — so the API key cannot gate them. Leaving
                    # them out 401s the panel in production while working
                    # locally, where API_KEY is empty: the toggle turns email
                    # 2FA ON (that call carries a bearer) and the endpoints
                    # needed to get back IN are the ones refused.
                    "/api/users/token/verify-email",
                    "/api/users/password-reset/request",
                    "/api/users/password-reset/confirm",
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
                    "/api/maintenance", "/maintenance-access",
                    # Fetched by the browser with no credentials of any kind,
                    # and by Kavenegar's own connection check. Left out, it
                    # 401s in production and works locally — the same way the
                    # login endpoints did.
                    "/kvn-push-sw.js",
                    # The phone-side SMS forwarder. Signs every POST with a
                    # shared secret (HMAC in X-Signature) and carries neither a
                    # bearer nor the API key — it is a phone, not the panel.
                    # Its own auth is inside the route; this only keeps the
                    # API-key gate from 401ing it in production the way it did
                    # the login endpoints.
                    "/api/scraper/otp-inbound", "/api/scraper/forwarder-heartbeat",
                    # What a crawler fetches before it fetches anything else,
                    # with no credential of any kind — and the same trap as the
                    # login endpoints: locally API_KEY is empty so these worked,
                    # while in production a search engine asking for the crawl
                    # rules got 401 JSON and therefore no rules at all.
                    "/robots.txt", "/sitemap.xml", "/llms.txt", "/og.png"}
    is_dashboard = (request.url.path.startswith("/dashboard")
                    or request.url.path.startswith("/images")
                    # the APK is fetched by a phone that has nothing to
                    # authenticate with yet — installing the app is step one
                    or request.url.path.startswith("/downloads")
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
    # robots.txt asks; this tells. A panel URL shared in a chat or landing in a
    # log a crawler reads is still not something to index.
    response.headers.setdefault("X-Robots-Tag", "noindex, nofollow")


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


# ─── request id ───────────────────────────────────────────────────────────────
# Registered after maintenance, the API key check and metrics (below them in
# this file), so it is outermost of the three — before any of them can log or
# respond, and wrapping their responses too when they short-circuit, not only
# the ones the router itself produces. Registered before gzip, so gzip stays
# the outermost layer of all (test_panel_delivery.py depends on that) — gzip
# neither logs nor answers early, so its order relative to this one does not
# matter for what this middleware exists to guarantee.
#
# No try/finally around call_next to reset the contextvar: an exception that
# escapes every handler below reaches Starlette's ServerErrorMiddleware —
# outside this middleware entirely — which is what calls internal_error_handler
# above. A finally here would already have reset the var by then, and the 500
# page would carry "-" instead of the request's own id. Each request runs its
# own asyncio task (uvicorn), so nothing else could read this value anyway.
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    incoming = request.headers.get("X-Request-ID", "")
    rid = incoming if _REQUEST_ID_RE.match(incoming) else uuid.uuid4().hex[:12]
    request_id_var.set(rid)
    response = await call_next(request)
    response.headers["X-Request-ID"] = rid
    return response


# ─── compression ─────────────────────────────────────────────────────────────
# Registered last, so it is outermost and compresses whatever the layers below
# produce. The panel was being served raw: 665 KB of app.js, 359 KB of markup
# and 170 KB of stylesheet, about 1.9 MB before anything appeared on screen —
# over a domestic Iranian line that is the wait people were complaining about.
# Text compresses five- to sixfold, so the same panel arrives in roughly 400 KB
# without a single line of it changing.
#
# Only bodies over a kilobyte: below that the header costs more than the saving.
# Images and fonts are already compressed formats and gzip leaves them alone.
app.add_middleware(GZipMiddleware, minimum_size=1024)


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


# ─── Audit-log retention ──────────────────────────────────────────────────
async def _audit_retention_checker():
    """Once a day: drop audit_events rows older than a year (app/services/audit.py)."""
    while True:
        try:
            await asyncio.sleep(24 * 3600)
            from app.services import audit as _audit
            n = await _audit.prune()
            if n:
                logger.info(f"Audit retention: dropped {n} event(s) older than a year")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Audit retention checker error: {e}")


# Health check endpoint
@app.get("/health")
async def health_check():
    """The process is up. Liveness and startup ask this — and only this: a
    database outage must not restart the app, which would not bring the
    database back and would drop every scrape in flight."""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "version": settings.app_version,
        "git_sha": settings.git_sha,
    }


@app.get("/ready")
async def readiness_check():
    """The process can serve. Readiness asks this: 503 while Postgres is
    unreachable, so Traefik answers 503 instead of the app answering 500 on
    every request (roadmap #13 — /health used to check nothing).

    Redis is still reported, but does not gate `ready`: a Redis outage
    degrades rate limiting and verification codes, which the routes that use
    them already handle (they fail open with a warning), not the whole API —
    taking every pod out of rotation over that is a bigger outage than the
    one being guarded against.
    """
    from sqlalchemy import text as _text
    from app.database import async_session_maker as _maker, get_redis as _redis
    checks = {}
    try:
        async with _maker() as s:
            await s.execute(_text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as e:
        checks["postgres"] = f"down: {type(e).__name__}"
    try:
        await (await _redis()).ping()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"down: {type(e).__name__}"
    ok = checks["postgres"] == "ok"
    return JSONResponse({"ready": ok, **checks}, status_code=200 if ok else 503)


# Errors from the user's browser (no auth: the phone that cannot even parse
# app.js has no token to send). Rate-limited per address inside; always 204.
@app.post("/api/public/client-error", status_code=204)
async def client_error(request: Request):
    from app.services import client_errors
    from app.services.verification import client_ip
    try:
        raw = await request.json()
    except Exception:
        return Response(status_code=204)
    if isinstance(raw, dict):
        await client_errors.record(raw, client_ip(request))
    return Response(status_code=204)


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


# ─── what crawlers are told ──────────────────────────────────────────────────
# /robots.txt and /sitemap.xml did not exist, so the API-key middleware answered
# both with 401 — a search engine asking for the crawl rules was turned away at
# the door, and with no rules to read, nothing said the panel was off limits.
#
# Answer engines get the same welcome as search engines, deliberately: being
# quoted in an answer is how an office in Urmia gets found now. What must not be
# crawled is the panel and the API, and that is said once, for everyone.
_PUBLIC_PATHS = ("/", "/portal")
_CLOSED_PATHS = ("/dashboard", "/api", "/images", "/downloads", "/maintenance-access")


def _site_root(request: Request) -> str:
    """The origin this request actually arrived on, so the links are right
    whichever hostname the site is reached by."""
    return f"{request.url.scheme}://{request.url.netloc}".rstrip("/")


@app.get("/robots.txt", include_in_schema=False)
async def robots_txt(request: Request):
    root = _site_root(request)
    lines = ["User-agent: *"]
    lines += [f"Disallow: {p}/" for p in _CLOSED_PATHS]
    lines += ["Allow: /$", "Allow: /portal", "", f"Sitemap: {root}/sitemap.xml", ""]
    return Response("\n".join(lines), media_type="text/plain",
                    headers={"Cache-Control": "public, max-age=86400"})


@app.get("/sitemap.xml", include_in_schema=False)
async def sitemap_xml(request: Request):
    root = _site_root(request)
    urls = "".join(
        f"<url><loc>{root}{'' if p == '/' else p}/</loc>"
        f"<changefreq>weekly</changefreq>"
        f"<priority>{'1.0' if p == '/' else '0.8'}</priority></url>"
        for p in _PUBLIC_PATHS)
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
           f'{urls}</urlset>')
    return Response(xml, media_type="application/xml",
                    headers={"Cache-Control": "public, max-age=86400"})


# The convention answer engines are converging on: the site in plain words, at a
# fixed address, so a model summarising it quotes the product rather than
# whatever it reconstructs from a page built out of animation and gradients.
_LLMS_TXT = """# SorinFlow — سورین‌فلو

> پلتفرم داده‌محور املاک: آگهی‌های دیوار را خودکار جمع می‌کند، هر آگهی را به یک
> لید تبدیل می‌کند، و کل مسیر تا قرارداد را برای دفتر املاک قابل پیگیری می‌کند.
> ساخت Tecso، فارسی و راست‌به‌چپ، با تقویم شمسی.

## چه می‌کند
- اسکرپر دیوار: جست‌وجوی خودکار با فیلتر قیمت، ودیعه و اجاره، متراژ، تعداد اتاق،
  امکانات و تاریخ انتشار شمسی؛ همراه با استخراج شمارهٔ تماس و تصاویر آگهی.
- CRM: هر آگهی یک لید، با وضعیت پیگیری، صف تماس روزانه، وظیفه، یادآور و معامله.
- پروفایل مشتری و DPA: ثبت نیاز مشتری با تحلیل بودجه (BANT) و ارزیابی روزانهٔ
  امتیازی عملکرد مشاوران.
- تطبیق و اطلاع‌رسانی: آگهی تازه با معیار مشتری سنجیده می‌شود و نتیجه از راه
  تلگرام و پیامک به مشاور می‌رسد.
- دستیار هوش مصنوعی «سورین» در تلگرام که از دیتابیس دفتر جواب می‌دهد.

## صفحه‌ها
- /            صفحهٔ معرفی محصول
- /portal      ثبت درخواست ملک برای مشتریان
- /dashboard   پنل مدیریت (نیازمند ورود، برای موتورها بسته است)

## تماس
ایمیل info@sorinflow.com · تلفن ۰۹۱۲۵۰۰۵۴۹۵ · تلگرام @sorinflow
"""


@app.get("/llms.txt", include_in_schema=False)
async def llms_txt():
    return Response(_LLMS_TXT, media_type="text/plain",
                    headers={"Cache-Control": "public, max-age=86400"})


@app.get("/og.png", include_in_schema=False)
async def og_image():
    """The picture that shows when the link is pasted in Telegram or WhatsApp,
    which is how this product is passed around. Served from the root because
    that is the address in the page's og:image."""
    return FileResponse("frontend/og.png", media_type="image/png",
                        headers={"Cache-Control": "public, max-age=604800"})


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


# GET and HEAD. FastAPI's @app.get registers GET alone, so a HEAD — which
# is what a checker reaching for "does this file exist" often sends — came
# back 405, on a file that serves perfectly over GET.
@app.api_route("/kvn-push-sw.js", methods=["GET", "HEAD"], include_in_schema=False)
async def kavenegar_push_service_worker():
    """Kavenegar's web-push service worker, served from the ORIGIN ROOT.

    A service worker can only control pages at or below its own path, so this
    one has to answer at /kvn-push-sw.js — mounting it under /dashboard would
    scope it to the panel and Kavenegar's «بررسی اتصال» would not find it.

    Service-Worker-Allowed is sent explicitly: without it a browser refuses any
    registration asking for a scope broader than the script's own directory,
    which is the failure people hit when the file is served correctly and the
    registration still will not take.
    """
    return FileResponse(
        "frontend/kvn-push-sw.js",
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/",
                 # The SDK it imports is versioned upstream; caching this
                 # one-line shim for a day is enough and keeps a stale worker
                 # from outliving a change here.
                 "Cache-Control": "public, max-age=86400"},
    )


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
    """This used to catch two different things under one name: a path no
    route matches, and a route's OWN `HTTPException(404, detail=…)` — the
    second lost its Persian detail to "Resource not found" or the HTML page.

    Starlette's Router puts the route it matched in scope["route"] before
    calling it (routing.py, Router.app) — for a FULL match, and for a
    Mount's path-prefix match too — and never sets it at all when nothing
    matched (Router.not_found raises this same HTTPException with the scope
    untouched). A Mount is the one matched "route" whose own 404 must still
    read as "nothing here": StaticFiles raises plain HTTPException(404) for
    a file it does not have under /dashboard, /images or /downloads, and
    that must keep today's page, not become FastAPI's bare {"detail": "Not
    Found"}. So: no route at all, or a Mount, is a real 404 page; anything
    else is a route's own answer, exactly as FastAPI would give it by
    default.
    """
    from starlette.routing import Mount
    from fastapi.exception_handlers import http_exception_handler
    from app import error_pages

    route = request.scope.get("route")
    if route is not None and not isinstance(route, Mount):
        return await http_exception_handler(request, exc)

    if _wants_html(request):
        return HTMLResponse(error_pages.render_not_found(request.url.path),
                            status_code=404)
    return JSONResponse(status_code=404, content={"detail": "Resource not found"})


@app.exception_handler(500)
async def internal_error_handler(request: Request, exc):
    from app import error_pages
    # The request id, not a fresh one: it is already on every log line this
    # request produced (the loguru patcher) and on the X-Request-ID response
    # header, so one id ties the error page to the traceback and everything
    # else this request logged.
    ref = request_id_var.get()
    # opt(exception=exc) logs the full traceback. diagnose stays False on
    # both sinks (see their logger.add calls above) — this adds the stack,
    # never local variable values, which is how DATABASE_URL leaked before.
    logger.opt(exception=exc).error(
        f"[{ref}] Internal error: {request.method} {request.url.path}")
    # Starlette's ServerErrorMiddleware — which is what calls this handler for
    # an exception nothing below caught — sends this response straight back
    # itself, outside every middleware including request_id_middleware. That
    # is the one path here the header would otherwise be missing from.
    if _wants_html(request):
        response = HTMLResponse(error_pages.render_server_error(ref), status_code=500)
    else:
        response = JSONResponse(status_code=500,
                                content={"detail": "Internal server error", "ref": ref})
    response.headers["X-Request-ID"] = ref
    return response


# Mount static files for frontend. Each mount on its own: one directory
# missing on a dev box must not silently take the others down with it.
try:
    app.mount("/dashboard", StaticFiles(directory="frontend", html=True), name="frontend")
except Exception:
    logger.warning("Frontend directory not found, skipping static file mount")
try:
    Path(settings.images_path).mkdir(parents=True, exist_ok=True)
    app.mount("/images", StaticFiles(directory=settings.images_path), name="images")
except Exception as e:
    logger.warning(f"images directory not mountable, skipping: {e}")
try:
    # The forwarder APK. Its own mount and its own media type: a browser on the
    # phone offered application/octet-stream may refuse to install it.
    import mimetypes
    mimetypes.add_type("application/vnd.android.package-archive", ".apk")
    # the PWA manifest is served from the dashboard mount; without this it
    # goes out as octet-stream and Chrome ignores it
    mimetypes.add_type("application/manifest+json", ".webmanifest")
    Path(settings.downloads_path).mkdir(parents=True, exist_ok=True)
    app.mount("/downloads", StaticFiles(directory=settings.downloads_path), name="downloads")
except Exception as e:
    logger.warning(f"downloads directory not mountable, skipping: {e}")


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
