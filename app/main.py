"""
SorinFlow Divar Scraper - Main Application
FastAPI backend for Divar.ir property scraper
"""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
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

    yield

    # Cleanup
    reminder_task.cancel()
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
    public_paths = {"/health", "/", "/api/docs", "/api/redoc", "/api/openapi.json", "/api/info", "/api/config",
                    "/api/users/token", "/api/users/token/verify-totp", "/api/users/me",
                    "/api/users/register"}
    is_dashboard = request.url.path.startswith("/dashboard")
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


# Request logging middleware
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = datetime.now()
    response = await call_next(request)
    process_time = (datetime.now() - start_time).total_seconds()
    logger.debug(f"{request.method} {request.url.path} - {response.status_code} - {process_time:.3f}s")
    return response


# Include API routes
app.include_router(api_router, prefix="/api")


# ─── Reminder background checker ─────────────────────────────────────────────
async def _reminder_checker():
    """Every 60 s: fire due reminders (send SMS if channel=sms, mark as sent)."""
    while True:
        try:
            await asyncio.sleep(60)
            await _fire_due_reminders()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Reminder checker error: {e}")


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


# Health check endpoint
@app.get("/health")
async def health_check():
    """Basic health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "version": settings.app_version
    }


# Root redirect to dashboard
@app.get("/", response_class=HTMLResponse)
async def root():
    """Redirect to admin dashboard"""
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>SorinFlow Divar Scraper</title>
        <meta http-equiv="refresh" content="0; url=/dashboard" />
    </head>
    <body>
        <p>Redirecting to <a href="/dashboard">Dashboard</a>...</p>
    </body>
    </html>
    """


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
