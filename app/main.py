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
from html import escape as html_escape
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
        level="DEBUG"
    )
except Exception as _log_err:  # pragma: no cover - environment dependent
    logger.warning(f"file logging disabled ({_log_err})")

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager"""
    logger.info("Starting SorinFlow Divar Scraper...")

    if settings.secret_key == "your-super-secret-key-change-in-production":
        logger.warning("SECRET_KEY is using the insecure default — set a strong value in .env")
    if not settings.api_key:
        logger.warning("API_KEY is not set — all API endpoints are unprotected")
    if "CHANGE_ME" in settings.database_url:
        logger.warning("DATABASE_URL still holds the placeholder password — set it for real")
    if settings.super_admin_password == "CHANGE_ME":
        logger.warning("SUPER_ADMIN_PASSWORD is the placeholder — set it before first boot")

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


# ─── حالت تعمیر ──────────────────────────────────────────────────────────────
# Starlette runs the last-registered middleware first, so this one sits closest
# to the routes — which is all it needs: it answers before any handler does.
MAINTENANCE_PAGE = r"""<!doctype html><html lang="fa" dir="rtl"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>سورین‌فلو — در حال بروزرسانی</title>
<link rel="icon" type="image/svg+xml" href="/favicon.svg">
<!--
  Deliberately one file with no external request of any kind: no CDN script, no
  webfont, no Lottie player. This is the page that shows when something is
  already wrong, so every dependency it has is another thing that can be down at
  exactly the moment it is needed. The animation below is inline SVG and CSS for
  the same reason — it weighs nothing and cannot fail to load.
-->
<style>
:root{
  --bg:#0b1020; --card:#141a2e; --line:#26304d; --text:#e6e9f2;
  --muted:#8f9fb6; --brand:#4f8cff; --brand2:#a78bfa; --ok:#2fbf71;
}
@media (prefers-color-scheme: light){
  :root{--bg:#eef2f9;--card:#fff;--line:#d9e0ee;--text:#111827;--muted:#5b6880}
}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
 background:var(--bg);color:var(--text);padding:1.25rem;
 font-family:Vazirmatn,Tahoma,system-ui,-apple-system,sans-serif;line-height:1.9;
 background-image:radial-gradient(1100px 520px at 50% -12%,rgba(79,140,255,.13),transparent 70%)}
.card{width:100%;max-width:560px;background:var(--card);border:1px solid var(--line);
 border-radius:20px;padding:2.25rem 1.75rem;text-align:center;
 box-shadow:0 24px 70px rgba(0,0,0,.32)}
h1{margin:.25rem 0 .6rem;font-size:1.4rem;font-weight:800}
p{margin:.35rem 0;color:var(--muted);font-size:.94rem}

/* ── the animation ───────────────────────────────────────────────────────── */
.art{width:150px;height:150px;margin:0 auto .5rem;display:block}
.gear-a{transform-origin:62px 62px;animation:spin 8s linear infinite}
.gear-b{transform-origin:112px 104px;animation:spin-r 5.5s linear infinite}
.spark{transform-origin:center;animation:pulse 2.4s ease-in-out infinite}
@keyframes spin{to{transform:rotate(360deg)}}
@keyframes spin-r{to{transform:rotate(-360deg)}}
@keyframes pulse{0%,100%{opacity:.25;transform:scale(.9)}50%{opacity:1;transform:scale(1.06)}}

/* ── countdown ───────────────────────────────────────────────────────────── */
.count{display:flex;gap:.5rem;justify-content:center;margin:1.25rem 0 .35rem;direction:ltr}
.unit{min-width:66px;background:rgba(127,146,255,.10);border:1px solid var(--line);
 border-radius:14px;padding:.55rem .35rem}
.unit b{display:block;font-size:1.5rem;font-weight:800;font-variant-numeric:tabular-nums;
 line-height:1.25;color:var(--text)}
.unit span{display:block;font-size:.66rem;color:var(--muted)}
.bar{height:6px;border-radius:99px;background:rgba(127,146,255,.15);overflow:hidden;margin:.9rem 0 .2rem}
.bar>i{display:block;height:100%;width:0%;border-radius:99px;
 background:linear-gradient(90deg,var(--brand),var(--brand2));transition:width .6s ease}

.contact{margin-top:1.5rem;padding-top:1.15rem;border-top:1px solid var(--line)}
.contact .lead{font-size:.8rem;color:var(--muted);margin-bottom:.6rem}
.links{display:flex;gap:.5rem;justify-content:center;flex-wrap:wrap}
.links a{display:inline-flex;align-items:center;gap:.4rem;padding:.5rem .95rem;
 border:1px solid var(--line);border-radius:11px;color:var(--text);text-decoration:none;
 font-size:.85rem;transition:border-color .15s,transform .15s}
.links a:hover{border-color:var(--brand);transform:translateY(-1px)}
.links a b{direction:ltr;font-weight:600}
.foot{margin-top:1.5rem;font-size:.72rem;color:var(--muted)}

@media (prefers-reduced-motion: reduce){
  .gear-a,.gear-b,.spark{animation:none}
  .bar>i{transition:none}
}
</style></head><body>
<div class="card">
  <svg class="art" viewBox="0 0 176 176" fill="none" aria-hidden="true">
    <defs>
      <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stop-color="#4f8cff"/><stop offset="1" stop-color="#a78bfa"/>
      </linearGradient>
    </defs>
    <circle cx="88" cy="88" r="78" stroke="url(#g)" stroke-opacity=".16" stroke-width="2"/>
    <g class="gear-a" stroke="url(#g)" stroke-width="7" stroke-linecap="round">
      <circle cx="62" cy="62" r="23" fill="none"/>
      <path d="M62 30v-11M62 105v-11M30 62H19M105 62H94M40 40l-8-8M92 92l-8-8M84 40l8-8M32 92l8-8"/>
    </g>
    <g class="gear-b" stroke="url(#g)" stroke-width="6" stroke-linecap="round" stroke-opacity=".8">
      <circle cx="112" cy="104" r="16" fill="none"/>
      <path d="M112 80v-8M112 136v-8M88 104h-8M144 104h-8M95 87l-6-6M135 127l-6-6M129 87l6-6M89 127l6-6"/>
    </g>
    <g class="spark" fill="url(#g)">
      <circle cx="140" cy="46" r="4"/><circle cx="34" cy="132" r="3"/>
    </g>
  </svg>

  <h1>__MESSAGE__</h1>
  <p>در حال بهبود سامانه هستیم و به‌زودی برمی‌گردیم.</p>

  <div id="cd" style="display:none">
    <div class="count">
      <div class="unit"><b id="d">۰</b><span>روز</span></div>
      <div class="unit"><b id="h">۰</b><span>ساعت</span></div>
      <div class="unit"><b id="m">۰</b><span>دقیقه</span></div>
      <div class="unit"><b id="s">۰</b><span>ثانیه</span></div>
    </div>
    <div class="bar"><i id="pb"></i></div>
    <p id="eta" style="font-size:.78rem"></p>
  </div>

  <div class="contact" id="contact" style="display:none">
    <div class="lead">در موارد فوری با ما تماس بگیرید</div>
    <div class="links" id="links"></div>
  </div>

  <div class="foot">
    © <span id="yr">۱۴۰۴</span> املاک سورین — سورین‌فلو
  </div>
</div>

<script>
const FA = n => String(n).padStart(2,'0').replace(/[0-9]/g, d => '۰۱۲۳۴۵۶۷۸۹'[+d]);
document.getElementById('yr').textContent =
  new Intl.DateTimeFormat('fa-IR-u-ca-persian',{year:'numeric'}).format(new Date())
    .replace(/[^۰-۹]/g,'');

// Contact details are injected as JSON rather than interpolated into the markup,
// so a phone number or address containing markup cannot become part of the page.
const CFG = __CONFIG__;

if (CFG.phone || CFG.email) {
  const box = document.getElementById('links');
  const add = (href, label, value) => {
    const a = document.createElement('a');
    a.href = href;                       // href is built from the value, never from markup
    const t = document.createTextNode(label + ' ');
    const b = document.createElement('b');
    b.textContent = value;               // textContent: nothing here can inject
    a.append(t, b);
    box.appendChild(a);
  };
  if (CFG.phone) add('tel:' + CFG.phone.replace(/[^\d+]/g,''), '☎', CFG.phone);
  if (CFG.email) add('mailto:' + encodeURIComponent(CFG.email), '✉', CFG.email);
  document.getElementById('contact').style.display = '';
}

if (CFG.seconds_left && CFG.seconds_left > 0) {
  const cd = document.getElementById('cd');
  cd.style.display = '';
  const total = CFG.seconds_left;
  const end = Date.now() + total * 1000;
  const eta = document.getElementById('eta');
  try {
    eta.textContent = 'زمان تخمینی بازگشت: ' + new Intl.DateTimeFormat('fa-IR-u-ca-persian',
      {dateStyle:'full', timeStyle:'short'}).format(new Date(end));
  } catch (_) {}

  const tick = () => {
    let left = Math.max(Math.floor((end - Date.now())/1000), 0);
    document.getElementById('d').textContent = FA(Math.floor(left/86400));
    document.getElementById('h').textContent = FA(Math.floor(left%86400/3600));
    document.getElementById('m').textContent = FA(Math.floor(left%3600/60));
    document.getElementById('s').textContent = FA(left%60);
    document.getElementById('pb').style.width = (100 - (left/total)*100).toFixed(1) + '%';
    if (left <= 0) {
      clearInterval(timer);
      // The deadline passing does not reopen anything — only a person does. Say
      // "finishing up" rather than counting into negative numbers, and reload
      // periodically so the site returning is noticed without a manual refresh.
      eta.textContent = 'در حال نهایی‌سازی — به‌زودی باز می‌شویم';
      setTimeout(() => location.reload(), 60000);
    }
  };
  tick();
  const timer = setInterval(tick, 1000);
}
</script>
</body></html>"""


def render_maintenance_page(state=None, message: str = None) -> str:
    """Fill the closed-site template.

    The contact details go in as a JSON blob the script reads, not as markup:
    an operator-typed phone number or address is untrusted like anything else,
    and json.dumps escaping is what keeps it from becoming part of the page.
    """
    import json
    from app.services import maintenance as mt

    text = message or (state.message if state else mt.DEFAULT_MESSAGE)
    cfg = {
        "seconds_left": (state.seconds_left if state else None),
        "phone": (state.phone if state else None),
        "email": (state.email if state else None),
    }
    # </script> inside a JSON string would end the block early; the escape keeps
    # the payload inert wherever it lands.
    blob = json.dumps(cfg, ensure_ascii=False).replace("</", "<\\/")
    return (MAINTENANCE_PAGE
            .replace("__MESSAGE__", html_escape(text))
            .replace("__CONFIG__", blob))


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
