"""
SorinFlow Divar Scraper - Main Scraper Module
Handles scraping property listings from Divar.ir
"""
import asyncio
import random
import re
import time
import uuid
from datetime import datetime, timedelta, date, timezone
from typing import Optional, Dict, List, Any
from pathlib import Path
from urllib.parse import urljoin
from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from bs4 import BeautifulSoup
import httpx
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from app.config import get_settings, CITIES, CATEGORIES
from app.models.property import Property, City, Category, allocate_serial_no
from app.models.scraping_job import ScrapingJob
from app.models.proxy import Proxy
from app.scraper.stealth import (StealthConfig, open_browser, apply_device, Device,
                                 close_context, context_alive)
from app.scraper.auth import DivarAuth
from app.scraper import divar_categories
from app.scraper.contact_extractor import ContactExtractor
from app.services import skipped_listings


def _mx_images(outcome: str, n: int = 1) -> None:
    """Count an image outcome. Wrapped because a metrics failure must never be
    the reason a scrape stops — this runs inside the per-listing loop."""
    try:
        from app import metrics as _mx
        _mx.scrape_images.labels(outcome).inc(n)
    except Exception:
        pass
from app.scraper.parsers import (
    extract_property_details as _parse_property_details,
    extract_price_info as _parse_price_info,
    detect_corner_type as _detect_corner,
    decide_advertiser_type as _decide_advertiser,
    agency_name_from_panel as _agency_name,
)

settings = get_settings()


class _DroppedConnection(Exception):
    """A save that failed because the database connection went away — the
    one save failure a retry can fix. Carries the original."""

    def __init__(self, cause: BaseException):
        super().__init__(str(cause))
        self.cause = cause


def _is_dropped_connection(e: BaseException) -> bool:
    """Is this the socket's fault rather than the statement's?

    InterfaceError is the driver reporting the connection gone underneath a
    statement. PendingRollbackError is the session still holding the
    transaction that died — the first error's aftermath, not a new one.
    OperationalError covers the server closing it (idle_in_transaction
    timeout, restart). DBAPIError flags the same thing generically.
    """
    from sqlalchemy.exc import (DBAPIError, InterfaceError, OperationalError,
                                PendingRollbackError)
    if isinstance(e, (InterfaceError, OperationalError, PendingRollbackError)):
        return True
    if isinstance(e, DBAPIError) and getattr(e, "connection_invalidated", False):
        return True
    name = type(e).__name__
    return name in ("ConnectionDoesNotExistError", "ConnectionResetError",
                    "InterfaceError", "ConnectionRefusedError")


# «The browser is not open on anybody's profile» — distinct from None, which
# is the shared anonymous profile a run with no number opens.
_NO_BROWSER = object()

# How long close() waits on one cleanup step before moving on to the next.
_CLOSE_STEP_TIMEOUT = 60.0

# The statuses a run moves its own row between. Anything else on the row was
# written from outside — «لغو», the queue's sweep — and a run that reads it
# stops, and never writes over it (DivarScraper._move_status).
_LIVE_STATUSES = ("running", "paused")
_STOPPED_STATUSES = ("cancelled", "failed", "completed", "partial")


# ── why a run could not start, in words the person who started it can act on ──
#
# A run whose browser did not come up used to end «مرورگر اسکرپر بالا نیامد:
# نامشخص» whenever nothing had set a technical error — which was every path
# that returned instead of raising: the only number's session refused, every
# number already open in other runs. Each reason below names the cause and
# what to do, and fits the finish line (finish_reason, 300 characters) with
# the «what to do» part always kept whole.

_FINISH_LINE_MAX = 300


def _say(cause: str, todo: str) -> str:
    """«cause — todo», cut to the finish line from the cause's end."""
    tail = f" — {todo}"
    room = _FINISH_LINE_MAX - len(tail)
    if len(cause) > room:
        cause = cause[:room - 1].rstrip() + "…"
    return cause + tail


def _numbers_fa(phones, limit: int = 2) -> str:
    phones = [p for p in phones if p]
    more = len(phones) - limit
    return "، ".join(phones[:limit]) + (f" و {more} شمارهٔ دیگر" if more > 0 else "")


_TRY_AGAIN = "چند دقیقه بعد «ادامه» را بزنید؛ اگر تکرار شد به مدیر سامانه خبر دهید"
_TELL_ADMIN = "به مدیر سامانه خبر دهید؛ «ادامه» تا رفع آن همین خطا را می‌دهد"

# (substrings of the error, the cause in words, what to do), first match wins.
# The sandbox markers come first: Chromium refusing its sandbox surfaces as a
# browser that closed, with the reason in the browser's output below it.
_BROWSER_FAILURES = (
    (("No usable sandbox", "SUID sandbox", "Failed to move to new namespace",
      "without --no-sandbox"),
     "Chromium با تنظیمات امنیتی (sandbox) این سرور اجرا نشد",
     "به مدیر سامانه خبر دهید (تنظیم CHROMIUM_SANDBOX)"),
    (("Executable doesn't exist", "executable doesn't exist"),
     "Chromium روی سرور پیدا نشد (نصب ناقص)", _TELL_ADMIN),
    (("No space left", "ENOSPC"), "فضای دیسک سرور پر است", _TELL_ADMIN),
    (("Permission denied", "EACCES"),
     "مرورگر اجازهٔ نوشتن در پوشهٔ پروفایل‌های روی سرور را ندارد", _TELL_ADMIN),
    (("Cannot allocate memory", "ENOMEM", "out of memory", "Out of memory"),
     "حافظهٔ سرور برای یک مرورگر دیگر کافی نبود",
     "وقتی اسکرپ‌های دیگر تمام شدند «ادامه» را بزنید"),
    (("Browser closed", "browser has been closed", "Target closed",
      "Target page, context or browser has been closed", "crashed", "SIGKILL", "SIGSEGV"),
     "مرورگر بلافاصله بعد از باز شدن بسته شد؛ معمولاً از کمبود حافظهٔ سرور است", _TRY_AGAIN),
    (("Timeout", "TimeoutError", "timed out"),
     "Chromium در زمان مجاز آماده نشد؛ سرور زیر بار است", _TRY_AGAIN),
)


def browser_failure_reason(err) -> str:
    """A browser that would not start — an exception or its text — in words:
    the cause, what to do, and the technical text for whoever is told."""
    if isinstance(err, BaseException):
        text = str(err).strip()
        tech = f"{type(err).__name__}: {text}" if text else type(err).__name__
    else:
        text = tech = str(err or "").strip()
    probe = f"{tech} {text}"
    first_line = tech.splitlines()[0][:90] if tech else ""
    for markers, cause, todo in _BROWSER_FAILURES:
        if any(m in probe for m in markers):
            return _say(f"مرورگر اسکرپر بالا نیامد: {cause} (خطای فنی: {first_line})", todo)
    if first_line:
        return _say(f"مرورگر اسکرپر بالا نیامد (خطای فنی: {first_line})",
                    "چند دقیقه بعد «ادامه» را بزنید؛ اگر تکرار شد همین متن را به مدیر سامانه بدهید")
    return _say("مرورگر اسکرپر بالا نیامد و Playwright متنی برای خطا نداد",
                "جزئیات در لاگ «scraper» در نمایشگر لاگ پنل است؛ " + _TRY_AGAIN)


def start_failure_reason(scraper) -> str:
    """Why `scraper` did not start, for its run's finish line. initialize()
    leaves the reason on every path that returns False; the technical text is
    only the fallback for one that forgets."""
    return (getattr(scraper, "_init_reason", None)
            or browser_failure_reason(getattr(scraper, "_init_error", "") or ""))


def crash_reason(err: BaseException) -> str:
    """A run that died of something nothing above expected, in words."""
    text = str(err).strip()
    tech = (f"{type(err).__name__}: {text}" if text else type(err).__name__).splitlines()[0][:120]
    return _say(f"اسکرپ با خطای فنی متوقف شد ({tech})",
                "آگهی‌های ذخیره‌شده سر جایشان هستند؛ «ادامه» را بزنید و اگر تکرار شد "
                "همین متن را به مدیر سامانه بدهید")


def _no_session_reason(rejected, closed, busy, broken) -> str:
    """No number of the run's opened into a working session: which failed
    how, and what to do about the one that matters most."""
    parts = []
    if rejected:
        parts.append(f"نشست {_numbers_fa(rejected)} باز نشد (دیوار آن را نپذیرفت یا منقضی شده)")
    if closed:
        parts.append(f"مرورگر هنگام باز کردن نشست {_numbers_fa(closed)} بسته شد")
    if busy:
        parts.append(f"{_numbers_fa(busy)} همین حالا در اسکرپ دیگری باز است")
    if broken:
        parts.append(f"{_numbers_fa(broken)} با خطای فنی باز نشد")
    one = len(rejected) + len(closed) + len(busy) + len(broken) == 1
    head = ("اسکرپ شروع نشد: " if one else
            "اسکرپ شروع نشد، چون هیچ‌کدام از شماره‌های دیوار شما باز نشد: ")
    if one:
        parts[-1] += " و شمارهٔ روشن دیگری به نام شما نیست"
    if rejected:
        todo = "در «احراز هویت دیوار» دوباره وارد شوید، سپس «ادامه» را بزنید"
    elif busy and not (closed or broken):
        todo = "بعد از پایان آن اسکرپ «ادامه» را بزنید"
    else:
        todo = _TRY_AGAIN
    return _say(head + "؛ ".join(parts), todo)


class DivarScraper:
    """Main scraper class for Divar.ir real estate listings"""

    BASE_URL = "https://divar.ir"
    # How many listings the browser-scroll phase may gather before the
    # cheaper API pagination takes over.
    DOM_COLLECT_CAP = 200
    # The most candidates one run walks — the ceiling collect_target has
    # always had, and the one a pool topped up mid-run stops at too (#30).
    POOL_CEILING = 1500

    def __init__(
        self,
        db_session: AsyncSession,
        proxy_enabled: bool = False,
        headless: bool = True
    ):
        self.db_session = db_session
        self.proxy_enabled = proxy_enabled
        self.headless = headless
        self.stealth_config = StealthConfig()
        self.auth = DivarAuth(db_session)
        
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.playwright = None
        
        self.images_dir = Path(settings.images_path)
        self.images_dir.mkdir(parents=True, exist_ok=True)

        self.active_phone: Optional[str] = None  # Divar account used for this session
        # Cookie rotation: cycle through saved Divar accounts mid-scrape so a
        # single number isn't hammered (which is what triggers the SMS checks)
        self._rotation_pool: List[str] = []
        # Counts contact-info reveals, not listings. Divar's SMS challenge is
        # triggered by asking for a phone number, and pre_contact_skip means most
        # listings never do that — so counting listings measured an event Divar
        # does not, and the setting could never hold the codes off.
        self._reveals_since_rotation = 0
        # For the finish line's «هر N افشا یک چالش» — the number the pacing is
        # tuned against. Per run, never reset by rotation.
        self._reveals_this_run = 0
        self._challenges_this_run = 0
        # Set when Divar demands a code: it is the account itself saying it is
        # spent, which beats any counter, so the next opportunity rotates.
        self._force_rotate = False
        self._rotate_every_override: Optional[int] = None

        self.current_job: Optional[ScrapingJob] = None
        self.request_count = 0
        self.session_start = datetime.now()
        # Captured /postlist/w/search POST request, replayed for cursor pagination
        self._search_req_template: Optional[Dict[str, Any]] = None
        # The pagination cursor Divar hands us in its own search responses.
        #
        # The DOM phase has always intercepted those responses and thrown the
        # cursor away, while the API phase sat waiting for a cursor it could
        # only obtain by first succeeding — which it could not do without one.
        # A deadlock that cost every run its depth.
        self._dom_cursor: Optional[int] = None
        # (reason, detail) for why listing collection ended. None until a
        # collection runs. The caller uses it to decide whether a short run is a
        # finished one or a blocked one — before this existed the two were
        # indistinguishable and every short run reported success.
        self._collect_stop: Optional[tuple] = None
        # Divar pushing back, and how hard we back off in response.
        #
        # There was no backoff at all: a 429 changed nothing about the pace, so
        # the scraper kept knocking at exactly the rate that had just been
        # refused until the session died. Slowing down when we are asked to is
        # both what keeps the account alive and what we owe someone else's
        # servers.
        self._refusals = 0
        self._cooldown_until = 0.0
        # A listing without a phone number is not a result. Set per run from
        # the job's own setting; defaults on because the number is the reason
        # the run exists.
        self._phone_required = True
        # The running job's UUID as a string. _recycle_browser needs it to write
        # an event, and reading it off self.current_job means an ORM attribute
        # access — which, on a row expired by an earlier commit, is a lazy
        # refresh in the middle of tearing a browser down.
        self._job_id_str = None
        # The run's filters, in the shape a divar.ir URL wants. Set when a job
        # starts; the collector appends them so Divar narrows the feed itself
        # instead of us reading an unfiltered one and discarding most of it.
        self._search_query = ""
        # One pooled HTTP client for the whole run. Each call site used to build
        # its own, so every image and every API request paid a fresh TCP and TLS
        # handshake to a host we talk to thousands of times per job.
        self._http: Optional[httpx.AsyncClient] = None
    
    async def _open_browser_for(self, account: Optional[str], proxy=None) -> None:
        """Open Chromium presenting as `account`'s device, and point auth at it.

        The one place a browser is opened in the scraper. initialize() and
        _recycle_browser() both call it, so the fresh browser after a recycle
        is the same device as the one before it — and auth always holds the
        browser that actually exists.
        """
        # Release whatever profile is open first: one Chromium per
        # user_data_dir, and rotation calls this with a different account
        # while the previous one is still held.
        _old = getattr(self, "context", None)
        if _old is not None:
            try:
                await close_context(_old)
            except Exception as e:
                logger.warning(f"[browser] closing the previous context failed: {e}")
            self.browser = self.context = self.page = None
        # From here until the new profile is open, the browser is nobody's.
        # If the open below raises — the profile is held by another run — a
        # rotation must see that and put the run's own profile back, not
        # read a stale «still on the old account» and leave it with none.
        self._browser_account = _NO_BROWSER

        self.browser, self.context, self.page, self.device = await open_browser(
            self.playwright, headless=self.headless, proxy=proxy,
            account=account, stealth_config=self.stealth_config)
        self.auth.browser = self.browser
        self.auth.context = self.context
        self.auth.page = self.page
        # Whose profile the browser is actually in — which is not always
        # active_phone: rotation opens a candidate's before it knows the
        # candidate's session works.
        self._browser_account = account
        if not account and self.context is not None:
            # No account: the shared «_anonymous» profile. It is on the data
            # volume and older code left real sessions in it — so a run with
            # no number of its own browsed, and revealed, as whoever had last
            # logged in there. Nobody's session, then: an empty jar.
            try:
                await self.context.clear_cookies()
            except Exception as e:
                logger.warning(f"[browser] could not empty the anonymous profile: {e}")

    async def initialize(self, restore_session: bool = True, phone_number: Optional[str] = None) -> bool:
        """Initialize scraper with browser and optional session restoration.

        False when the run cannot start, and then `_init_reason` says why and
        what to do, in the words run_scraping_job puts on the finish line."""
        self._init_reason: Optional[str] = None
        self._init_logged = False
        pool: List[str] = []
        try:
            self.playwright = await async_playwright().start()

            proxy = None  # chosen per account, below, once the account is known

            # The browser is opened AFTER the account is chosen (below), because
            # the device it presents as is derived from the account. Opening it
            # here with no account and re-presenting later would show Divar one
            # laptop on the first request and another on the second.
            self.browser = self.context = self.page = None

            # Restore authentication session
            if restore_session:
                owner = getattr(self, "owner_user_id", None)
                # A number named for the run has to be one the run may use:
                # the owner's own, switched on. _launch_job refuses anything
                # else at the door; this is the same rule where the browser
                # actually opens, so a caller that skips the route (a resume
                # of an old config, a schedule saved before a number was
                # switched off) cannot carry somebody else's session in.
                if phone_number and not await self._account_usable(phone_number):
                    logger.warning(
                        f"[rotate] {phone_number} is not usable by this run "
                        f"(owner {owner or '—'}) — choosing from the owner's own pool")
                    await self._log_run(
                        f"شمارهٔ {phone_number} برای این اجرا قابل استفاده نیست "
                        "(متعلق به شما نیست یا خاموش است) — از شماره‌های خودتان انتخاب می‌شود",
                        level="warning", phone=phone_number)
                    phone_number = None
                # DIVAR_PHONE_NUMBER is a single-operator install's default,
                # and it names ONE person's number. Applied to every run it was
                # how a colleague's scrape logged the root account's number in
                # and spent its reveals: the owner's pool was never consulted.
                # Only an ownerless, internally started run may fall back to it.
                if not phone_number and not owner:
                    phone_number = settings.divar_phone_number or None
                # Otherwise the least-spent number the owner has switched on —
                # and that no other run has open right now.
                if not phone_number and self.db_session:
                    try:
                        # Least-spent first, oldest-used to break the tie.
                        #
                        # This used to take the most recently *updated* row, and
                        # saving a session on rotation bumps updated_at — so the
                        # account that had just been used was always the one
                        # picked next, and with up to three jobs at once they
                        # all landed on the same number. One account absorbed
                        # every reveal while the others sat idle, which is what
                        # the constant SMS was.
                        # The same ordering and the same rest rule rotation
                        # uses, so the first account of a run is chosen the
                        # way every later one is.
                        pool = await self._load_rotation_pool()
                        if pool:
                            phone_number = await self._pick_free_account(pool)
                            if phone_number is None:
                                return await self._init_failed(self._all_numbers_busy(pool))
                            logger.info(f"Auto-selected Divar session for {phone_number}")
                    except Exception as _e:
                        logger.warning(f"Could not auto-select session: {_e}")

                # Now the account is known, so the device is — and the proxy,
                # which is sticky per account. Open the browser.
                if self.proxy_enabled:
                    proxy = await self._get_working_proxy(phone_number)
                    if proxy is None:
                        logger.warning("[proxy] PROXY_ENABLED but no proxy reaches Divar — going direct")
                await self._wait_for_released_profile(phone_number)
                try:
                    await self._open_browser_for(phone_number, proxy)
                except RuntimeError as e:
                    # Picked as free, then opened by another run of the same
                    # owner in the moment between the look and this open: the
                    # next free number of the pool, not a failed run.
                    if not (phone_number in pool and "already open" in str(e)):
                        raise
                    phone_number = await self._open_first_free(pool, skip={phone_number})
                    if phone_number is None:
                        return await self._init_failed(self._all_numbers_busy(pool))

                if phone_number:
                    from app.scraper.stealth import profile_in_use
                    restored = await self.auth.restore_session(phone_number)
                    if not restored:
                        logger.warning(f"Session not restored for {phone_number}. Trying other saved sessions...")
                        # Fall back to the owner's OTHER numbers, in the order
                        # rotation would reach for them.
                        #
                        # This used to take «the most recently updated valid
                        # session» from the whole table — anybody's — so a run
                        # whose own number had expired carried on, silently, on
                        # a colleague's. Every candidate now comes from the same
                        # owner-scoped, switched-on pool as rotation.
                        failed = phone_number
                        phone_number = None
                        # How each number failed, for the one sentence that
                        # tells the owner what to do: refused by Divar, a
                        # browser that closed under it, open in another run.
                        rejected: List[str] = []
                        closed: List[str] = []
                        busy: List[str] = []
                        broken: List[str] = []
                        (rejected if context_alive(self.context) else closed).append(failed)
                        candidates = []
                        if self.db_session:
                            try:
                                candidates = [p for p in await self._load_rotation_pool()
                                              if p != failed]
                            except Exception as _e:
                                logger.warning(f"Could not load fallback sessions: {_e}")
                        for cand in candidates:
                            if await profile_in_use(cand):
                                busy.append(cand)          # another run has it open
                                continue
                            logger.info(f"Falling back to session for {cand}")
                            try:
                                # A different person, so a different laptop.
                                if self.proxy_enabled:
                                    proxy = await self._get_working_proxy(cand)
                                await self._open_browser_for(cand, proxy)
                                if await self.auth.restore_session(cand):
                                    phone_number = cand
                                    break
                                (rejected if context_alive(self.context) else closed).append(cand)
                            except Exception as _e:
                                (busy if "already open" in str(_e) else broken).append(cand)
                                logger.warning(f"Fallback to {cand} failed: {_e}")
                        if phone_number:
                            await self._log_run(
                                f"نشست {failed} کار نکرد — با شمارهٔ دیگر خودتان {phone_number} ادامه می‌دهیم",
                                level="warning", phone=phone_number, previous=failed)

                        if phone_number:
                            self.active_phone = phone_number
                            logger.info(f"Session restored successfully using fallback: {phone_number}")
                        else:
                            # The run stops here, so the line says so and why.
                            # It said «شمارهٔ تماس آگهی‌ها استخراج نمی‌شود» — as
                            # though the run went on without numbers — and the
                            # finish line said «مرورگر اسکرپر بالا نیامد: نامشخص».
                            logger.warning(f"No session of the run's own opened (from {failed}) — not starting")
                            return await self._init_failed(
                                _no_session_reason(rejected, closed, busy, broken), phone=failed)
                    else:
                        self.active_phone = phone_number
                        logger.info("Session restored successfully")
                else:
                    logger.warning("No Divar session configured — phone numbers will not be extracted.")
                    # Said in the run, not only the server log: with ownership
                    # enforced, «no session» now usually means «none of YOUR
                    # numbers is on», and that is something the owner can fix.
                    await self._log_run(
                        "هیچ شمارهٔ دیوار روشن و معتبری به نام شما نیست — آگهی‌ها بدون "
                        "شمارهٔ تماس ذخیره می‌شوند. در «احراز هویت دیوار» شمارهٔ خودتان را "
                        "وارد کنید یا شمارهٔ خاموش را روشن کنید.",
                        level="warning")

            if self.context is None:
                # The CONTEXT, not the browser: a persistent context leaves
                # context.browser as None even on success, so testing the
                # browser here would re-open a second Chromium on the same
                # profile every run and fail on the profile guard.
                # restore_session=False: no account, so the default device.
                await self._open_browser_for(None, proxy)

            return True

        except Exception as e:
            logger.error(f"Failed to initialize scraper: {e}")
            # Kept for callers that want to say WHY to a person — «profile is
            # already open in this process» is a busy account, not a fault,
            # and deserves a different sentence than a crashed browser.
            self._init_error = str(e)
            self._init_reason = await self._explain_open_failure(e, phone_number)
            return False

    async def _init_failed(self, reason: str, **extra) -> bool:
        """The run cannot start: say why in its own log, once, keep it for
        run_scraping_job's finish line, and answer initialize's False."""
        self._init_reason = reason
        self._init_logged = True
        await self._log_run(reason, level="error", **extra)
        return False

    async def _pick_free_account(self, pool: List[str]) -> Optional[str]:
        """The first number of `pool` whose browser profile no run has open.
        Failing that, the first held only by a run that has already ended —
        its browser is closing, and _wait_for_released_profile waits for it.
        None when a live run has every one of them open.

        Two runs of one owner both took the least-spent number, and the second
        failed with «already open» while the owner's other numbers sat free."""
        from app.scraper.stealth import profile_in_use
        closing = None
        for phone in pool:
            if not await profile_in_use(phone):
                return phone
            if closing is None and not await self._live_run_on(phone):
                closing = phone
        return closing

    async def _open_first_free(self, pool: List[str], skip) -> Optional[str]:
        """Open the first number of `pool` outside `skip` that no run has
        open; its number, or None when every one is taken."""
        from app.scraper.stealth import profile_in_use
        for phone in pool:
            if phone in skip or await profile_in_use(phone):
                continue
            proxy = await self._get_working_proxy(phone) if self.proxy_enabled else None
            try:
                await self._open_browser_for(phone, proxy)
                return phone
            except RuntimeError as e:
                if "already open" not in str(e):
                    raise
                logger.info(f"[browser] {phone} was just taken by another run too — trying the next")
        return None

    def _all_numbers_busy(self, pool: List[str]) -> str:
        return _say(f"همهٔ شماره‌های دیوار شما ({_numbers_fa(pool, limit=3)}) همین حالا در "
                    "اسکرپ‌های دیگری باز است و هر شماره در یک زمان فقط در یک اسکرپ باز می‌شود",
                    "بعد از پایان یکی از آن‌ها «ادامه» را بزنید، یا در «احراز هویت دیوار» "
                    "شمارهٔ دیگری اضافه کنید")

    async def _holder_of(self, account: str) -> Optional[str]:
        """The short id of the live run that has `account` open, for a
        message; None when there is none or it cannot be told."""
        db = getattr(self, "db_session", None)
        if not account or db is None:
            return None
        try:
            q = select(ScrapingJob.job_id).where(
                ScrapingJob.divar_phone == account, ScrapingJob.status.in_(_LIVE_STATUSES))
            mine = getattr(self, "_job_id_str", None)
            if mine:
                q = q.where(ScrapingJob.job_id != uuid.UUID(str(mine)))
            held_by = (await db.execute(q.limit(1))).scalar_one_or_none()
            await db.commit()
            return str(held_by)[:8] if held_by else None
        except Exception as e:
            logger.debug(f"[browser] could not tell who has {account} open: {e}")
            try:
                await db.rollback()
            except Exception:
                pass
            return None

    async def _explain_open_failure(self, err: Exception, account: Optional[str]) -> str:
        """An exception out of initialize, in words. «already open» is a
        number (or the shared no-number browser) another run has open, not a
        fault; everything else is the browser itself."""
        if "already open" not in str(err):
            return browser_failure_reason(err)
        if account:
            holder = await self._holder_of(account)
            return _say(f"شمارهٔ {account} همین حالا در اسکرپ دیگری"
                        f"{f' ({holder})' if holder else ''} باز است و هر شماره در یک زمان "
                        "فقط در یک اسکرپ باز می‌شود",
                        "بعد از پایان آن «ادامه» را بزنید، یا اسکرپ را با «خودکار» شروع "
                        "کنید تا شمارهٔ آزاد دیگری از شماره‌های خودتان برداشته شود")
        return _say("اسکرپ دیگری که آن هم شمارهٔ دیوار ندارد همین حالا در حال اجراست و "
                    "مرورگرِ بدون شماره در یک زمان فقط در یک اسکرپ باز می‌شود",
                    "بعد از پایان آن «ادامه» را بزنید، یا در «احراز هویت دیوار» یک شمارهٔ "
                    "دیوار به نام خودتان اضافه کنید")
    
    def _client(self) -> httpx.AsyncClient:
        """The shared HTTP client, created on first use and closed in close()."""
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=30.0, follow_redirects=True)
        return self._http

    async def close(self):
        """Close browser and cleanup resources.

        Each step on its own. They were one try block, so the first that
        raised — an HTTP pool already closed, a browser that had crashed —
        skipped the rest: a Playwright driver left running per run, and the
        number's profile lock left for the refresher to keep alive. Each
        handle is dropped before its close, so a second call does nothing.
        A step that hangs is given up on after a minute: the driver's stop
        takes whatever Chromium is left with it."""
        http, self._http = getattr(self, "_http", None), None
        if http is not None and not http.is_closed:
            try:
                await http.aclose()
            except Exception as e:
                logger.warning(f"[browser] closing the HTTP client failed: {e}")
        # The context owns the browser in a persistent profile, and
        # context.browser is None — closing it releases both, and the
        # profile guard with them (close_context releases even when the
        # close itself fails).
        ctx = getattr(self, "context", None)
        self.browser = self.context = self.page = None
        if ctx is not None:
            try:
                await asyncio.wait_for(close_context(ctx), _CLOSE_STEP_TIMEOUT)
            except Exception as e:
                logger.warning(f"[browser] closing the browser failed: {type(e).__name__}: {e}")
        driver, self.playwright = getattr(self, "playwright", None), None
        if driver is not None:
            try:
                await asyncio.wait_for(driver.stop(), _CLOSE_STEP_TIMEOUT)
            except Exception as e:
                logger.warning(f"[browser] stopping Playwright failed: {type(e).__name__}: {e}")
        logger.info("Scraper closed")
    
    async def _get_working_proxy(self, account: Optional[str] = None) -> Optional[str]:
        """The proxy for this account — sticky, so one account is always one
        address. Used to take the single best proxy for everybody."""
        try:
            from app.services import proxy_pool
            return await proxy_pool.pick_for_account(
                self.db_session, account or getattr(self, "active_phone", None))
        except Exception as e:
            logger.error(f"Failed to get proxy: {e}")
            return None
    
    def _note_refusal(self, status: int) -> None:
        """Divar pushed back. Back off, and keep backing off if it continues.

        Exponential with jitter, capped at five minutes. Jittered because a
        fleet of clients all retrying on the same round number is precisely the
        pattern that makes a busy server busier.
        """
        self._refusals += 1
        base = min(20.0 * (2 ** (self._refusals - 1)), 300.0)
        wait = base * random.uniform(0.7, 1.3)
        self._cooldown_until = max(self._cooldown_until, time.monotonic() + wait)
        logger.warning(
            f"[pace] Divar answered {status} — refusal #{self._refusals}, "
            f"backing off {wait:.0f}s")

    async def _live_run_on(self, account: str) -> bool:
        """Whether another run that is running or waiting for a code is on
        this account — the one case where its profile is really in use.
        When that cannot be told, yes: the caller then fails as it always
        did instead of waiting on a guess."""
        if self.db_session is None:
            return True
        from sqlalchemy import select as _select
        try:
            q = _select(ScrapingJob.id).where(
                ScrapingJob.divar_phone == account,
                ScrapingJob.status.in_(("running", "paused")))
            mine = getattr(self, "_job_id_str", None)
            if mine:
                q = q.where(ScrapingJob.job_id != uuid.UUID(str(mine)))
            found = (await self.db_session.execute(q.limit(1))).first() is not None
            # nothing may hold a transaction across the sleep that follows
            await self.db_session.commit()
            return found
        except Exception as e:
            logger.warning(f"[browser] could not tell who holds {account}: {e}")
            try:
                await self.db_session.rollback()
            except Exception:
                pass
            return True

    async def _wait_for_released_profile(self, account: Optional[str], *,
                                         limit: float = 180.0, step: float = 3.0) -> None:
        """Wait for the browser of a run that has already ended on this account.

        Cancelling a run only marks its row; its browser closes — and releases
        the account's profile lock — once the run reaches its next check,
        which takes the rest of the listing in hand and the pause after it.
        A run started on the same number meanwhile failed at once with «در
        یک اسکرپ دیگر در حال اجراست» (1405/07/05). When no live run holds
        the number, the holder is one on its way out, so wait for it (up to
        the lock's own 120 s TTL and then some). When a live run holds it,
        return at once and let the open fail as it always has."""
        if not account:
            return
        from app.scraper.stealth import profile_in_use
        if not await profile_in_use(account) or await self._live_run_on(account):
            return
        logger.info(f"[browser] {account} is still open in a run that has ended — waiting for it to close")
        await self._log_run(
            "این شماره هنوز در مرورگر یک اسکرپ لغوشده یا تمام‌شده باز است — "
            "تا بسته شدنش صبر می‌کنیم (حداکثر ۳ دقیقه)", level="info", phone=account)
        started = time.monotonic()
        while time.monotonic() - started < limit:
            await asyncio.sleep(step)
            if not await profile_in_use(account):
                waited = time.monotonic() - started
                logger.info(f"[browser] {account} released after {waited:.0f}s")
                await self._log_run(f"مرورگر قبلی بعد از {waited:.0f} ثانیه بسته شد — شروع می‌کنیم",
                                    level="info", phone=account)
                return
            if await self._live_run_on(account):
                return

    def _job_pk(self):
        """The run's row id, read without an attribute load: after a rollback
        the ORM object is expired, and touching job.id then is a lazy load —
        MissingGreenlet on this async session."""
        job = getattr(self, "current_job", None)
        if job is None:
            return None
        try:
            from sqlalchemy import inspect as _sa_inspect
            ident = _sa_inspect(job).identity
        except Exception:
            ident = None
        return ident[0] if ident else job.__dict__.get("id")

    async def _move_status(self, to: str, *, only_from: tuple = _LIVE_STATUSES) -> bool:
        """Set this run's status to `to` only while its row still says one of
        `only_from`. True when it moved. In the session's current transaction:
        the caller commits.

        The run wrote its status through the ORM object, which holds what the
        row said when it was last read. A cancel committed since then was
        written over: waiting for an SMS code wrote «paused» over it, the code
        (or the timeout) «running», and the run went on to the end; the end
        of the run wrote «completed» over a cancel, or over the sweep's
        «failed», that landed during its last listing. As a conditional
        UPDATE, whatever the button or the sweep wrote wins. The object is
        told the row's real status either way, without marking it changed.
        """
        job = getattr(self, "current_job", None)
        db = getattr(self, "db_session", None)
        if job is None or db is None:
            return False
        from sqlalchemy import update as _update
        from sqlalchemy.orm.attributes import set_committed_value
        pk = self._job_pk()
        moved = (await db.execute(
            _update(ScrapingJob)
            .where(ScrapingJob.id == pk, ScrapingJob.status.in_(only_from))
            .values(status=to)
            .returning(ScrapingJob.id)
            .execution_options(synchronize_session=False))).scalar_one_or_none() is not None
        if moved:
            set_committed_value(job, "status", to)
        else:
            now = (await db.execute(
                select(ScrapingJob.status).where(ScrapingJob.id == pk))).scalar_one_or_none()
            if isinstance(now, str):
                set_committed_value(job, "status", now)
        return moved

    async def _finish_status(self, status: str) -> bool:
        """The run's last word on its own status — `status` is whatever the
        run concluded — unless a cancel or the sweep got there first, in which
        case that stays. The caller commits, with the rest of the finish."""
        moved = await self._move_status(status)
        if not moved:
            logger.info(f"Job {getattr(self, '_job_id_str', None) or self._job_pk()} became "
                        f"«{getattr(self.current_job, 'status', None)}» while it was finishing — "
                        f"left so, not «{status}»")
        return moved

    async def _pause_for_code(self) -> None:
        """ContactExtractor's on_pause: the row reads «paused» while the run
        waits for an SMS code — unless the run was stopped meanwhile. Then it
        stays stopped, and the wait's first check (_cancelled_now) ends it."""
        job = getattr(self, "current_job", None)
        if job is None:
            return
        moved = await self._move_status("paused")
        await self.db_session.commit()
        jid = getattr(self, "_job_id_str", None) or str(job.job_id)
        if not moved:
            logger.info(f"Job {jid} is «{job.status}» — not pausing it for a code")
            return
        logger.info(f"Job {jid} PAUSED — awaiting OTP code")
        from app.services import job_log
        await job_log.record(jid, job_log.PAUSE,
                             "دیوار کد تأیید خواست — اسکرپ متوقف شد تا کد وارد شود",
                             level="warning")

    async def _resume_after_code(self) -> None:
        """ContactExtractor's on_resume: «paused» back to «running» — and only
        that. A cancel that came during the wait stays a cancel."""
        job = getattr(self, "current_job", None)
        if job is None:
            return
        moved = await self._move_status("running", only_from=("paused",))
        await self.db_session.commit()
        if moved:
            jid = getattr(self, "_job_id_str", None) or str(job.job_id)
            logger.info(f"Job {jid} RESUMED")
            from app.services import job_log
            await job_log.record(jid, job_log.RESUME, "کد وارد شد — اسکرپ ادامه پیدا کرد")

    async def _cancelled_now(self) -> bool:
        """Whether the current run has been stopped from outside — cancelled,
        or failed by the queue's sweep — asked without leaving a transaction
        open: the caller is in the middle of a sleep, and Postgres closes a
        connection idle in a transaction after 60 s."""
        job = getattr(self, "current_job", None)
        if job is None or self.db_session is None:
            return False
        from sqlalchemy import select as _select
        try:
            status = (await self.db_session.execute(
                _select(ScrapingJob.status).where(ScrapingJob.id == self._job_pk()))).scalar_one_or_none()
            await self.db_session.commit()
            return status in _STOPPED_STATUSES
        except Exception as e:
            logger.debug(f"[pace] cancel check failed: {e}")
            try:
                await self.db_session.rollback()
            except Exception:
                pass
            return False

    async def _pause(self, seconds: float, stop_on_cancel: bool) -> bool:
        """Sleep; with stop_on_cancel, in steps of at most 2 s that end early
        once the run is cancelled. True when a cancel cut it short."""
        if not stop_on_cancel:
            await asyncio.sleep(seconds)
            return False
        end = time.monotonic() + seconds
        while (left := end - time.monotonic()) > 0:
            await asyncio.sleep(min(2.0, left))
            if await self._cancelled_now():
                return True
        return False

    async def _human_like_delay(self, min_delay: Optional[float] = None,
                                max_delay: Optional[float] = None,
                                *, stop_on_cancel: bool = False):
        """Wait between actions, and wait out any backoff we owe Divar.

        Two changes from a flat random.uniform(0.35, 0.9):

        * The cooldown is honoured first. Without it a 429 changed nothing and
          the scraper kept knocking at the rate that had just been refused.
        * The distribution is heavy-tailed. Real browsing is bursty: mostly
          quick, occasionally a long pause while somebody reads something. A
          tight uniform window is a signature in itself, and it is also simply
          harder on the server than the same work spread out.

        stop_on_cancel is for the pause between listings: a cancelled run
        used to sit out the whole of it — up to five minutes of cooldown —
        before its next check noticed, holding the account's browser all the
        while, so a new run on that number could not open it.
        """
        now = time.monotonic()
        if now < self._cooldown_until:
            owed = self._cooldown_until - now
            logger.info(f"[pace] cooling down for {owed:.0f}s before the next request")
            if await self._pause(owed, stop_on_cancel):
                return

        min_d = min_delay or self.stealth_config.min_delay
        max_d = max_delay or self.stealth_config.max_delay
        if random.random() < 0.12:
            # the pause where a person actually reads the ad
            delay = random.uniform(max_d, max_d * 4)
        else:
            delay = random.uniform(min_d, max_d)
        await self._pause(delay, stop_on_cancel)
    
    async def _simulate_scroll(self):
        """Simulate human-like scrolling"""
        try:
            for _ in range(self.stealth_config.scroll_steps):
                scroll_distance = self.stealth_config.get_random_scroll_distance()
                await self.page.evaluate(f"window.scrollBy(0, {scroll_distance})")
                await asyncio.sleep(self.stealth_config.scroll_delay)
        except Exception as e:
            logger.warning(f"Scroll simulation failed: {e}")
    
    def _generate_tag_number(self) -> str:
        """Generate unique tag number for property"""
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        random_suffix = uuid.uuid4().hex[:6].upper()
        return f"SF-{timestamp}-{random_suffix}"
    
    def _extract_divar_id(self, url: str) -> Optional[str]:
        """Extract Divar listing ID from URL"""
        try:
            clean = url.split('?')[0].rstrip('/')
            parts = clean.split('/')
            return parts[-1] if parts else None
        except:
            return None
    
    def _parse_persian_number(self, text: str) -> Optional[int]:
        """Convert Persian numbers to integer"""
        if not text:
            return None
        
        persian_digits = '۰۱۲۳۴۵۶۷۸۹'
        english_digits = '0123456789'
        
        translation_table = str.maketrans(persian_digits, english_digits)
        text = text.translate(translation_table)
        text = re.sub(r'[^\d]', '', text)
        
        try:
            return int(text) if text else None
        except ValueError:
            return None
    
    @staticmethod
    def _memory_fraction() -> Optional[float]:
        """How much of the container's memory limit is in use, 0..1, or None.

        Read from the cgroup rather than psutil: inside a container psutil
        reports the HOST's memory, so a pod at 96% of its 2Gi limit looks like
        48% of a 4GB box and nothing appears wrong right up until the OOM
        killer arrives. cgroup v2 first, then v1.
        """
        pairs = (("/sys/fs/cgroup/memory.current", "/sys/fs/cgroup/memory.max"),
                 ("/sys/fs/cgroup/memory/memory.usage_in_bytes",
                  "/sys/fs/cgroup/memory/memory.limit_in_bytes"))
        for use_p, max_p in pairs:
            try:
                with open(use_p) as f:
                    used = int(f.read().strip())
                with open(max_p) as f:
                    raw = f.read().strip()
                if raw == "max":
                    return None                      # no limit set
                limit = int(raw)
                # cgroup v1 writes a sentinel near 2**63 when unlimited
                if limit <= 0 or limit > (1 << 62):
                    return None
                return used / limit
            except (OSError, ValueError):
                continue
        return None

    async def _recycle_browser(self, why: str) -> None:
        """Close Chromium and open a fresh one, keeping the session.

        Chromium does not give memory back. Over a few hundred navigations a
        long run climbs steadily, and on a 2Gi pod that ends as an OOM kill —
        which looks like «اسکرپر کرش کرد» and leaves the job row stuck at
        «در حال اجرا» until the next boot marks it failed.
        """
        logger.warning(f"[memory] recycling the browser: {why}")
        phone = self.active_phone

        # Save the jar the live browser is holding BEFORE tearing it down.
        # Without this the restore below replays whatever was last written to
        # the database — older than what the browser had, because Divar hands
        # back a fresh sAccessToken on every navigation. Never allowed to
        # block the recycle: a failed save is worth less than a recycled
        # browser.
        try:
            await self._persist_active_session()
        except Exception as e:
            logger.warning(f"[memory] could not persist before the recycle: {e}")

        # Replace the BROWSER, not the Playwright driver.
        #
        # The first version called self.close(), which also does
        # `await self.playwright.stop()`, and then started a fresh driver. That
        # tears down Playwright's subprocess transports on the running event
        # loop, and the asyncpg connections live on that same loop: fifteen
        # seconds later every query failed with "connection is closed", and
        # SQLAlchemy's attempt to recover surfaced as MissingGreenlet. The run
        # died at listing 1 of 222 having collected them all.
        #
        # Chromium is the memory, not the driver, so closing the browser is the
        # whole point and stopping the driver bought nothing.
        ctx = getattr(self, "context", None)
        if ctx is not None:
            try:
                await close_context(ctx)
            except Exception as e:
                logger.warning(f"[memory] closing the context failed: {e}")
        self.page = self.context = self.browser = None

        await asyncio.sleep(1)

        try:
            if self.playwright is None:
                self.playwright = await async_playwright().start()
            proxy = await self._get_working_proxy(phone) if self.proxy_enabled else None
            # Same account, same device, same proxy: the recycle must be invisible to
            # Divar, and it is only invisible if the fresh browser presents
            # exactly as the old one did.
            await self._open_browser_for(phone, proxy)
        except Exception as e:
            logger.error(f"[memory] could not start a fresh browser: {e}")
            raise

        self.request_count = 0
        self.session_start = datetime.now()

        # Hand the NEW browser to auth before asking it to do anything.
        #
        # initialize() points self.auth at the scraper's page/context/browser
        # once, at start. This method replaced all three and left auth holding
        # the closed ones — so restore_session() below saw a dead browser,
        # refused ("the browser is gone"), and the fresh Chromium went to Divar
        # with no cookies at all. Divar did what it should to an anonymous
        # visitor asking for a phone number: demanded a code. Six minutes into
        # every run, right after this recycle, on every account. That was read
        # as detection for a week. It was a logout.
        #
        # The same stale pointer made maybe_rotate_account refuse every
        # rotation for the rest of the run ("no account can be restored onto
        # it"), which is why one account carried 223 reveals while three sat
        # at zero.
        # (_open_browser_for has already pointed auth at the new browser.)

        if phone:
            try:
                restored = await self.auth.restore_session(phone)
            except Exception as e:
                logger.error(f"[memory] could not restore {phone} after recycle: {e}")
                restored = False
            if restored:
                self.active_phone = phone
                logger.info(f"[memory] session for {phone} restored after recycle")
            else:
                # restore_session returns False rather than raising, and this
                # used to log «restored» on that False. A browser with no
                # session must not be reported as one that has it.
                logger.error(
                    f"[memory] session for {phone} was NOT restored after the "
                    f"recycle — the next contact click will be anonymous and "
                    f"Divar will ask for a code")
                if self._job_id_str:
                    from app.services import job_log as _jl
                    await _jl.record(
                        self._job_id_str, _jl.SESSION,
                        f"نشست {phone} بعد از بازراه‌اندازی مرورگر بازیابی نشد",
                        level="error", phone=phone)

        # job_id captured as a plain value, not read off the ORM object: the
        # row may be expired after a commit, and refreshing it here would be one
        # more piece of database IO in the middle of a browser restart.
        if self._job_id_str:
            from app.services import job_log
            await job_log.record(
                self._job_id_str, job_log.PAGE,
                f"مرورگر برای آزادسازی حافظه بازراه‌اندازی شد ({why})",
                level="warning")

    async def _check_rate_limit(self):
        """Check and enforce rate limiting"""
        self.request_count += 1

        # A refusal's cooldown applies to the NEXT request, whatever makes it.
        # _note_refusal set the deadline and only the per-listing delay ever
        # read it, so a 429 during collection was followed by the very next
        # navigation without pause — the one moment Divar had just asked for
        # one. Every page load passes through here; this is where it belongs.
        now = time.monotonic()
        if now < self._cooldown_until:
            owed = self._cooldown_until - now
            logger.info(f"[backoff] honouring a refusal cooldown: {owed:.0f}s before the next request")
            await asyncio.sleep(owed)

        # Recycle before the OOM killer does it for us.
        #
        # The request-count ceiling below is a poor proxy for memory: at 500 it
        # never fired before a 2Gi pod ran out, because what grows is Chromium's
        # footprint per navigation, not our request tally. This checks the thing
        # that actually matters.
        frac = self._memory_fraction()
        if frac is not None and frac >= 0.80:
            await self._recycle_browser(f"container memory at {frac:.0%}")
            return
        
        # Check requests per minute.
        #
        # This was `request_count / elapsed * 60` from the first request on,
        # and on a fresh instance the first request comes 0.1s after
        # session_start: 1 / 0.1 × 60 = 600 rpm, «Rate limit reached», a
        # 59-second sleep. Every «اسکرپ تکی» paid it on its very first page —
        # a projection from a tenth of a second treated as a minute's rate.
        # In the first minute the only rate that means anything is the plain
        # count; the projection is trusted once there is a minute to project
        # from.
        limit = self.stealth_config.max_requests_per_minute
        elapsed = (datetime.now() - self.session_start).total_seconds()
        over = (self.request_count > limit) if elapsed < 60 \
            else ((self.request_count / elapsed) * 60 > limit)
        if over:
            wait_time = 60 - (elapsed % 60)
            logger.info(f"Rate limit reached. Waiting {wait_time:.1f} seconds...")
            await asyncio.sleep(wait_time)
        
        # Check requests per session
        if self.request_count >= self.stealth_config.max_requests_per_session:
            await self._recycle_browser(
                f"{self.request_count} requests this browser session")
    
    async def _fetch_listings_direct_api(
        self, city: str, category: str, page_num: int,
        last_post_date: Optional[int] = None,
    ) -> tuple:
        """Fetch the next page of listings by replaying Divar's own search POST
        — from inside the page, with the page's own fetch().

        Returns (listings, last_post_date).

        This used to go out through httpx with a hand-built Cookie header and a
        random User-Agent. That request carried the browser's session cookies
        but nothing else of the browser: a Python TLS fingerprint, HTTP/1.1
        where Chrome speaks h2, no Sec-CH-UA, no Sec-Fetch-*, headers in
        httpx's order — and a different UA on every call. One session, two
        clients. Every other page of a run was the odd one out.

        page.evaluate(fetch) is literally what Divar's frontend does. Same TLS,
        same h2, same header order, same cookies (credentials: include), same
        device — because it IS the same browser, on the same origin.
        """
        listings: List[Dict[str, Any]] = []
        next_last_post_date: Optional[int] = None
        # What Divar answered this page, for the collector: an empty page it
        # refused and an empty page it simply did not have are not the same
        # end, and only this call saw which one it was. None: never asked.
        self._replay_status = None
        self._replay_more = None

        template = self._search_req_template
        if not (template and template.get('post_data') and self.page and not self.page.is_closed()):
            if not template:
                logger.info("[api] no captured search request to replay — "
                            "listing collection is DOM-only for this run")
            return listings, next_last_post_date

        try:
            import json as _json
            body = _json.loads(template['post_data'])
            pd = body.get('pagination_data')
            if not isinstance(pd, dict):
                pd = {"@type": "type.googleapis.com/post_list.PaginationData"}
            # Only send a cursor we actually have. Writing None here would
            # post `"last_post_date": null`.
            if isinstance(last_post_date, int) and last_post_date > 0:
                pd['last_post_date'] = last_post_date
            pd['page'] = page_num
            if 'layer_page' in pd:
                pd['layer_page'] = page_num
            body['pagination_data'] = pd

            result = await self.page.evaluate(
                """async ({url, body}) => {
                    const r = await fetch(url, {
                        method: 'POST',
                        credentials: 'include',
                        headers: {
                            'Content-Type': 'application/json',
                            'Accept': 'application/json, text/plain, */*',
                            'x-render-type': 'CSR',
                            'x-standard-divar-error': 'true',
                        },
                        body: JSON.stringify(body),
                    });
                    let data = null;
                    try { data = await r.json(); } catch (e) {}
                    return {status: r.status, data};
                }""",
                {"url": template['url'], "body": body},
            )
            status = int(result.get("status") or 0)
            self._replay_status = status or None
            logger.info(f"[api] in-page replay POST {template['url']} → {status}")
            if status == 200 and result.get("data") is not None:
                data = result["data"]
                pagination = (data.get("pagination") or {}) if isinstance(data, dict) else {}
                if "has_next_page" in pagination:
                    # Divar's own word on whether the list goes on: the only
                    # thing that tells its end from a cursor that is stuck.
                    self._replay_more = bool(pagination.get("has_next_page"))
                parsed, lpd = self._parse_api_response(data)
                if parsed:
                    logger.info(f"Got {len(parsed)} listings via replayed postlist/w/search")
                    return parsed, lpd
            elif status in (401, 403, 429) or status >= 500:
                # Was invisible before: the API phase never reported refusals,
                # so a run refused here read as «the feed ran out».
                self._note_refusal(status)
        except Exception as e:
            logger.debug(f"[api] in-page replay failed: {e}")

        # The legacy /v8/web-search endpoint is gone, and deleting it is the
        # fix rather than tidying.
        #
        # Verified on the wire, 2026-09-02:
        #
        #   GET https://api.divar.ir/v8/web-search/tehran/real-estate
        #   -> 200, 1559 bytes
        #      {"widget_list":[{"widget_type":"BLOCKING_VIEW",
        #        "title":"نیاز به بروزرسانی",
        #        "description":"شما از نسخهٔ قدیمی اپلیکیشن دیوار ..."}],
        #       "last_post_date": -1}
        #
        # HTTP 200, so it never looked like a failure. Zero listings, three
        # requests per page (GET with params, GET without, then a POST), each
        # carrying our live session cookie to an endpoint whose only reply is
        # "your app is out of date". And its "last_post_date": -1 is truthy in
        # Python, so any code that trusted the returned cursor would poison the
        # next request with it.
        #
        # Everything real comes from replaying the browser's own
        # /postlist/w/search POST above, from inside the page.
        return listings, next_last_post_date

    async def _switch_to_list_view(self) -> bool:
        """Attempt to switch Divar from map view to list view. Returns True if switched."""
        # CSS selector attempts — includes "بستن نقشه" (Close Map) button visible on screenshot
        for sel in [
            'button[data-testid="list-tab"]',
            'button[data-testid="LIST"]',
            '[aria-label="لیست"]',
            'button:has-text("بستن نقشه")',   # "Close Map" — shows full list view
            'button:has-text("لیست")',
            '.kt-action-header__button--active + button',
        ]:
            try:
                btn = await self.page.query_selector(sel)
                if btn and await btn.is_visible():
                    await btn.click()
                    await asyncio.sleep(2)
                    logger.info(f"[view] switched to list view via '{sel}'")
                    return True
            except Exception:
                pass

        try:
            clicked = await self.page.evaluate("""() => {
                const keywords = ['بستن نقشه', 'لیست', 'list', 'LIST', 'فهرست'];
                const elems = [...document.querySelectorAll('button, [role=tab], a')];
                for (const kw of keywords) {
                    const el = elems.find(e => (e.innerText || '').trim().includes(kw) || e.getAttribute('aria-label') === kw);
                    if (el) { el.click(); return kw; }
                }
                return null;
            }""")
            if clicked:
                await asyncio.sleep(2)
                logger.info(f"[view] switched via JS: clicked '{clicked}'")
                return True
        except Exception as e:
            logger.debug(f"[view] JS switch failed: {e}")

        logger.warning("[view] Could not switch to list view — proceeding in current view")
        return False

    @staticmethod
    def _token_from_url(url: str) -> Optional[str]:
        """The listing token out of any Divar listing URL — /v/<slug>/<token>
        or bare /v/<token> — or None if this is not one."""
        m = re.search(r"/v/(?:[^/?#]+/)?([A-Za-z0-9_-]{6,20})(?:[/?#]|$)", str(url or ""))
        return m.group(1) if m else None

    async def _visible_link_count(self) -> int:
        """How many listing links the page currently shows."""
        try:
            return int(await self.page.evaluate(
                "() => document.querySelectorAll('a[href*=\"/v/\"]').length"))
        except Exception:
            return -1

    async def _wait_for_links_beyond(self, before: int, ceiling: float) -> None:
        """Return as soon as the page shows more listing links than `before`,
        or after `ceiling` seconds — whichever is first. The ceiling is the
        old fixed sleep, so a slow Divar is waited for exactly as long as it
        was; a fast one no longer is."""
        if before < 0:
            await asyncio.sleep(ceiling)
            return
        waited = 0.0
        while waited < ceiling:
            await asyncio.sleep(0.25)
            waited += 0.25
            now = await self._visible_link_count()
            if now > before:
                # a beat for the rest of the batch to land alongside
                await asyncio.sleep(0.4)
                return

    async def _click_load_more(self) -> bool:
        """Click the 'آگهی‌های بیشتر' (Load More) button in Divar's list view.

        Divar's new list view (shown after closing the map) paginates via an
        explicit button click — NOT pure infinite scroll.  Each click loads the
        next batch of ~24 listings.  Returns True if a button was clicked.
        """
        try:
            result = await self.page.evaluate("""() => {
                const candidates = [...document.querySelectorAll('button, a[role=button], a')];
                for (const b of candidates) {
                    const t = (b.innerText || '').replace(/\\s+/g, ' ').trim();
                    // Match the load-more button specifically — it contains both
                    // 'آگهی' and 'بیشتر'. Avoid 'نمایش نقشه' (show map) and the
                    // detail-page description 'بیشتر' button.
                    if (t && t.includes('آگهی') && t.includes('بیشتر') && t.length < 30) {
                        const r = b.getBoundingClientRect();
                        if (r.width > 0 && r.height > 0) {
                            b.scrollIntoView({block: 'center'});
                            b.click();
                            return t;
                        }
                    }
                }
                return null;
            }""")
            if result:
                logger.info(f"[dom] clicked load-more button: '{result}'")
                return True
        except Exception as e:
            logger.debug(f"[dom] load-more click failed: {e}")
        return False

    async def _collect_from_browser_dom(
        self, city: str, category: str, target_count: int
    ) -> List[Dict[str, Any]]:
        """Collect listings by extracting /v/ token links from the live rendered DOM.

        Works regardless of API response format changes — reads what Divar has
        already rendered via JS, including cards that appear after scrolling.
        Also intercepts API responses as a bonus to get richer metadata.
        """
        self._collect_stop = None
        self._dom_cursor = None
        all_listings: List[Dict[str, Any]] = []
        seen_ids: set = set()
        pending_api: list = []

        # What Divar said while we were scrolling, when it was not "200 OK".
        #
        # This listener used to `return` on any non-200 and say nothing. So when
        # Divar started refusing — 403 because the session had been killed, 429
        # because we were going too fast — the scraper carried on scrolling an
        # empty feed, collected whatever it already had, and reported the run
        # COMPLETED. That is the whole of "it was cancelled by Divar and the log
        # says it is OK": the one moment the truth was on the wire, we dropped it.
        refusals: Dict[str, int] = {}

        async def _on_resp(response):
            try:
                if 'api.divar.ir' not in response.url:
                    return
                if response.status != 200:
                    if response.status in (401, 403, 429) or response.status >= 500:
                        refusals[str(response.status)] = refusals.get(str(response.status), 0) + 1
                        logger.warning(
                            f"[dom] Divar answered {response.status} during collection "
                            f"({sum(refusals.values())} refusal(s) so far)")
                        self._note_refusal(response.status)
                    return
                if 'json' not in response.headers.get('content-type', ''):
                    return
                if (
                    '/postlist/w/search' in response.url
                    or '/v8/web-search' in response.url
                    or (city in response.url and category in response.url)
                ):
                    data = await response.json()
                    pending_api.append(data)
                    logger.info(
                        f"[dom] captured {response.url.split('?')[0]} "
                        f"top_keys={list(data.keys())[:8] if isinstance(data, dict) else type(data).__name__}"
                    )
            except Exception as e:
                logger.debug(f"[dom] _on_resp error: {e}")

        async def _on_request(request):
            # Capture the browser's real /postlist/w/search POST so we can replay
            # it (with an advanced cursor) via httpx for reliable pagination.
            try:
                if request.method == 'POST' and '/postlist/w/search' in request.url:
                    pd = request.post_data
                    if pd:
                        self._search_req_template = {'url': request.url, 'post_data': pd}
            except Exception:
                pass

        self.page.on("request", _on_request)
        self.page.on("response", _on_resp)
        try:
            # Ask Divar to apply the filters it is willing to apply.
            #
            # This was a bare «/s/{city}/{category}». One real run collected 204
            # listings from that unfiltered feed and kept 14, dropping 131 on
            # deposit alone — while the 201 that actually matched sat further
            # down a feed the run had already stopped reading. That is the whole
            # of «it says completed but there should be 202».
            _q = getattr(self, "_search_query", "") or ""
            url = f"{self.BASE_URL}/s/{city}/{category}" + (f"?{_q}" if _q else "")
            logger.info(f"[dom] Loading {url} | target={target_count}")
            await self._check_rate_limit()
            try:
                await self.page.goto(url, wait_until="networkidle", timeout=45000)
            except Exception:
                # networkidle timeout is OK — page is still usable
                logger.info("[dom] networkidle timeout — continuing with loaded content")
            await asyncio.sleep(3)

            switched = await self._switch_to_list_view()
            # After closing map, wait for the full list view to render
            await asyncio.sleep(4 if switched else 2)

            # Confirm the map actually closed (the 'نمایش نقشه' / Show-Map button
            # appears only in list view). Diagnostic only — the dual-scroll logic
            # below handles either view regardless of the outcome.
            try:
                in_list_view = await self.page.evaluate("""() => {
                    return [...document.querySelectorAll('button, a')].some(b =>
                        (b.innerText || '').includes('نمایش نقشه'));
                }""")
                logger.info(f"[dom] list-view confirmed={in_list_view} (switch returned {switched})")
            except Exception:
                pass

            # Verify listing cards appeared; log how many /v/ links exist now
            try:
                await self.page.wait_for_selector('a[href*="/v/"]', timeout=8000)
            except Exception:
                logger.warning("[dom] No /v/ links visible after view switch")

            vp = self.page.viewport_size or {"width": 1280, "height": 720}
            await self.page.mouse.move(vp["width"] // 2, vp["height"] // 2)

            no_new_streak = 0
            no_button_streak = 0
            max_scrolls = max(50, target_count // 2)

            for scroll_n in range(max_scrolls):
                prev = len(all_listings)

                # Drain any captured API responses for richer metadata.
                #
                # Swap the list out rather than removing from it. `list.remove`
                # searches by equality, and these are large nested dicts, so
                # draining N responses meant N deep dict comparisons over a
                # shrinking list — quadratic, on the biggest objects in the
                # process, on every scroll.
                batch_api, pending_api[:] = list(pending_api), []
                for data in batch_api:
                    parsed, _cur = self._parse_api_response(data)
                    # Divar just told us where the next page starts. Keep it:
                    # this is the cursor the API phase needs to page deeper,
                    # and it was being discarded one line from where it was
                    # needed. Non-positive values are rejected — the dead
                    # legacy endpoint answers with -1, which is truthy.
                    if isinstance(_cur, int) and _cur > 0:
                        self._dom_cursor = _cur
                    logger.info(
                        f"[dom] API parse: {len(parsed)} tokens from "
                        f"top_keys={list(data.keys())[:8] if isinstance(data, dict) else type(data).__name__}"
                    )
                    for lst in parsed:
                        if lst['divar_id'] not in seen_ids:
                            seen_ids.add(lst['divar_id'])
                            all_listings.append(lst)

                # Extract tokens from:
                # 1. Regex scan of window.__NEXT_DATA__ JSON (fastest, gets all pre-loaded data)
                # 2. a[href*="/v/"] rendered DOM links
                # 3. Any element with data-token attribute
                dom_items = await self.page.evaluate(r"""() => {
                    const TOKEN_RE = /^[A-Za-z0-9]{4,20}$/;
                    const seen = new Map();   // token -> index into results
                    const results = [];

                    // First one wins, EXCEPT for the title.
                    //
                    // Method 1 below scans __NEXT_DATA__ and can only supply
                    // the token, so it registers every pre-loaded listing with
                    // no title at all — and it runs first, so the rendered
                    // link's own text could never reach the listing it belongs
                    // to. That mattered far downstream: a listing with no title
                    // and a bare /v/<token> URL carries no category signal at
                    // all, and the category check drops exactly those. They
                    // were being thrown away for having no name, not for being
                    // the wrong kind of ad.
                    const addToken = (tok, title) => {
                        if (!tok || !TOKEN_RE.test(tok)) return;
                        title = (title || '').trim().substring(0, 120);
                        const at = seen.get(tok);
                        if (at !== undefined) {
                            if (!results[at].title && title) results[at].title = title;
                            return;
                        }
                        seen.set(tok, results.length);
                        results.push({ href: 'https://divar.ir/v/' + tok, title });
                    };

                    const tokFromUrl = (url) => {
                        if (!url || !url.includes('/v/')) return null;
                        const segs = url.split('/v/')[1].split('?')[0].split('/');
                        return segs[segs.length - 1];
                    };

                    // Method 1: regex scan of __NEXT_DATA__ JSON string (very fast).
                    // ONLY match tokens inside a /v/SLUG/TOKEN post URL. A bare
                    // "token":"..." match also catches widget/tracking/category
                    // tokens, producing bogus /v/ URLs that redirect away on the
                    // detail page and inflate failed_items.
                    if (window.__NEXT_DATA__) {
                        try {
                            const json = JSON.stringify(window.__NEXT_DATA__);
                            const re2 = /\/v\/[^"]*\/([A-Za-z0-9]{6,20})(?=["?])/g;
                            let m;
                            while ((m = re2.exec(json)) !== null) addToken(m[1], '');
                        } catch(e) {}
                    }

                    // Method 2: rendered <a href="/v/..."> links
                    for (const a of document.querySelectorAll('a[href*="/v/"]')) {
                        addToken(tokFromUrl(a.href), (a.innerText || '').trim());
                    }

                    // Method 3: data-token attributes anywhere on the page
                    for (const el of document.querySelectorAll('[data-token]')) {
                        addToken(el.dataset.token, (el.innerText || '').trim());
                    }

                    return results;
                }""")
                for item in (dom_items or []):
                    href = item.get('href', '')
                    if '/v/' not in href:
                        continue
                    # Divar URL format: /v/TITLE-SLUG/TOKEN  or  /v/TOKEN
                    # Token is always the LAST alphanumeric segment before query params
                    path = href.split('/v/', 1)[1].split('?')[0].rstrip('/')
                    token = path.split('/')[-1]
                    # Divar tokens: 4-20 chars, strictly alphanumeric (no hyphens/Persian)
                    if not token or not re.match(r'^[A-Za-z0-9]{4,20}$', token):
                        continue
                    if token in seen_ids:
                        continue
                    seen_ids.add(token)
                    all_listings.append({
                        'divar_id': token,
                        'url': f"https://divar.ir/v/{token}",
                        'title': item.get('title') or None,
                        'descriptions': [],
                    })

                gained = len(all_listings) - prev
                logger.info(
                    f"[dom scroll #{scroll_n}] +{gained} items | total {len(all_listings)}/{target_count}"
                )

                if len(all_listings) >= target_count:
                    self._collect_stop = ("target", None)
                    break

                # ── Scroll to bottom to reveal the 'آگهی‌های بیشتر' (Load More) button ──
                # The list paginates via an explicit button click. The scrollable
                # element differs by view: it's the window when the map is closed,
                # but the sidebar container when the map is open. Scroll BOTH the
                # window and every scrollable ancestor of a listing card so the
                # button is revealed regardless of which view Divar rendered.
                await self.page.evaluate(r"""() => {
                    window.scrollTo(0, document.body.scrollHeight);
                    const link = document.querySelector('a[href*="/v/"]');
                    let el = link && link.parentElement;
                    while (el) {
                        const st = getComputedStyle(el);
                        if ((st.overflowY === 'auto' || st.overflowY === 'scroll')
                            && el.scrollHeight > el.clientHeight + 50) {
                            el.scrollTop = el.scrollHeight;
                        }
                        el = el.parentElement;
                    }
                }""")
                # Wait for the page to CHANGE, not for a number of seconds.
                #
                # These were fixed sleeps — 1.2s, then 3.5s after a load-more
                # click or 2.5s after wheeling — about five seconds a cycle
                # regardless of how fast Divar actually rendered the batch,
                # which is usually well under one. Over the twenty-odd cycles a
                # city takes, that was most of the minutes between «شروع» and
                # the first listing opened. Now: poll the link count and move
                # on the moment it grows, with the old sleep as the ceiling.
                before = await self._visible_link_count()
                await self._wait_for_links_beyond(before, 1.2)

                clicked_more = await self._click_load_more()
                if clicked_more:
                    no_button_streak = 0
                    await self._wait_for_links_beyond(before, 3.5)
                else:
                    no_button_streak += 1
                    # No button found — fall back to wheel events (infinite-scroll variant)
                    for _ in range(12):
                        await self.page.mouse.wheel(0, 700)
                        await asyncio.sleep(0.1)
                    await self._wait_for_links_beyond(before, 2.5)

                if gained == 0:
                    no_new_streak += 1
                    # Stop only when no new items AND no load-more button for a while
                    # Three empty cycles with no load-more button is the feed
                    # ending. It was six, which with the waits above cost half
                    # a minute of confirming what the third cycle already said.
                    if no_new_streak >= 3 and no_button_streak >= 3:
                        # Two very different situations look identical from here:
                        # the feed genuinely ended, or Divar stopped serving us.
                        # The refusal tally is what tells them apart.
                        if refusals:
                            self._collect_stop = ("refused", dict(refusals))
                            logger.warning(
                                f"[dom] stopped after {sum(refusals.values())} refusal(s) "
                                f"from Divar {refusals} — this is a block, not an empty feed")
                        else:
                            self._collect_stop = ("exhausted", None)
                            logger.info("[dom] no new items & no load-more button — stopping")
                        break
                else:
                    no_new_streak = 0

            if not all_listings:
                try:
                    ss_path = self.images_dir / "debug_collect_zero.png"
                    await self.page.screenshot(path=str(ss_path))
                    logger.warning(f"[dom] Zero results — saved debug screenshot to {ss_path}")
                except Exception:
                    pass

        except Exception as e:
            self._collect_stop = ("error", f"{type(e).__name__}: {e}")
            logger.error(f"[dom] _collect_from_browser_dom failed: {e}")
        finally:
            self.page.remove_listener("response", _on_resp)
            self.page.remove_listener("request", _on_request)

        # A run that never hit any explicit break fell out of the loop bound.
        if self._collect_stop is None:
            self._collect_stop = ("refused", dict(refusals)) if refusals else ("loop-end", None)
        elif refusals and self._collect_stop[0] in ("exhausted", "target"):
            # We finished, but Divar was refusing some of it on the way. The
            # count is short for a reason the caller must be told about.
            self._collect_stop = ("partly-refused", dict(refusals))

        logger.info(f"[dom] collected {len(all_listings)} listings "
                    f"(target={target_count}, stop={self._collect_stop[0]})")
        return all_listings[:target_count]

    @staticmethod
    def _cursor_to_datetime(lpd: Optional[int]) -> Optional[datetime]:
        """The API's last_post_date cursor (epoch in s/ms/µs/ns, or RFC 3339
        text) as a moment in Tehran time — so its .date() is the day the
        person picked, not the server's.

        It was datetime.fromtimestamp(ts): the server's own clock, which is
        UTC in the container, so a cursor at 01:30 Tehran time read as the
        day before and the date walk stopped short of its day.
        """
        from app.services.divar_count import cursor_moment
        try:
            return cursor_moment(lpd)
        except (OverflowError, OSError, ValueError):
            return None

    async def _collect_listings_robust(
        self, city: str, category: str, target_count: int,
        until_day: Optional[date] = None,
    ) -> List[Dict[str, Any]]:
        """Primary collection method: browser DOM extraction with direct API supplement.

        Strategy 1 — browser DOM: navigate the listing page, switch to list view,
        scroll while reading live DOM links + intercepting API responses.
        Strategy 2 — direct API: httpx GET to api.divar.ir with cursor pagination.

        With until_day set (exact-date scraping), target_count is ignored as a
        stop condition: pagination continues until the feed cursor moves past
        that day, so the pool covers every post of the day (safety cap 1500).

        Leaves self._collect_stop saying why the pool ends where it does —
        and only «target» or «exhausted» mean it is everything the run could
        ask for. A later page Divar refused used to be thrown away here and
        the pool declared «exhausted» (#28).
        """
        all_listings: List[Dict[str, Any]] = []
        seen_ids: set = set()
        self._collect_stop = None
        self._feed_more = None

        # Strategy 0: the search API over plain HTTP, no browser.
        #
        # «زمان تا اولین آگهی ۲:۳۰ دقیقه طول کشید و خیلی زیاده.» That time was
        # the browser walking the listing page — a real Chromium scrolling the
        # feed a batch at a time. The same feed is one POST per 24 listings to
        # the API the count already uses, public, sessionless, and back in a
        # couple of seconds for a whole city. So it goes first, and the walk
        # below is kept only for the day the API's shape changes underneath
        # us: an empty answer here falls through to it, with the reason logged.
        form = getattr(self, "_search_form", None)
        if form is not None:
            from app.services import divar_count as _dc
            from app.services import job_log as _jl
            job_id = getattr(self, "_job_id_str", None)

            async def _progress(page, fresh, total):
                logger.info(f"[api] page {page}: +{fresh} → {total}")

            report = _dc.FeedReport()
            listings, err = await _dc.fetch_listings(
                city, form, target=target_count, until_day=until_day,
                on_page=_progress, report=report)
            if listings:
                for lst in listings:
                    if lst['divar_id'] not in seen_ids:
                        seen_ids.add(lst['divar_id'])
                        all_listings.append(lst)
                # How the walk ended, with the listings it did get. A refusal
                # on page 2 is not the end of Divar's list.
                self._collect_stop = self._stop_from_feed(report, len(all_listings))
                if err:
                    logger.warning(f"[robust] API-first stopped short: {err}")
                # The way back into the feed, for a pool that runs dry before
                # the run's target (#30): where the next page starts, and the
                # rows the last page carried past the target. Not after a
                # refusal — the run says it is short instead of asking again.
                if until_day is None and report.stop in (_dc.STOP_TARGET, _dc.STOP_END) \
                        and (report.cursor or report.leftover):
                    self._feed_more = {"city": city, "form": form, "cursor": report.cursor,
                                       "page": report.last_page, "leftover": list(report.leftover)}
                logger.info(f"[robust] API-first: {len(all_listings)} listings "
                            f"(stop={report.stop}, pages={report.last_page}), no browser walk")
                if job_id:
                    await _jl.record(job_id, _jl.PAGE,
                                     f"{len(all_listings)} آگهی از API دیوار جمع شد — بدون پیمایش مرورگر",
                                     collected=len(all_listings), via="api",
                                     pages=report.last_page, stop=report.stop)
                return all_listings if until_day is not None else all_listings[:target_count]
            logger.warning(f"[robust] API-first returned nothing ({err or 'empty'}) — "
                           "falling back to the browser walk")
            if job_id:
                await _jl.record(job_id, _jl.PAGE,
                                 f"API دیوار آگهی نداد ({err or 'خالی'}) — به پیمایش مرورگر برمی‌گردیم",
                                 level="warning", page=report.page, status=report.status,
                                 divar_message=report.divar_message)

        # Strategy 1: live DOM extraction (independent of API response format).
        # Bounded on purpose: this phase scrolls a real browser and its cost
        # grows with the target (max_scrolls = target/2), while the API phase
        # below pages through the same feed over plain HTTP. Depth is the API's
        # job; the DOM is here because it does not depend on a response shape.
        dom_target = min(target_count, self.DOM_COLLECT_CAP)
        try:
            dom_listings = await self._collect_from_browser_dom(city, category, dom_target)
            for lst in dom_listings:
                if lst['divar_id'] not in seen_ids:
                    seen_ids.add(lst['divar_id'])
                    all_listings.append(lst)
            logger.info(f"[robust] DOM strategy: {len(all_listings)}/{dom_target} (pool target {target_count})")
        except Exception as e:
            logger.error(f"[robust] DOM strategy failed: {e}")
            if self._collect_stop is None:
                self._collect_stop = ("error", f"{type(e).__name__}: {e}")

        if until_day is None and len(all_listings) >= target_count:
            self._collect_stop = ("target", None)
            return all_listings[:target_count]

        # What the browser walk said, before the replay below has its say.
        dom_stop = self._collect_stop or ("unknown", None)
        walked = len(all_listings)
        # Why the replay stopped: "target", "day", "end" (Divar said there is
        # no next page), "cap", "refused", "error", or — two pages with
        # nothing new — "stale" when it could replay and "no-replay" when no
        # search request was ever captured to replay.
        replay_stop: str = "cap"
        replay_detail: Dict[str, Any] = {}

        # Strategy 2: direct API with cursor pagination
        remaining = max(target_count - len(all_listings), 0)
        # Start where the browser left off, rather than from nothing.
        last_post_date: Optional[int] = self._dom_cursor
        consecutive_empty = 0
        max_pages = 75 if until_day else max(8, (remaining // 20) + 3)
        for page_num in range(1, max_pages + 1):
            try:
                batch, lpd = await self._fetch_listings_direct_api(
                    city, category, page_num, last_post_date
                )
                new_count = 0
                for lst in batch:
                    if lst['divar_id'] not in seen_ids:
                        seen_ids.add(lst['divar_id'])
                        all_listings.append(lst)
                        new_count += 1
                logger.info(
                    f"[robust] API page={page_num} got={len(batch)} "
                    f"new={new_count} total={len(all_listings)}"
                    + ("" if until_day else f"/{target_count}")
                )
                if lpd:
                    last_post_date = lpd
                if until_day:
                    if len(all_listings) >= 1500:
                        logger.info("[robust] date-mode safety cap (1500) reached")
                        replay_stop, replay_detail = "cap", {"page": page_num, "listings": 1500}
                        break
                    cursor_dt = self._cursor_to_datetime(last_post_date)
                    if cursor_dt and cursor_dt.date() < until_day:
                        logger.info(
                            f"[robust] feed cursor {cursor_dt} moved past "
                            f"{until_day} — day fully covered"
                        )
                        replay_stop = "day"
                        break
                elif len(all_listings) >= target_count:
                    replay_stop = "target"
                    break
                if getattr(self, "_replay_more", None) is False:
                    logger.info("[robust] Divar says there is no next page — the list ended")
                    replay_stop = "end"
                    break
                # Count pages that added NOTHING NEW, not pages that came back
                # empty. While the replay was dead every batch was empty and
                # this worked by accident; with it alive, a stuck cursor returns
                # the same non-empty page forever, new_count stays 0, and the
                # loop would burn all 75 pages re-fetching one page of results.
                if new_count == 0:
                    refused = getattr(self, "_replay_status", None)
                    if refused and refused != 200:
                        # Divar said no. Asking again at once is the pace
                        # that was just refused; the pool ends here, and says so.
                        replay_stop, replay_detail = "refused", {"page": page_num, "status": refused}
                        break
                    consecutive_empty += 1
                    if consecutive_empty >= 2:
                        logger.info(
                            f"[robust] two pages with nothing new (cursor "
                            f"{last_post_date}) — stopping")
                        replay_stop = ("stale" if getattr(self, "_search_req_template", None)
                                       else "no-replay")
                        replay_detail = {"page": page_num}
                        break
                else:
                    consecutive_empty = 0
                await asyncio.sleep(random.uniform(0.8, 1.5))
            except Exception as e:
                logger.error(f"[robust] API page={page_num} failed: {e}")
                replay_stop, replay_detail = "error", {"page": page_num,
                                                       "error": f"{type(e).__name__}: {e}"}
                break
        else:
            replay_stop, replay_detail = "cap", {"page": max_pages}

        self._collect_stop = self._settle_stop(dom_stop, replay_stop, replay_detail,
                                               grew=len(all_listings) > walked)
        return all_listings if until_day else all_listings[:target_count]

    @staticmethod
    def _stop_from_feed(report, collected: int) -> tuple:
        """The search API's own account of how its walk ended, as _collect_stop.

        «target» and «exhausted» (Divar said there is no next page, or the
        cursor moved past the day) are a complete pool. A refused page is
        «partly-refused», no answer at all «error», and a stuck cursor or the
        page cap «cut-short» — each with the page, the status and Divar's
        own words, for the run's log and its finish line.
        """
        from app.services import divar_count as _dc
        if report.stop == _dc.STOP_TARGET:
            return ("target", None)
        if report.stop in (_dc.STOP_END, _dc.STOP_DAY):
            return ("exhausted", None)
        detail = {"via": "api", "why": report.stop, "page": report.page,
                  "status": report.status, "divar_message": report.divar_message,
                  "sentence": report.sentence, "collected": collected}
        if report.stop == _dc.STOP_ERROR:
            return ("partly-refused" if report.status else "error", detail)
        return ("cut-short", detail)

    @staticmethod
    def _settle_stop(dom_stop: tuple, replay_stop: str, replay_detail: dict, *,
                     grew: bool) -> tuple:
        """One verdict from the browser walk and the replay that follows it.

        The replay reaching the target or moving past the day settles it. A
        walk Divar refused or that crashed keeps its own verdict, and so does
        one that saw the end of the list when the replay found nothing past
        it. Otherwise the walk stopped at its own cap and the replay could not
        page on to the end — whatever it ran into is why the pool is short.
        """
        if replay_stop == "target":
            return ("target", None)
        if replay_stop in ("day", "end"):
            return ("exhausted", None)
        kind = dom_stop[0]
        if kind in ("refused", "partly-refused", "error"):
            return dom_stop
        if kind == "exhausted" and not grew:
            return dom_stop
        detail = {"via": "replay", "why": replay_stop, **replay_detail}
        page, status = replay_detail.get("page"), replay_detail.get("status")
        if replay_stop == "refused":
            detail["sentence"] = (f"دیوار صفحهٔ {page} جست‌وجو (پس از پیمایش مرورگر) "
                                  f"را رد کرد (HTTP {status})")
            return ("partly-refused", detail)
        if replay_stop == "error":
            detail["sentence"] = (f"خواندن صفحهٔ {page} جست‌وجو (پس از پیمایش مرورگر) "
                                  f"با خطا متوقف شد ({replay_detail.get('error')})")
            return ("error", detail)
        return ("cut-short", detail)

    # A collection that ended on one of these did not reach the end of
    # Divar's list: the run walks what it has and ends «ناقص» (#28) — unless
    # it met its target anyway. "refused" is not here: a walk Divar stopped
    # dead fails the run, as it always has.
    _CUT_SHORT = ("partly-refused", "error", "cut-short", "loop-end", "unknown")

    @staticmethod
    def _what_cut_collection(stop: str, detail: Any, pool: int) -> str:
        """The first sentence of a partial run's reason: what stopped the
        collection, with the page, Divar's status and its own words."""
        d = detail if isinstance(detail, dict) else {}
        if d.get("sentence"):
            return d["sentence"]
        if stop == "partly-refused":    # the browser walk's tally of refusals
            counts = ", ".join(f"HTTP {k}×{v}" for k, v in sorted(d.items()))
            return f"دیوار در حین پیمایش فهرست بخشی از درخواست‌ها را رد کرد ({counts})"
        if stop == "error":
            return f"جمع‌آوری فهرست با خطا متوقف شد ({detail})"
        why, page = d.get("why"), d.get("page")
        if why == "stuck":
            return (f"دیوار صفحهٔ {page} جست‌وجو را بدون آگهی تازه برگرداند، "
                    "با این‌که گفت صفحهٔ بعدی هست")
        if why == "cap" and d.get("listings"):
            return (f"جمع‌آوری به سقف {d['listings']} نامزدِ هر اجرا رسید و آن روز "
                    "هنوز تمام نشده بود")
        if why == "cap":
            return f"جمع‌آوری به سقف {page} صفحهٔ جست‌وجو رسید و فهرست دیوار هنوز ادامه داشت"
        if why == "no-replay":
            return ("پیمایش مرورگر به سقف خودش رسید و راهی برای خواندن صفحه‌های "
                    "بعدی فهرست نبود")
        if why == "stale":
            return "صفحه‌های بعدی فهرست چیز تازه‌ای نیاوردند و جمع‌آوری پیش از ته فهرست ماند"
        if stop == "target":
            return (f"ظرفیت جست‌وجوی این اجرا ({pool} نامزد) پر شد و فهرست دیوار "
                    "هنوز ادامه داشت")
        return "جمع‌آوری فهرست پیش از رسیدن به ته فهرست دیوار ماند"

    def _collection_shortfall(self, category: str, *, collected: int,
                              divar_total: Optional[int], pool: int,
                              saved: int = 0, asked: Optional[int] = None) -> Optional[str]:
        """Why this run's candidate pool is not everything it could have
        walked, in the user's words and with what to do — or None when it is:
        the end of Divar's list, the day covered, an explicit list.

        «target» is here too: a pool that filled its capacity without the run
        meeting its target stopped short of Divar's list for our own reason.
        """
        from app.services import divar_count as _dc
        stop, detail = self._collect_stop or ("unknown", None)
        if stop not in self._CUT_SHORT and stop != "target":
            return None
        what = self._what_cut_collection(stop, detail, pool).rstrip(".")
        got = (f"{collected} نامزد" + (f" از {divar_total} آگهیِ دیوار" if divar_total else "")
               + " جمع شد"
               + (f" و {saved} آگهی تازه از {asked} درخواستی ذخیره شد." if asked else "."))
        d = detail if isinstance(detail, dict) else {}
        status = d.get("status")
        if stop == "partly-refused" and not status and d:
            # the browser walk's tally: advise on what Divar said most
            top = max(d.items(), key=lambda kv: kv[1])[0]
            status = int(top) if str(top).isdigit() else None
        if status or stop == "error":
            name = CATEGORIES.get(category, {}).get("name")
            advice = _dc.refusal_advice(status, d.get("divar_message"), name)
        else:
            advice = "«ادامه» را بزنید تا بقیهٔ فهرست خوانده شود."
        return f"{what}. {got} {advice}"

    async def _record_cut_short(self, job_id, stop: str, detail: Any, *, collected: int,
                                pool: int, asked: Optional[int],
                                opening: str = "جمع‌آوری ناقص ماند") -> None:
        """One line in the run's log for a collection that stopped short with
        listings in hand: what stopped it — the page, Divar's status, its own
        words — and that the run walks what it has and ends «ناقص» (#28)."""
        from app.services import job_log
        d = detail if isinstance(detail, dict) else {}
        what = self._what_cut_collection(stop, detail, pool).rstrip(".")
        msg = (f"{opening}: {what}. {collected} نامزد تا آن‌جا جمع شد و همین‌ها بررسی "
               "می‌شوند؛ " + ("اگر به تعداد درخواستی نرسد، " if asked else "")
               + "اسکرپ «ناقص» تمام می‌شود.")
        # The browser walk's tally ({"429": 3}) has no "via": those are
        # refusals by definition. Divar pushing back on us is a CHALLENGE; a
        # search it rejected or never answered, an ERROR.
        tally = stop == "partly-refused" and not d.get("via")
        level = "error" if stop in ("partly-refused", "error") else "warning"
        logger.log(level.upper(), f"[collect] {msg}")
        await job_log.record(
            job_id,
            job_log.CHALLENGE if (tally or d.get("status") in (401, 403, 429)) else (
                job_log.ERROR if level == "error" else job_log.PAGE),
            msg, level=level, collected=collected, target=pool, stop=stop,
            page=d.get("page"), status=d.get("status"), divar_message=d.get("divar_message"),
            refusals=detail if tally else None)

    async def _top_up_pool(self, pool: List[Dict[str, Any]], seen: set, want: int,
                           asked: Optional[int] = None) -> int:
        """Page on into Divar's search from where the collection stopped, for
        up to `want` more candidates onto the end of `pool` (#30).

        The pool starts at twice the target plus a page. In a big city whose
        filters Divar cannot apply itself — rooms, the amenity boxes, price
        per metre, the advertiser check that drops «unknown» — or whose first
        pages earlier runs already saved, that runs dry long before the
        target, and the run ended «آگهی بیشتری پیدا نشد» («Ran out of
        candidates») with Divar holding thousands more. Topped up here it
        goes on until the target is met, Divar's list really ends, or
        POOL_CEILING. Never raises; returns how many it added.
        """
        more = getattr(self, "_feed_more", None)
        room = self.POOL_CEILING - len(pool)
        if not more or want <= 0 or room <= 0:
            return 0
        want = min(want, room)
        added = 0
        # First the rows the last page carried past the target: fetched, never walked.
        leftover, more["leftover"] = list(more.get("leftover") or []), []
        for n, row in enumerate(leftover):
            if added >= want:
                more["leftover"] = leftover[n:]
                break
            if row["divar_id"] not in seen:
                seen.add(row["divar_id"])
                pool.append(row)
                added += 1
        if added >= want or not more.get("cursor"):
            if not more.get("cursor") and not more["leftover"]:
                self._feed_more = None
            return added

        from app.services import divar_count as _dc
        report = _dc.FeedReport()
        first = int(more.get("page") or 0) + 1
        try:
            rows, _err = await _dc.fetch_listings(
                more["city"], more["form"], target=want - added, after=more["cursor"],
                first_page=first, exclude=seen, report=report)
        except Exception as e:           # it never raises; a top-up must never cost the run
            logger.warning(f"[collect] could not top the pool up: {type(e).__name__}: {e}")
            return added
        for row in rows:
            if row["divar_id"] not in seen:
                seen.add(row["divar_id"])
                pool.append(row)
                added += 1
        if report.stop == _dc.STOP_TARGET:
            more.update(cursor=report.cursor, page=report.last_page,
                        leftover=list(report.leftover))
        else:
            # The list ended, or the walk stopped short: nothing more to page
            # on from here, and what stopped it is now why the pool ends.
            self._feed_more = ({**more, "cursor": None, "leftover": list(report.leftover)}
                               if report.leftover else None)
        self._collect_stop = self._stop_from_feed(report, len(pool))
        logger.info(f"[collect] topped the pool up by {added} (pages {first}–{report.last_page}, "
                    f"stop={report.stop}) → {len(pool)}")
        job_id = getattr(self, "_job_id_str", None)
        if job_id:
            from app.services import job_log
            if added:
                await job_log.record(
                    job_id, job_log.PAGE,
                    f"{added} نامزد دیگر از دیوار گرفته شد تا به تعداد درخواستی برسیم "
                    f"(صفحهٔ {first} تا {max(report.last_page, first)}) — روی هم {len(pool)} نامزد",
                    collected=len(pool), added=added, via="api", stop=report.stop)
            if self._collect_stop[0] in self._CUT_SHORT:
                await self._record_cut_short(job_id, self._collect_stop[0], self._collect_stop[1],
                                             collected=len(pool), pool=len(pool), asked=asked,
                                             opening="ادامهٔ جمع‌آوری ناقص ماند")
        return added

    def _parse_api_response(self, data: dict) -> tuple:
        """Parse Divar API JSON response (handles multiple known response shapes).

        Returns (listings, last_post_date) where last_post_date is an int Unix
        timestamp used as the cursor for the next page, or None if unavailable.
        """
        listings: List[Dict[str, Any]] = []
        last_post_date: Optional[int] = None

        if not isinstance(data, dict):
            return listings, last_post_date

        # Extract pagination cursor. The modern /postlist/w/search response nests
        # it at pagination.data.last_post_date; older shapes put it at the top
        # level or under meta. Check all known spots, then deep-scan as a fallback.
        def _as_int(v):
            try:
                return int(v)
            except (TypeError, ValueError):
                return None

        pagination = data.get('pagination') or {}
        raw_lpd = (
            _as_int(data.get('last_post_date'))
            or _as_int(pagination.get('last_post_date'))
            or _as_int((pagination.get('data') or {}).get('last_post_date'))
            or _as_int((data.get('meta') or {}).get('last_post_date'))
        )
        if raw_lpd is None:
            # Deep scan: find the first last_post_date anywhere in the response
            def _deep_find(obj):
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        if k == 'last_post_date':
                            iv = _as_int(v)
                            if iv:
                                return iv
                        found = _deep_find(v)
                        if found:
                            return found
                elif isinstance(obj, list):
                    for it in obj:
                        found = _deep_find(it)
                        if found:
                            return found
                return None
            raw_lpd = _deep_find(data)
        if raw_lpd:
            last_post_date = raw_lpd

        widget_list = (
            data.get('list_widgets')
            or data.get('widget_list')
            or data.get('items')
            or data.get('action_list')
            or data.get('listing_list')
            or []
        )
        if not widget_list:
            logger.warning(f"[parse_api] no widget_list — top_keys={list(data.keys())[:12]}")
            # Flat structure with direct token list
            if data.get('token'):
                token = data['token']
                listings.append({
                    'url': f"https://divar.ir/v/{token}",
                    'divar_id': token,
                    'title': data.get('title'),
                    'descriptions': [data.get('description', '')],
                })
            return listings, last_post_date

        if widget_list:
            w0 = widget_list[0] if isinstance(widget_list[0], dict) else {}
            wd0 = w0.get('data', {})
            logger.info(
                f"widget_list[0] type={w0.get('widget_type')} "
                f"data_keys={list(wd0.keys())[:8]}"
            )

        for widget in widget_list:
            try:
                if not isinstance(widget, dict):
                    continue
                widget_data = widget.get('data', widget)

                # Divar API v8: token lives inside action.payload.token
                action = widget_data.get('action') or {}
                if isinstance(action, dict):
                    payload = action.get('payload') or {}
                else:
                    payload = {}

                token = (
                    widget_data.get('token')
                    or payload.get('token')
                    or widget_data.get('header_action', {}).get('payload', {}).get('token')
                    or widget_data.get('action_log', {}).get('token')
                )
                # Fallback: extract token from web_url in action payload
                if not token:
                    web_url = payload.get('web_url', '') or widget_data.get('web_url', '')
                    if web_url and '/v/' in web_url:
                        parts = web_url.split('/v/', 1)[1].split('?')[0].rstrip('/').split('/')
                        candidate = parts[-1]
                        if re.match(r'^[A-Za-z0-9]{4,20}$', candidate):
                            token = candidate
                if not token:
                    continue

                # Fallback cursor from a widget's own date — only when pagination
                # didn't supply one (never override the authoritative cursor above).
                if last_post_date is None:
                    sort_date = (
                        widget_data.get('sort_date')
                        or widget_data.get('date')
                        or widget_data.get('created_at')
                    )
                    if sort_date:
                        try:
                            last_post_date = int(sort_date)
                        except (TypeError, ValueError):
                            pass

                listing_url = f"https://divar.ir/v/{token}"
                listings.append({
                    'url': listing_url,
                    'divar_id': token,
                    'title': widget_data.get('title') or widget_data.get('header_description'),
                    'descriptions': [
                        widget_data.get('top_description_text', ''),
                        widget_data.get('bottom_description_text', ''),
                    ],
                    'thumbnail_url': widget_data.get('image_url'),
                    'category_hint': widget_data.get('bottom_description_text'),
                })
            except Exception as e:
                logger.debug(f"Failed to parse API widget: {e}")

        return listings, last_post_date
    
    @staticmethod
    def _date_skip(posted: Optional[datetime], target_day, max_age_hours) -> Optional[str]:
        """Why the publish-date filters drop an ad, or None.

        The exact-day filter compares TEHRAN days — the day the person picked
        in the panel. posted_at is UTC, and an ad posted at 01:00 Tehran time
        is still the previous day in UTC.
        """
        if target_day:
            if not posted:
                return "posted_at unknown; date filter active"
            from app.scraper.parsers import TEHRAN_OFFSET
            day = (posted + TEHRAN_OFFSET).date()
            if day > target_day:
                return f"posted {day} is after {target_day}"
            if day < target_day:
                return f"posted {day} is before {target_day}"
            return None
        if max_age_hours and posted and posted < datetime.now() - timedelta(hours=max_age_hours):
            return f"posted_at {posted} older than {max_age_hours}h"
        return None

    def pre_contact_skip(self, detail: Dict[str, Any], listing_type: str,
                         f: Dict[str, Any]) -> Optional[str]:
        """Why this ad would be dropped, judged from the page alone.

        Only filters that need nothing beyond what the ad page already gave us.
        The phone is deliberately not among them: deciding this *before* asking
        for it is the whole point. The full filter set still runs afterwards in
        the scrape loop and remains the authority — this only avoids paying for
        an answer we are going to discard.
        """
        # The date first: on 1405/07/04 a date-filtered run revealed 26
        # numbers and kept none of them, and those reveals are what brought
        # Divar's code prompts.
        why = self._date_skip(detail.get("posted_at"), f.get("target_day"),
                              f.get("max_age_hours"))
        if why:
            return why

        # …then the same judgement the scrape loop makes once the ad is
        # saved-or-not. It is one function on purpose: this was a second copy,
        # and the two disagreed (a deposit of 0, an unnamed category).
        return self.local_filter_skip(detail, listing_type, f)

    @staticmethod
    def local_filter_skip(detail: Dict[str, Any], listing_type: str,
                          f: Dict[str, Any]) -> Optional[str]:
        """Why the run's own filters drop this ad, or None. Judged from what the
        ad page gave up — no phone, no date.

        Divar applies most of these itself (rooms, amenities, price per metre,
        …), so this is a safety net for what it lets through. It is also the one
        place that decides, for `pre_contact_skip` (before a reveal is spent)
        and for the scrape loop (which keeps or drops the row), so the two can
        never disagree about the same ad.

        A figure the ad does not state does not fail a band: missing is not the
        same as out of range. A figure of 0 is one it does state — a deposit of
        «مجانی», an ad with no rooms — except for prices and areas, where 0 is
        how a blank reads. An amenity the page never mentions counts as absent,
        which is how Divar prints it. The reason's first word is the bucket the
        run tallies it under, and every one has a name in _FILTER_LABELS_FA.
        """
        def band(label, value, lo, hi, *, zero_value=False, zero_bound=False):
            if value is None or (value == 0 and not zero_value):
                return None
            if not zero_bound:
                lo, hi = lo or None, hi or None
            if lo is not None and value < lo:
                return f"{label} {value} < min {lo}"
            if hi is not None and value > hi:
                return f"{label} {value} > max {hi}"
            return None

        if listing_type == "buy":
            why = (band("price", detail.get("total_price") or detail.get("price"),
                        f.get("min_price"), f.get("max_price"))
                   or band("price/m²", detail.get("price_per_meter"),
                           f.get("min_price_per_meter"), f.get("max_price_per_meter")))
        elif listing_type == "rent":
            why = (band("deposit", detail.get("deposit"),
                        f.get("min_deposit"), f.get("max_deposit"), zero_value=True)
                   or band("rent", detail.get("rent_price"),
                           f.get("min_rent"), f.get("max_rent"), zero_value=True))
        else:
            why = None
        why = (why
               or band("area", detail.get("area"), f.get("min_area"), f.get("max_area"))
               or band("rooms", detail.get("rooms"), f.get("min_rooms"), f.get("max_rooms"),
                       zero_value=True, zero_bound=True))
        if why:
            return why

        for key in ("has_images", "has_elevator", "has_parking", "has_storage", "has_balcony"):
            wanted = f.get(key)
            if wanted is None:
                continue
            actual = bool(detail.get(key) or (key == "has_images" and detail.get("images")))
            if wanted and not actual:
                return f"{key} required but not present"
            if not wanted and actual:
                return f"{key} must be absent"

        adv = f.get("advertiser_type")
        if adv:
            actual_type = detail.get("advertiser_type")
            if not actual_type:
                return f"advertiser_type unknown; {adv} filter active"
            if actual_type != adv:
                return f"advertiser_type {actual_type} != {adv}"
        return None

    # What an ad's own words look like when it is real estate. Used twice: as
    # a hint on the URL, and as the verdict on Divar's breadcrumb.
    REAL_ESTATE_URL_KEYWORDS = [
        'خرید', 'اجاره', 'اجارهٔ', 'رهن', 'فروش', 'مسکن', 'ملک', 'املاک',
        'آپارتمان', 'اپارتمان', 'خانه', 'ساختمان', 'زمین', 'کلنگی',
        'ویلا', 'سوئیت', 'واحد', 'مغازه', 'دفتر', 'انبار', 'باغ', 'حیاط',
        'buy', 'rent', 'residential', 'apartment', 'villa',
    ]

    # A listing Divar no longer has: deleted by its poster, expired, or never
    # there. Its page answers 410 Gone and says so in words, «این صفحه حذف
    # شده یا وجود ندارد» as served and «در پایین، آگهی‌های مشابه با آگهی حذف
    # شده را ببینید.» once the browser has put other people's ads under it.
    GONE_FROM_DIVAR = "در دیوار حذف شده"
    GONE_MARKERS = ("صفحه حذف شده", "آگهی حذف شده")

    async def scrape_property_detail(
        self, url: str, target_category: Optional[str] = None,
        source_title: Optional[str] = None,
        wants_contact=None,
    ) -> Optional[Dict[str, Any]]:
        """Scrape detailed information from a property page.

        target_category: the category the run searched. Divar's own breadcrumb
        on the page is the only thing that can say the listing is elsewhere
        (app/scraper/divar_categories.py), and then it returns False.
        source_title: the listing title captured from the search results, for
        the log line of a listing left out.
        """
        self._last_detail_error = None
        try:
            logger.info(f"Scraping property detail: {url}")

            await self._check_rate_limit()
            assert self.page is not None   # opened by initialize(); a None lands in the except below
            response = await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)

            # If Divar redirected to a CAPTCHA or home page, skip this property
            actual_url = self.page.url
            if '/v/' not in actual_url:
                logger.warning(f"Detail page redirected away from property: {url} → {actual_url}, skipping")
                self._last_detail_error = "صفحه باز نشد"
                return None

            # Gone from Divar. Everything past this point would have worked on
            # the similar ads Divar shows in its place: job 076c865a looked for
            # a contact button that was not there, counted a reveal for it,
            # downloaded twenty of those ads' photos, and then filed the
            # listing as «عنوان نبود». Stop before any of it.
            if getattr(response, "status", None) == 410:
                logger.info(f"{url}: Divar answered 410 — the listing is gone")
                self._last_detail_error = self.GONE_FROM_DIVAR
                return None

            from urllib.parse import unquote
            decoded_url = unquote(actual_url)

            # ── The run's category: Divar's own breadcrumb decides, below ──
            #
            # The run searched Divar with this category's token, so Divar has
            # already filed the listing there. Words of ours in its URL, its
            # title or the tab's title («کلنگی», «دفتر», «صنعتی») used to decide
            # whether to believe that, and every word a list lacked — a plot
            # titled «زمین ۲۰۰ متری», a workshop, a short-term villa — sent the
            # listing to a keyword test of its breadcrumb's last crumb, which a
            # neighbourhood or Divar's short menu name failed too (#57). Only
            # the breadcrumb, read as a place in Divar's tree, can say it is
            # elsewhere; nothing is asked of the URL or the title any more.
            category_known = divar_categories.known(target_category)
            kind_unconfirmed = False
            if not category_known:
                # Fallback broad check when no category is known — «اسکرپ تکی»
                # and «بازاسکرپ», where the caller names the URL and there is
                # no search category to match against.
                #
                # This used to drop on the URL alone, and the URL is the ad's
                # own title: «حیاط راه جدا قرنطینه» names no property word, so
                # a listing Divar itself files under «اجاره ویلا» — one already
                # in our own database, with a deposit and a rent — came back
                # «ملک نبود» on every single scrape and every re-scrape of the
                # rows whose number we still owe. The known-category branch
                # above learned this already: an absence is not a denial.
                #
                # So carry the doubt to the breadcrumb, the same way, and let
                # the two answers that really know decide — Divar's own
                # category, and the fact that we have this listing already.
                if not any(kw in decoded_url for kw in self.REAL_ESTATE_URL_KEYWORDS):
                    logger.info(
                        f"No property word in the URL ({decoded_url}) — deferring to "
                        f"Divar's breadcrumb instead of dropping")
                    kind_unconfirmed = True

            await asyncio.sleep(0.6)
            # Wait for property specs to be rendered by React (fires as soon as
            # they appear, so a lower cap only matters on missing/slow pages)
            try:
                await self.page.wait_for_selector(
                    '.kt-group-row-item, .kt-unexpandable-row, .kt-base-row',
                    timeout=4000
                )
            except Exception:
                pass
            try:
                await self.page.wait_for_selector(
                    '[class*="description-row__text"], .kt-description-row',
                    timeout=1500
                )
            except Exception:
                pass

            # The same page, said in words, for when the status did not say
            # 410. Asked only of a page without an h1: every listing has its
            # title there and this page has none, so an ad whose description
            # merely mentions «آگهی حذف شده» is never taken for one. Read from
            # the text the page shows, not its HTML: the inline state script
            # carries a live ad's description before React has drawn its h1.
            if not await self.page.query_selector("h1"):
                shown = BeautifulSoup(await self.page.content(), "lxml").get_text(" ")
                shown = shown.replace("\u200c", " ")   # «حذف‌شده»
                if any(m in shown for m in self.GONE_MARKERS):
                    logger.info(f"{url}: the page says the listing is gone")
                    self._last_detail_error = self.GONE_FROM_DIVAR
                    return None

            await self._simulate_scroll()
            await asyncio.sleep(0.3)

            # Click "Show all details" button if it exists
            await self._click_show_all_details()

            # Expand description "بیشتر" button if present
            try:
                await self.page.evaluate("""() => {
                    const btns = Array.from(document.querySelectorAll(
                        '.kt-description-row button, [class*="description-row"] button, [class*="description"] button[class*="more"]'
                    ));
                    for (const btn of btns) {
                        const t = (btn.innerText || '').trim();
                        if (t.includes('بیشتر') || t.includes('ادامه') || t.includes('نمایش')) {
                            btn.click();
                        }
                    }
                }""")
                await asyncio.sleep(0.5)
            except Exception:
                pass

            # Get page content
            content = await self.page.content()
            soup = BeautifulSoup(content, 'lxml')

            property_data = {
                "url": url,
                "divar_id": self._extract_divar_id(url),
                "scraped_at": datetime.now()
            }

            # Extract title - use specific Divar selector
            title_elem = soup.select_one('h1.kt-page-title__title.kt-page-title__title--responsive-sized, h1.kt-page-title__title, h1')
            if title_elem:
                property_data["title"] = title_elem.get_text(strip=True)

            # DEBUG: log all elements with "description" in class to help find the right selector
            try:
                debug_elems = await self.page.evaluate("""() => {
                    const out = [];
                    document.querySelectorAll('[class*="description"]').forEach(el => {
                        out.push(el.tagName + '.' + el.className.split(' ').join('.') + ' => ' + el.innerText.trim().substring(0, 80));
                    });
                    return out;
                }""")
                for line in (debug_elems or []):
                    logger.info(f"[desc-debug] {line}")
            except Exception:
                pass

            # Extract description via Playwright JS (rendered DOM — more reliable than BeautifulSoup)
            try:
                raw_desc = await self.page.evaluate("""() => {
                    const BAD = ['موردی برای نمایش', 'انتشار آگهی'];

                    // Divar puts description in p.kt-description-row__text--primary
                    // but also uses that class for the publish date (which has --small too).
                    // Loop ALL matching <p> elements, skip --small and skip placeholder text.
                    const paras = document.querySelectorAll(
                        'p[class*="description-row__text--primary"]:not([class*="description-row__text--small"])'
                    );
                    for (const p of paras) {
                        const text = p.innerText.trim();
                        if (text.length < 10) continue;
                        if (BAD.some(b => text.includes(b))) continue;
                        return text;
                    }
                    return null;
                }""")
                if raw_desc and 'موردی برای نمایش' not in raw_desc:
                    property_data["description"] = raw_desc
                    logger.info(f"Description extracted ({len(raw_desc)} chars)")
                else:
                    logger.info("No description found on page")
            except Exception as desc_err:
                logger.debug(f"JS description extraction failed: {desc_err}")
                desc_elem = (
                    soup.select_one('p.kt-description-row__text') or
                    soup.select_one('[class*="description-row__text"]') or
                    soup.select_one('.kt-description-row p') or
                    soup.select_one('.kt-description-row .kt-body')
                )
                if desc_elem:
                    raw = desc_elem.get_text(separator='\n').strip()
                    if raw and 'موردی برای نمایش' not in raw and len(raw) > 10:
                        property_data["description"] = raw
            
            # Extract price info
            property_data.update(_parse_price_info(soup))
            
            # Extract property details
            property_data.update(_parse_property_details(soup, property_data.get("title", "")))
            
            # Extract location
            property_data.update(self._extract_location(soup))
            
            # Extract amenities/features
            property_data["features"] = self._extract_features(soup)
            property_data["amenities"] = self._extract_amenities(soup)

            # نبش has no Divar field — recover it from the ad's own prose
            if not property_data.get("corner_type"):
                corner = _detect_corner(
                    property_data.get("title"),
                    property_data.get("description"),
                    " ".join(property_data.get("features") or []),
                )
                if corner:
                    property_data["corner_type"] = corner


            # Extract images (use Playwright JS to capture all gallery slides)
            property_data["images"] = await self._extract_images_from_page()
            if not property_data["images"]:
                property_data["images"] = self._extract_images(soup)
            
            # Set has_images flag if images were found
            if property_data.get("images"):
                property_data["has_images"] = True
            
            # Extract advertiser type and posting time
            advertiser_type, agency_name = await self._extract_advertiser_type()
            if advertiser_type:
                property_data["advertiser_type"] = advertiser_type
            if agency_name:
                # nothing populated seller_name for scraped ads, so the CRM
                # column, the matcher and the share card all had an empty field
                property_data["seller_name"] = agency_name[:200]

            posted_at = await self._extract_posted_at()
            if posted_at:
                property_data["posted_at"] = posted_at

            # Category / property_type / listing_type from the breadcrumb trail
            # (e.g. املاک › فروش مسکونی › فروش آپارتمان). The leaf crumb gives the
            # category; stripping its leading transaction word gives the property
            # type, and the transaction word itself is the authoritative
            # buy/rent signal.
            all_crumbs: List[str] = []
            where: Optional[str] = None
            try:
                import re as _re
                all_crumbs = [a.get_text(strip=True) for a in soup.select('a.kt-breadcrumbs__action')]
                all_crumbs = [c for c in all_crumbs if c]
                # Where Divar filed it: the deepest crumb that names one of its
                # categories. The crumbs after it (a neighbourhood, a finer
                # sub-category) and before it (the city) are not categories.
                where, said = divar_categories.place(all_crumbs)
                crumbs = [c for c in all_crumbs if c != 'املاک']
                if crumbs:
                    leaf = said if where not in (None, divar_categories.ROOT) else crumbs[-1]
                    property_data.setdefault('category_name', leaf)
                    ptype = _re.sub(r'^(پیش[‌ ]?فروش|فروش|اجارهٔ|اجاره|رهن|خرید)\s+', '', leaf).strip()
                    if ptype and ptype != leaf:
                        property_data.setdefault('property_type', ptype)
                    kind = divar_categories.listing_type(where)
                    if kind is None:
                        # A breadcrumb we cannot place: its own words, as
                        # before — the crumbs after «املاک», not the city's.
                        after = all_crumbs[all_crumbs.index('املاک') + 1:] if 'املاک' in all_crumbs else crumbs
                        joined = ' '.join(after)
                        if 'اجاره' in joined or 'رهن' in joined:
                            kind = 'rent'
                        elif 'فروش' in joined or 'خرید' in joined:
                            kind = 'buy'
                    if kind:
                        property_data['listing_type'] = kind
            except Exception:
                pass

            # Divar's own answer, now that the page has been parsed.
            #
            # This is where the doubt raised above is settled. The breadcrumb
            # is Divar's own words for what this ad is («املاک › اجاره مسکونی ›
            # اجاره آپارتمان»), so it is worth more than any keyword we could
            # look for in a title. Placed before the contact reveal on purpose:
            # a reveal costs the account an SMS and a listing about to be
            # dropped must not spend one.
            if kind_unconfirmed:
                # Divar's breadcrumb is the answer the URL could not give. It
                # names a property category («اجاره ویلا», «اجاره مغازه») for
                # anything we want; a car or a phone says something else. No
                # breadcrumb at all keeps the listing: the caller pasted this
                # URL on purpose, and the price fields below still have a say.
                leaf = f"{property_data.get('category_name') or ''} {property_data.get('property_type') or ''}".strip()
                if leaf and not any(kw in leaf for kw in self.REAL_ESTATE_URL_KEYWORDS):
                    logger.info(f"Divar's breadcrumb says {leaf!r} — not real estate, skipping")
                    self._last_detail_error = "ملک نبود"
                    return None
                logger.info(
                    f"Breadcrumb {leaf!r} confirms real estate for "
                    f"{property_data.get('divar_id')}" if leaf else
                    f"No breadcrumb for {property_data.get('divar_id')} — keeping it; "
                    f"the caller named this URL")

            if category_known:
                keep, where, why = divar_categories.judge(all_crumbs, target_category)
                if not keep:
                    # In Divar's own words: the crumb that placed it, or the
                    # end of a breadcrumb that is not real estate at all.
                    named = (divar_categories.place(all_crumbs)[1]
                             or " › ".join(all_crumbs[-2:]))
                    logger.info(
                        f"Skipping off-category listing for '{target_category}' — {why} "
                        f"(breadcrumb: {' › '.join(all_crumbs)!r})")
                    self._last_category_drop = f"{named} — {source_title or decoded_url}"[:80]
                    return False  # sentinel: category skip — not a scrape error
                logger.info(f"{property_data.get('divar_id')}: {why}")

            # Infer listing_type (buy/rent) from the parsed price fields when the
            # breadcrumb didn't supply it (e.g. job category missing).
            # The frontend treats any non-'buy' value as اجاره, so leaving this
            # unset mislabels sale listings as rent.
            if not property_data.get("listing_type"):
                if property_data.get("rent_price") or property_data.get("deposit"):
                    property_data["listing_type"] = "rent"
                elif (property_data.get("total_price")
                      or property_data.get("price_per_meter")
                      or property_data.get("price")):
                    property_data["listing_type"] = "buy"

            # Get phone number (requires login). Always register an OTP key —
            # even for single-property scrapes (no job) — so Divar's SMS-OTP
            # prompt surfaces in the dashboard and the user can submit the code.
            _divar_id = property_data.get('divar_id', '')
            _otp_key = (
                f"{self.current_job.job_id}:{_divar_id}" if self.current_job
                else f"single:{_divar_id}"
            )
            # Flip the job's status while the scraper is blocked on an OTP code,
            # so the dashboard clearly shows it as paused → running — never
            # over a cancel (see _move_status), and the wait checks for one
            # every slice without holding a transaction open.
            _pause_job = self._pause_for_code
            _resume_job = self._resume_after_code
            _job_cancelled = self._cancelled_now

            # Everything above came free with the page. Contact info does not:
            # it clicks «اطلاعات تماس», solves a captcha, and spends one of the
            # account's requests — which is the budget Divar counts before it
            # demands a code. Asking for it on an ad the filters are about to
            # throw away spent that budget for nothing, and it is why a filtered
            # scrape was both slow and forever being asked to verify.
            if wants_contact is not None:
                reason = wants_contact(property_data)
                if reason:
                    logger.info(
                        f"Not requesting contact info for {property_data.get('divar_id')}: {reason}")
                    return property_data

            # Read the ad the way a person would before asking for the number,
            # then hold the pace between reveals. Both before Divar counts.
            await self._dwell_like_a_reader(property_data)
            await self._space_out_reveal()

            # …and the moment we count, against the account, which is what
            # Divar is counting against.
            self._reveals_since_rotation += 1
            self._reveals_this_run = getattr(self, "_reveals_this_run", 0) + 1
            await self._charge_reveal()
            from app import metrics as _mx
            _mx.scrape_reveals.inc()

            contact_extractor = ContactExtractor(
                self.page, self.images_dir, otp_key=_otp_key,
                on_pause=_pause_job, on_resume=_resume_job,
                should_cancel=_job_cancelled,
                # the code goes to whichever account is logged in *now*, which
                # rotation may have changed since the job started
                account_phone=self.active_phone,
                # Divar challenging this account is the strongest signal there
                # is that it needs replacing — louder than any threshold.
                on_challenge=self._note_account_challenged,
                # A code that has been answered is trust Divar just granted to
                # this jar. Save it, or the next use starts untrusted again.
                on_verified=self._persist_active_session,
                # Divar asking who this account IS. Nothing here can answer;
                # mark the account, rotate away, and put it in front of a person.
                on_identity_required=self._note_identity_required,
            )
            # How many sessions rotation can still reach. An unanswered code
            # prompt suppresses phone numbers for the whole job only once every
            # account has been tried — with one account that is the old
            # behaviour, and with five it is four more chances.
            contact_extractor.account_count = await self._usable_account_count()
            phone_number = await contact_extractor.get_phone_number()
            # How the reveal ended, on the row itself. «chat_only» is the one
            # that changes behaviour: property_exists stops re-opening those
            # to fill a gap that is not a gap, and the panel can say «فقط چت»
            # instead of showing a blank that reads as a scrape that failed.
            property_data["contact_channel"] = (
                "phone" if phone_number
                else (contact_extractor.contact_channel or "unavailable"))

            # Divar asked THIS ACCOUNT to prove who it is. That is not about
            # this listing: every reveal from here fails the same way, so
            # carrying on would write off the rest of the pool one by one —
            # which is exactly what happened before this existed, twenty
            # listings marked «فقط چت» with their numbers on the page.
            if getattr(contact_extractor, "needs_identity", False):
                self._needs_identity = True
                await self._report_identity_block()
            if phone_number:
                property_data["phone_number"] = phone_number
                # A reveal worked, so the pool is not exhausted after all.
                from app.scraper import otp_store as _os
                await _os.clear_timeouts(self._job_id_str)

            return property_data
            
        except Exception as e:
            logger.error(f"Failed to scrape property detail: {e}")
            self._last_detail_error = f"{type(e).__name__}"
            return None
    
    def _extract_location(self, soup) -> Dict[str, Any]:
        """Extract location information"""
        location = {}
        
        try:
            # Look for breadcrumb or location info
            breadcrumb = soup.select('.kt-page-title__subtitle a, .kt-breadcrumb a')
            if breadcrumb:
                locations = [b.get_text(strip=True) for b in breadcrumb]
                if len(locations) >= 1:
                    location['city_name'] = locations[0]
                if len(locations) >= 2:
                    location['district'] = locations[1]
                if len(locations) >= 3:
                    location['neighborhood'] = locations[2]

            # Fallback: Divar renders the location inside the first info row's
            # title (value is empty), formatted like
            # "۱ ساعت پیش در ارومیه، کنارگذر آزادگان". Match the relative-time +
            # "در" prefix, then split the comma-separated city/district.
            if not location.get('city_name'):
                import re as _re
                for row in soup.select('.kt-base-row, .kt-unexpandable-row, .kt-group-row-item'):
                    title_el = row.select_one(
                        '.kt-info-row__title, [class*="row__title"], .kt-group-row-item__title'
                    )
                    if not title_el:
                        continue
                    ttext = title_el.get_text(strip=True)
                    m = _re.search(r'(?:پیش|دیروز|امروز|لحظاتی|الان)\s*در\s+(.+)$', ttext)
                    if not m and ' در ' in ttext and '،' in ttext:
                        m = _re.search(r'\bدر\s+(.+)$', ttext)
                    if not m:
                        continue
                    parts = [p.strip() for p in m.group(1).split('،') if p.strip()]
                    if parts:
                        location['city_name'] = parts[0]
                    if len(parts) >= 2:
                        location['district'] = parts[1]
                    if len(parts) >= 3:
                        location['neighborhood'] = parts[2]
                    break

            # Look for map coordinates
            map_elem = soup.select_one('[data-lat][data-lng]')
            if map_elem:
                location['latitude'] = float(map_elem.get('data-lat', 0))
                location['longitude'] = float(map_elem.get('data-lng', 0))
            
            # Look for address
            address_elem = soup.select_one('.kt-unexpandable-row__value a[href^="geo:"]')
            if address_elem:
                location['address'] = address_elem.get_text(strip=True)
        
        except Exception as e:
            logger.warning(f"Failed to extract location: {e}")
        
        return location
    
    # Fields already stored as structured DB columns — never put these in features/amenities
    _STRUCTURED_TITLES = frozenset([
        'متراژ', 'مساحت', 'زیربنا', 'متراژ زمین', 'مساحت زمین',
        'اتاق', 'خواب', 'تعداد اتاق',
        'سال ساخت', 'سن بنا',
        'طبقه', 'تعداد طبقات',
        'آسانسور', 'پارکینگ', 'انباری', 'بالکن', 'تراس',
        'جهت', 'جهت ساختمان', 'نبش',
        'وضعیت', 'وضعیت واحد',
        'نوع سند', 'سند',
        'کاربری', 'نوع کاربری',
        'نوع ملک',
        'قیمت', 'قیمت کل', 'قیمت هر متر', 'ودیعه', 'اجاره', 'رهن',
    ])
    # Boolean amenities already shown as Yes/No badges — exclude from free-text lists
    _BOOLEAN_AMENITIES = frozenset(['آسانسور', 'پارکینگ', 'انباری', 'بالکن', 'تراس'])

    @staticmethod
    def _is_numeric_text(text: str) -> bool:
        """Return True if text is purely a number (Persian or Latin digits)."""
        return bool(re.match(r'^[\d۰-۹,،٬.\s]+$', text))

    def _extract_features(self, soup) -> List[str]:
        """Extract notable property labels that aren't structured fields.

        Uses ONLY .kt-feature-row__title (tag-style chips on Divar), not table
        cell values — those are already captured by the structured-field parsing
        in app/scraper/parsers.py.
        """
        features = []
        seen: set = set()
        try:
            for elem in soup.select('.kt-feature-row__title, .kt-group-row-item .kt-body--stable'):
                text = elem.get_text(strip=True)
                if not text or len(text) < 2:
                    continue
                if self._is_numeric_text(text):
                    continue
                # Skip if the element's title/context matches a structured field
                parent_title = ''
                row = elem.find_parent(class_=re.compile(r'kt-group-row-item|kt-base-row|kt-unexpandable-row'))
                if row:
                    t = row.select_one('[class*="__title"]')
                    if t:
                        parent_title = t.get_text(strip=True)
                if any(k in parent_title for k in self._STRUCTURED_TITLES):
                    continue
                # Skip boolean amenities — already shown as badges
                if any(k in text for k in self._BOOLEAN_AMENITIES):
                    continue
                if text not in seen:
                    seen.add(text)
                    features.append(text)
        except Exception as e:
            logger.warning(f"Failed to extract features: {e}")
        return features

    def _extract_amenities(self, soup) -> List[str]:
        """Extract extra amenities not already captured as boolean fields."""
        amenities = []
        seen: set = set()

        extra_keywords = [
            'استخر', 'سونا', 'جکوزی', 'سالن ورزش', 'روف گاردن', 'لابی', 'سرایدار',
            'کولر', 'شوفاژ', 'پکیج', 'رادیاتور', 'اسپلیت', 'چیلر', 'گرمایش',
            'پارکت', 'سرامیک', 'موزاییک', 'کف سنگ', 'کمد دیواری', 'شومینه',
            'هود', 'کابینت', 'گاز رومیزی',
            'اسکلت فلزی', 'اسکلت بتنی', 'نورگیر', 'حیاط اختصاصی',
            'شمالی', 'جنوبی', 'شرقی', 'غربی',
            'نوساز', 'بازسازی شده', 'کناف',
        ]

        def _add(text: str):
            text = text.strip()
            if not text or len(text) < 2:
                return
            if self._is_numeric_text(text):
                return
            # Skip boolean amenities
            if any(k in text for k in self._BOOLEAN_AMENITIES):
                return
            # Skip "بدون X" — already reflected in boolean badges
            if text.startswith('بدون '):
                return
            if text not in seen:
                seen.add(text)
                amenities.append(text)

        try:
            # 1. Parse the dedicated امکانات section on Divar
            for title_kw in ('امکانات', 'ویژگی'):
                hdr = soup.find('span', class_='kt-section-title__title',
                                string=lambda x: x and title_kw in x)
                if hdr:
                    section = hdr.find_parent('div', class_='kt-section-title')
                    if section:
                        container = section.find_next_sibling()
                        if container:
                            for item in container.select(
                                '.kt-group-row-item__value, .kt-feature-row__title, '
                                '.kt-unexpandable-row__value'
                            ):
                                _add(item.get_text(strip=True))

            # 2. Keyword scan — only for known extra amenities not in booleans
            for elem in soup.select(
                '.kt-group-row-item__value, .kt-unexpandable-row__value'
            ):
                text = elem.get_text(strip=True)
                if any(kw in text for kw in extra_keywords):
                    _add(text)

        except Exception as e:
            logger.warning(f"Failed to extract amenities: {e}")

        return amenities
    
    async def _extract_images_from_page(self) -> List[str]:
        """Extract all image URLs using Playwright JS (handles lazy loading and gallery slides)"""
        images = []
        try:
            # Scroll back to top where gallery lives
            await self.page.evaluate("window.scrollTo(0, 0)")
            await asyncio.sleep(0.3)

            # Click through gallery slides — use only gallery-specific selectors
            # (avoid generic [aria-label="بعدی"] which could match page-nav buttons)
            for _ in range(20):
                try:
                    next_btn = await self.page.query_selector(
                        '.slick-next, .kt-slider__next, .swiper-button-next, '
                        'button[data-direction="next"], .kt-image-block__carousel-button--next'
                    )
                    if next_btn and await next_btn.is_visible():
                        await next_btn.click()
                        await asyncio.sleep(0.5)  # wait for image to load
                    else:
                        break
                except Exception:
                    break

            # Final wait for all images to finish loading
            await asyncio.sleep(1.0)

            # Extract all image URLs from DOM including data-src attributes
            result = await self.page.evaluate("""
                () => {
                    const urls = new Set();

                    // From a srcset string, pick the URL with the highest width descriptor
                    // (e.g. "url1.webp 400w, url2.webp 800w" → url2.webp)
                    function bestFromSrcset(srcset) {
                        if (!srcset) return null;
                        let best = null, bestW = 0;
                        srcset.split(',').forEach(entry => {
                            const parts = entry.trim().split(/\\s+/);
                            const url = parts[0];
                            const w = parts[1] ? parseInt(parts[1]) : 0;
                            if (url && url.includes('divarcdn.com')) {
                                if (!best || w > bestW) { best = url; bestW = w; }
                            }
                        });
                        return best;
                    }

                    document.querySelectorAll('img').forEach(img => {
                        // Prefer srcset (highest-res) over src (may be thumbnail)
                        const fromSrcset = bestFromSrcset(
                            img.getAttribute('srcset') || img.getAttribute('data-srcset')
                        );
                        if (fromSrcset) { urls.add(fromSrcset); return; }
                        ['src', 'data-src', 'data-original', 'data-lazy-src'].forEach(attr => {
                            const s = img.getAttribute(attr);
                            if (s && s.includes('divarcdn.com')) urls.add(s);
                        });
                    });
                    document.querySelectorAll('source').forEach(source => {
                        ['srcset', 'data-srcset'].forEach(attr => {
                            const best = bestFromSrcset(source.getAttribute(attr));
                            if (best) urls.add(best);
                        });
                    });
                    return Array.from(urls);
                }
            """)

            # Deduplicate: keep only the highest-quality version per photo UUID.
            # Priority: webp_main > webp_post > webp_thumbnail
            # Also filter non-photo assets (maps, icons, related-listing thumbnails).
            QUALITY = {'webp_main': 3, 'original': 3, 'webp_post': 2, 'webp_thumbnail': 1}

            def _quality(url: str) -> int:
                for k, v in QUALITY.items():
                    if k in url:
                        return v
                return 2  # unknown = treat as medium

            by_uuid: dict = {}
            for src in result:
                if not src:
                    continue
                # Exclude non-property-photo URLs
                if 'mapimage.divarcdn.com' in src:
                    continue
                if '/widget-icons/' in src or '/icon_' in src:
                    continue
                # webp_thumbnail = from related/listing cards, NOT this property's gallery
                if '/webp_thumbnail/' in src:
                    continue
                # UUID is the last path segment without extension
                slug = src.rstrip('/').split('/')[-1].split('.')[0]
                if slug not in by_uuid or _quality(src) > _quality(by_uuid[slug]):
                    by_uuid[slug] = src

            images = list(by_uuid.values())

        except Exception as e:
            logger.warning(f"Failed to extract images via JS: {e}")

        return images

    def _extract_images(self, soup) -> List[str]:
        """Fallback: extract image URLs from BeautifulSoup (may miss lazy-loaded images)"""
        images = []
        try:
            img_elems = soup.select('.kt-image-block__image, .post-image img, picture img')
            for img in img_elems:
                src = img.get('src') or img.get('data-src')
                if src and 'divarcdn.com' in src and src not in images:
                    images.append(src)
        except Exception as e:
            logger.warning(f"Failed to extract images (soup fallback): {e}")
        return images
    
    async def _click_show_all_details(self) -> bool:
        """Click 'Show all details' button to reveal hidden features"""
        try:
            # Selectors for "Show all details" button
            show_all_selectors = [
                'button:has-text("نمایش همهٔ جزئیات")',
                'button:has-text("نمایش همه")',
                'button:has-text("مشاهده بیشتر")',
                '.kt-show-more-button',
                'button.kt-button--secondary:has-text("جزئیات")',
            ]
            
            for selector in show_all_selectors:
                try:
                    button = await self.page.query_selector(selector)
                    if button:
                        is_visible = await button.is_visible()
                        if is_visible:
                            logger.info(f"Found 'Show all details' button with selector: {selector}")
                            await button.scroll_into_view_if_needed()
                            await asyncio.sleep(0.3)
                            await button.click(force=True, timeout=3000)
                            logger.info("'Show all details' button clicked successfully")
                            await asyncio.sleep(1.0)  # Wait for content to expand
                            return True
                except Exception as e:
                    logger.debug(f"Failed with selector {selector}: {e}")
                    continue
            
            logger.info("No 'Show all details' button found (content may already be expanded)")
            return False
            
        except Exception as e:
            logger.warning(f"Failed to click 'Show all details' button: {e}")
            return False
    
    async def _extract_advertiser_type(self):
        """Returns (advertiser_type, agency_name); either may be None."""
        """Detect whether the seller is personal (شخصی) or an agency (مشاور).

        Divar's own row is the first source, but it is missing on a lot of ads
        and agencies routinely post under «شخصی». So the same DOM read also
        hands back the text it looked at, and looks_like_agency() decides in
        Python — the keyword list lives there, where it can be tested, instead
        of being duplicated inside this page script.
        """
        try:
            result = await self.page.evaluate("""() => {
                const clean = s => (s || '').replace(/\\s+/g, ' ').trim().slice(0, 200);
                const rows = [];
                // Harvest only — no judgement here. Every label and keyword
                // decision lives in decide_advertiser_type(), where it can be
                // tested without a browser.
                for (const row of document.querySelectorAll(
                        '.kt-base-row, .kt-unexpandable-row')) {
                    const title = row.querySelector('[class*="__title"]');
                    if (!title) continue;
                    const value = row.querySelector('[class*="__value"], [class*="__end"]');
                    rows.push([clean(title.innerText), clean(value ? value.innerText : '')]);
                }
                const contact = document.querySelector(
                    '[class*="contact"], [class*="seller"], [class*="advertiser"]'
                );

                // Short standalone lines and link labels only. Divar's agency
                // panel sits under the map — «مشاور املاک | فعالیت از تیر ۱۴۰۴»,
                // a «پروفایل مشاور املاک» link, the agency's own row — and none
                // of it exists on an ad an owner posted. The description is
                // deliberately out of reach: an owner writing «مشاورین املاک
                // تماس نگیرند» must not be read as one.
                // textContent, not innerText: innerText forces a layout pass
                // per element, and this walks every leaf on the page once per
                // ad. The scan is never cut short — the agency block sits at
                // the bottom of the page, under the map, so stopping early
                // would miss exactly what is being looked for. Only the payload
                // is bounded, and a Set keeps the de-dupe off the hot path.
                const seen = new Set();
                const push = t => {
                    t = clean(t);
                    if (t && t.length <= 80) seen.add(t);
                };
                for (const a of document.querySelectorAll('a')) push(a.textContent);
                for (const el of document.querySelectorAll('p, span, h1, h2, h3, h4, div')) {
                    if (el.children.length) continue;      // leaf nodes only
                    push(el.textContent);
                }
                const panel = [...seen].slice(0, 400);
                return {
                    rows,
                    contact: clean(contact ? contact.innerText : ''),
                    panel,
                };
            }""")
            if not result:
                return None, None
            panel = result.get("panel") or []
            kind = _decide_advertiser(result.get("rows") or [], result.get("contact"), panel)
            # the shop's own name, for the seller_name column nothing ever filled
            return kind, (_agency_name(panel) if kind == "agency" else None)
        except Exception as e:
            logger.debug(f"Could not extract advertiser type: {e}")
            return None, None

    def _parse_relative_time(self, text: str) -> Optional[datetime]:
        """Convert Persian relative time strings (e.g. '۱۲ ساعت پیش') to datetime."""
        from app.scraper.parsers import normalize_persian_digits
        if not text:
            return None
        normalized = normalize_persian_digits(text)
        now = datetime.now()
        # «دقایقی پیش» / «لحظاتی پیش»: just posted, and no number to read
        if 'دقایقی' in normalized or 'لحظاتی' in normalized:
            return now
        m = re.search(r'(\d+)', normalized)
        n = int(m.group(1)) if m else 1
        if 'دقیقه' in normalized:
            return now - timedelta(minutes=n)
        if 'ساعت' in normalized:
            return now - timedelta(hours=n)
        if 'دیروز' in normalized:
            return now - timedelta(days=1)
        if 'روز' in normalized:
            return now - timedelta(days=n)
        if 'هفته' in normalized:
            return now - timedelta(weeks=n)
        if 'ماه' in normalized:
            return now - timedelta(days=n * 30)
        return None

    async def _extract_posted_at(self) -> Optional[datetime]:
        """Extract the listing's publication time from the property page."""
        try:
            raw = await self.page.evaluate("""() => {
                // <time datetime="..."> element
                const timeEl = document.querySelector('time[datetime]');
                if (timeEl) return timeEl.getAttribute('datetime');
                // Divar's own publish date, exact to the minute: «انتشار
                // آگهی: ۴ مهر ۱۴۰۵، ۰۸:۴۶». It sits where «... پیش» used to,
                // which is why every date-filtered run of 1405/07/04 found
                // no date at all and dropped every listing.
                for (const el of document.querySelectorAll('p, span')) {
                    const t = (el.innerText || '').trim();
                    if (t.startsWith('انتشار آگهی')) return t;
                }
                // Relative time: «۳ ساعت پیش در ارومیه» now lives in the
                // header's info-row title.
                const candidates = document.querySelectorAll(
                    'p[class*="--small"], span[class*="--small"], [class*="publish"], [class*="date"], [class*="info-row__title"]'
                );
                for (const el of candidates) {
                    const t = (el.innerText || '').trim();
                    if (t.includes('پیش') || t.includes('دیروز') || t.includes('هفته') || t.includes('ساعت'))
                        return t;
                }
                return null;
            }""")
            if not raw:
                return None
            # Try ISO datetime first
            try:
                from datetime import timezone
                return datetime.fromisoformat(raw.replace('Z', '+00:00')).replace(tzinfo=None)
            except Exception:
                pass
            from app.scraper.parsers import parse_divar_published
            return parse_divar_published(raw) or self._parse_relative_time(raw)
        except Exception as e:
            logger.debug(f"Could not extract posted_at: {e}")
            return None

    @staticmethod
    def _noop(*_a, **_k):
        return None

    async def download_images(
        self,
        images: List[str],
        divar_id: str
    ) -> List[str]:
        """Download images and return local paths"""
        local_paths = []
        # Reset per call rather than per scraper: one property's fingerprints
        # leaking into the next would merge two unrelated listings, which is
        # the one failure this feature must not have.
        self._pending_hashes: List[int] = []
        self._pending_quality: List[Dict[str, Any]] = []
        
        try:
            property_dir = self.images_dir / divar_id
            property_dir.mkdir(parents=True, exist_ok=True)
            
            import io as _io
            from PIL import Image as _Image

            # Pillow warns above MAX_IMAGE_PIXELS and only errors at twice that,
            # so the default lets a bomb through with a log line. Halving our
            # ceiling here makes our real limit the erroring one.
            _prev_bomb_limit = _Image.MAX_IMAGE_PIXELS
            _Image.MAX_IMAGE_PIXELS = max(int(settings.max_image_pixels) // 2, 1)

            max_count = max(int(settings.max_images_per_property), 0)
            max_bytes = max(int(settings.max_image_bytes), 1)
            if max_count and len(images) > max_count:
                logger.info(
                    f"{divar_id}: {len(images)} images offered, keeping the first {max_count}")
                _mx_images("too_many", len(images) - max_count)
                images = images[:max_count]

            try:
                # Through the browser's own network stack, not httpx.
                #
                # The httpx client sent every image request as
                # `python-httpx/0.26.0`, with no cookies, no Referer and no
                # pause — up to a thousand CDN hits per run announcing a
                # Python script, from the same IP that had just browsed the
                # listing as Chrome. context.request uses Chromium's TLS, its
                # cookies and the device's UA; the Referer is the page the
                # image sits on, which is what a browser sends.
                req = self.context.request if self.context else None
                for i, url in enumerate(images):
                    try:
                        raw = bytearray()
                        too_big = False
                        if req is None:
                            continue
                        response = await req.get(
                            url, timeout=30_000,
                            headers={"Referer": f"https://divar.ir/v/{divar_id}",
                                     "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"})
                        if response.status != 200:
                            continue
                        declared = (response.headers or {}).get("content-length")
                        if declared and declared.isdigit() and int(declared) > max_bytes:
                            logger.warning(
                                f"{divar_id}: image {i+1} declares "
                                f"{int(declared)}B > {max_bytes}B cap — skipped")
                            continue
                        body = await response.body()
                        if len(body) > max_bytes:
                            too_big = True
                        else:
                            raw.extend(body)
                        # A person's browser does not fetch five images in the
                        # same millisecond; the page loads them as it renders.
                        if i + 1 < len(images):
                            await asyncio.sleep(random.uniform(0.15, 0.6))
                        if too_big:
                            logger.warning(
                                f"{divar_id}: image {i+1} exceeded the "
                                f"{max_bytes}B cap mid-download — skipped")
                            _mx_images("too_big")
                            continue

                        # Always convert (webp/png/...) to JPEG so every stored
                        # image is a browser-universal .jpg
                        filename = f"img_{i+1}.jpg"
                        filepath = property_dir / filename
                        try:
                            im = _Image.open(_io.BytesIO(bytes(raw)))
                            # Checked before decoding: .size is read from the
                            # header, so this rejects a bomb without ever
                            # allocating the bitmap it describes.
                            pixels = (im.size[0] or 0) * (im.size[1] or 0)
                            if pixels > settings.max_image_pixels:
                                logger.warning(
                                    f"{divar_id}: image {i+1} decodes to {pixels} "
                                    f"pixels — over the cap, skipped")
                                continue
                            im = im.convert("RGB")
                            im.save(filepath, format="JPEG", quality=85)
                            # Fingerprint here, from the decoded bitmap we
                            # already hold. Doing it later means opening every
                            # JPEG again from disk for no reason, and doing it
                            # before the save would hash images we then reject.
                            try:
                                from app.services.image_fingerprint import dhash
                                self._pending_hashes.append(dhash(im))
                            except Exception as hash_err:
                                logger.debug(
                                    f"{divar_id}: could not fingerprint image {i+1}: {hash_err}")
                            try:
                                from app.services import image_quality as _iq
                                self._pending_quality.append(
                                    _iq.verdict(_iq.measure(im)))
                            except Exception as q_err:
                                logger.debug(
                                    f"{divar_id}: could not score image {i+1}: {q_err}")
                        except Exception as decode_err:
                            # not a decodable image, or a decompression bomb
                            logger.debug(f"{divar_id}: image {i+1} not usable: {decode_err}")
                            _mx_images("undecodable")
                            continue
                        # served URL (see /images static mount)
                        local_paths.append(f"/images/{divar_id}/{filename}")
                        logger.debug(f"Downloaded+converted image: {filename}")
                        await asyncio.sleep(0.3)  # Rate limit downloads
                    except Exception as e:
                        logger.warning(f"Failed to download image {i+1}: {e}")
            finally:
                _Image.MAX_IMAGE_PIXELS = _prev_bomb_limit

        except Exception as e:
            logger.error(f"Failed to download images: {e}")

        return local_paths
    
    async def _log_run(self, message: str, *, level: str = "info", **extra) -> None:
        """One line in this run's own log, if the run is known. Never raises.

        initialize() runs before start_scraping_job sets current_job, so the
        job id comes from _job_id_str, which the route sets first."""
        jid = getattr(self, "_job_id_str", None) or (
            str(self.current_job.job_id) if getattr(self, "current_job", None) else None)
        if not jid:
            return
        try:
            from app.services import job_log as _jl
            await _jl.record(jid, _jl.SESSION, message, level=level, **extra)
        except Exception as e:
            logger.debug(f"[rotate] could not write to the run log: {e}")

    def _usable_accounts_query(self, query):
        """Narrow a cookies query to the numbers THIS run may use.

        One definition for every place that chooses, counts or resets
        accounts. Four of them used to write their own filter and two forgot
        the owner — so a colleague's numbers were counted as «still unspent»
        and had their counters wiped by a run that could never reach them.

        Owner-scoped when the run has one (every run started from the panel
        or a schedule does); switched-on only; and never a number Divar has
        asked to verify its identity. A run nobody owns gets the numbers
        nobody owns: it used to get the whole table «so an internally-started
        scrape does not lose its pool», which made every internal run a way
        onto everybody's numbers.
        """
        from app.models.cookie import Cookie as CookieModel
        query = (query
                 .where(CookieModel.is_valid == True)          # noqa: E712
                 .where(CookieModel.is_enabled == True)        # noqa: E712
                 .where(CookieModel.identity_required_at.is_(None)))
        owner = getattr(self, "owner_user_id", None)
        if owner:
            return query.where(CookieModel.owner_user_id == owner)
        return query.where(CookieModel.owner_user_id.is_(None))

    async def _account_usable(self, phone: Optional[str]) -> bool:
        """Whether this run may put `phone` in the browser: the owner's own,
        valid, switched on, and not waiting on an identity check."""
        if not phone:
            return False
        db = getattr(self, "db_session", None)
        if db is None:
            # Nothing to check against (tests, a bare scraper): the caller
            # named it, and there is no pool it could be stealing from.
            return True
        try:
            from app.models.cookie import Cookie as CookieModel
            want = "".join(ch for ch in str(phone) if ch.isdigit())[-10:]
            rows = (await db.execute(self._usable_accounts_query(
                select(CookieModel.phone_number)))).scalars().all()
            return any("".join(ch for ch in str(p) if ch.isdigit())[-10:] == want
                       for p in rows)
        except Exception as e:
            logger.warning(f"[rotate] could not check {phone}: {e}")
            return False

    async def _load_rotation_pool(self) -> List[str]:
        """Valid saved Divar accounts, least-spent first — the rotation
        candidates, in the order they should be reached for."""
        if not self.db_session:
            return []
        try:
            from app.models.cookie import Cookie as CookieModel
            # Rotation must stay inside the pool the run's owner owns.
            # Without this it would log somebody else's number in and spend
            # their reveals — and a reveal is charged to the account, not to
            # us. An account Divar wants identified is not a candidate either:
            # handing it back would spend a reveal to hit the same wall. And a
            # number its owner switched off is not reachable — a code sent to
            # it parks the run. A run with no known owner gets only the
            # numbers nobody owns (see _usable_accounts_query).
            query = self._usable_accounts_query(select(CookieModel))
            rows = (await self.db_session.execute(
                query.order_by(CookieModel.reveals.asc(),
                               CookieModel.last_used_at.asc().nullsfirst())
            )).scalars().all()
            # A heavy account Divar recently challenged is resting. Handing it
            # back every cycle was how one account with 225 reveals kept
            # collecting a code prompt on every entry while light accounts
            # rotated through clean. Only rested when the pool has others —
            # a resting account beats no account.
            rest_after = int(getattr(settings, "rest_after_reveals", 50) or 0)
            rest_h = float(getattr(settings, "rest_hours", 24) or 0)
            cutoff = datetime.now(timezone.utc) - timedelta(hours=rest_h)
            def _resting(r) -> bool:
                if rest_after <= 0 or rest_h <= 0 or not r.challenged_at:
                    return False
                ch = r.challenged_at if r.challenged_at.tzinfo else r.challenged_at.replace(tzinfo=timezone.utc)
                return (r.reveals or 0) >= rest_after and ch > cutoff
            # de-dupe while keeping order (one entry per phone)
            seen, pool, resting = set(), [], []
            for r in rows:
                if not r.phone_number or r.phone_number in seen:
                    continue
                seen.add(r.phone_number)
                (resting if _resting(r) else pool).append(r.phone_number)
            if resting and pool:
                logger.info(f"[rotate] resting {len(resting)} heavy account(s): {', '.join(resting)}")
            return pool or resting
        except Exception as e:
            logger.warning(f"[rotate] could not load cookie pool: {e}")
            return []

    async def _usable_account_count(self) -> int:
        """How many Divar sessions rotation can still choose from.

        Used to decide how many unanswered code prompts to absorb before
        concluding that every account is challenged rather than just this one.
        Never returns 0: the current session is always one.
        """
        try:
            from app.models.cookie import Cookie
            from sqlalchemy import func, select as _select
            # Same pool rotation actually draws from, or the run absorbs
            # prompts for accounts it will never be allowed to reach.
            q = self._usable_accounts_query(_select(func.count()).select_from(Cookie))
            n = (await self.db_session.execute(q)).scalar() or 0
            return max(1, int(n))
        except Exception as e:
            logger.warning(f"[rotate] could not count usable accounts: {e}")
            return 1

    async def _dwell_like_a_reader(self, property_data: dict) -> None:
        """Spend on the ad what a person would before pressing «اطلاعات تماس».

        The scraper used to land, wait three hundred milliseconds, and click.
        Nobody does that. A reader scrolls down through the description in a
        few moves, pauses on it for as long as there is to read, sometimes
        goes back up to the photos, and only then asks for the number.

        The dwell scales with the text: about eighteen characters a second of
        Persian on a screen, bounded by the two settings, then jittered so
        two ads of the same length are not read in the same time.
        """
        page = getattr(self, "page", None)
        if page is None:
            return
        cfg = settings
        lo = float(getattr(cfg, "reveal_dwell_min_seconds", 0) or 0)
        hi = float(getattr(cfg, "reveal_dwell_max_seconds", 0) or 0)
        if hi <= 0:
            return
        text = f"{property_data.get('title') or ''} {property_data.get('description') or ''}"
        reading = len(text) / 18.0
        dwell = max(lo, min(hi, reading)) * random.uniform(0.7, 1.4)

        # spread the dwell over a few scrolls, the way reading actually moves
        steps = random.randint(2, 4)
        for n in range(steps):
            try:
                await page.mouse.wheel(0, random.randint(180, 520))
            except Exception:
                pass
            await asyncio.sleep(dwell / steps * random.uniform(0.6, 1.4))
        # …and sometimes a look back up at the photos
        if random.random() < 0.3:
            try:
                await page.mouse.wheel(0, -random.randint(300, 900))
            except Exception:
                pass
            await asyncio.sleep(random.uniform(0.8, 2.5))

    async def _space_out_reveal(self) -> None:
        """Hold off until enough time has passed since the last reveal.

        _human_like_delay paces LISTINGS, and a filtered run opens far more
        listings than it reveals — pre_contact_skip drops most of them before
        the contact button is ever clicked. So the one action Divar counts was
        the one action nothing paced, and a tightly filtered run could fire
        reveals back to back while looking slow from the outside.

        Waits out a challenge cooldown first: a challenge is Divar saying
        «slow down» in the only words it has, and carrying on at the same pace
        on the next account is how one challenge becomes five.

        Jittered on purpose. A reveal exactly every twelve seconds is a
        signature; the floor is a floor, not a metronome.
        """
        now = time.monotonic()

        until = getattr(self, "_reveal_hold_until", 0.0)
        if now < until:
            owed = until - now
            logger.info(f"[pace] resting {owed:.0f}s after Divar's challenge "
                        f"before the next reveal")
            await asyncio.sleep(owed)
            now = time.monotonic()

        # A person does not reveal forty numbers in a row without looking up.
        # Roughly every N reveals, a longer break — N and the length both
        # drawn around their settings so the rhythm has no period to detect.
        every = int(getattr(settings, "reveal_break_every", 0) or 0)
        brk = float(getattr(settings, "reveal_break_seconds", 0) or 0)
        if every > 0 and brk > 0:
            self._until_break = getattr(self, "_until_break", None)
            if self._until_break is None:
                self._until_break = max(3, int(random.gauss(every, every / 3)))
            self._until_break -= 1
            if self._until_break <= 0:
                pause = max(15.0, random.gauss(brk, brk / 3))
                logger.info(f"[pace] a longer break — {pause:.0f}s — before the next reveal")
                await asyncio.sleep(pause)
                self._until_break = max(3, int(random.gauss(every, every / 3)))
                now = time.monotonic()

        gap = float(getattr(settings, "reveal_min_gap_seconds", 0) or 0)
        if gap <= 0:
            self._last_reveal_at = now
            return
        last = getattr(self, "_last_reveal_at", None)
        if last is not None:
            owed = (last + gap) - now
            if owed > 0:
                owed += random.uniform(0, gap * 0.5)
                logger.info(f"[pace] {owed:.1f}s before the next contact reveal")
                await asyncio.sleep(owed)
        self._last_reveal_at = time.monotonic()

    async def _charge_reveal(self) -> int:
        """Bill one contact reveal to the active account; return its new total.

        Written straight through rather than batched: a job that is cancelled
        or crashes still spent those reveals on Divar's side, and losing the
        record would let the next job pick that same account as if it were
        rested.
        """
        # built with __new__ in the rotation tests, so nothing here is assumed
        db = getattr(self, "db_session", None)
        if not getattr(self, "active_phone", None) or db is None:
            return 0
        try:
            from app.models.cookie import Cookie as CookieModel
            row = (await db.execute(
                select(CookieModel).where(
                    CookieModel.phone_number == self.active_phone))).scalars().first()
            if not row:
                return 0
            row.reveals = (row.reveals or 0) + 1
            row.last_used_at = datetime.now()
            await db.commit()
            return row.reveals
        except Exception as e:
            logger.warning(f"[rotate] could not charge a reveal to {self.active_phone}: {e}")
            try:
                await db.rollback()
            except Exception:
                pass
            return 0

    async def _account_reveals(self, phone: Optional[str] = None) -> int:
        """What this account has already spent, across every job."""
        phone = phone or getattr(self, "active_phone", None)
        db = getattr(self, "db_session", None)
        if not phone or db is None:
            return 0
        try:
            from app.models.cookie import Cookie as CookieModel
            row = (await db.execute(
                select(CookieModel).where(
                    CookieModel.phone_number == phone))).scalars().first()
            return (row.reveals or 0) if row else 0
        except Exception:
            return 0

    async def _mark_account_spent(self, phone: Optional[str], budget: int) -> None:
        """Record an account as having used up its budget.

        Used when Divar challenges it: the challenge is the account telling us
        it is spent, and that beats whatever our own count had reached.
        """
        db = getattr(self, "db_session", None)
        if not phone or db is None:
            return
        try:
            from app.models.cookie import Cookie as CookieModel
            row = (await db.execute(
                select(CookieModel).where(
                    CookieModel.phone_number == phone))).scalars().first()
            if not row:
                return
            row.reveals = max(row.reveals or 0, budget)
            row.last_used_at = datetime.now()
            row.challenged_at = datetime.now(timezone.utc)
            await db.commit()
            heavy = (row.reveals or 0) >= int(getattr(settings, "rest_after_reveals", 50) or 0)
            logger.info(f"[rotate] {phone} marked spent after a Divar challenge"
                        + (f" — heavy account ({row.reveals} reveals), resting it "
                           f"{getattr(settings, 'rest_hours', 24)}h" if heavy else ""))
        except Exception as e:
            logger.warning(f"[rotate] could not mark {phone} spent: {e}")
            try:
                await db.rollback()
            except Exception:
                pass

    async def _unspent_account_count(self, every: int) -> int:
        """Valid accounts that still have reveals left in this round.

        Counted, not inferred. The caller decides whether to start a new round,
        and starting one early throws away the ordering that makes rotation
        spread load at all.
        """
        db = getattr(self, "db_session", None)
        if db is None or every <= 0:
            return 0
        try:
            from app.models.cookie import Cookie as CookieModel
            from sqlalchemy import func as _func
            # The run's own pool. Counting everybody's numbers meant a
            # colleague's fresh account kept «something unspent» true for a
            # run that could never reach it, and the round never turned over.
            return int((await db.execute(
                self._usable_accounts_query(
                    select(_func.count()).select_from(CookieModel))
                .where(_func.coalesce(CookieModel.reveals, 0) < every)
            )).scalar() or 0)
        except Exception as e:
            logger.warning(f"[rotate] could not count unspent accounts: {e}")
            # Assume something is left: a miscount that starts a new round
            # early is the bug this replaced.
            return 1

    async def _rest_all_accounts(self) -> None:
        """Start a fresh round once every account has spent its budget.

        Divar's own tolerance recovers with time; without this the pool would
        stay permanently exhausted and rotation would stop meaning anything.
        """
        db = getattr(self, "db_session", None)
        if db is None:
            return
        try:
            from app.models.cookie import Cookie as CookieModel
            # Only the run's own pool. This reset every valid session in the
            # table, so one person's exhausted round wiped the counters —
            # and with them the «least spent first» order — of everybody
            # else's numbers.
            rows = (await db.execute(
                self._usable_accounts_query(select(CookieModel)))).scalars().all()
            for r in rows:
                r.reveals = 0
            await db.commit()
            logger.info(f"[rotate] every account had spent its budget — new round for {len(rows)}")
        except Exception as e:
            logger.warning(f"[rotate] could not start a new round: {e}")

    async def _persist_active_session(self) -> None:
        """Write the current account's live cookies back before leaving it.

        The saved set is a snapshot from the day that account logged in. Divar
        keeps updating a session as it is used, so restoring the snapshot every
        time we come back replays an old session and discards everything the
        account had built up — and a browser whose identity resets on a cycle is
        exactly what gets asked to verify itself. Rotation was creating the
        challenges it exists to avoid.
        """
        # built with __new__ in the rotation tests — assume nothing
        db = getattr(self, "db_session", None)
        if not getattr(self, "active_phone", None) or not getattr(self, "auth", None):
            return
        try:
            cookies = await self.auth.get_current_cookies()
            if not cookies:
                return
            from app.services.divar_session import auth_cookie as _auth_cookie
            _ac = _auth_cookie(cookies)
            token = _ac.get("value") if _ac else None
            await self.auth.save_cookies_to_file(self.active_phone, cookies)
            if db is not None:
                await self.auth.save_cookies_to_db(self.active_phone, cookies, token)
            logger.info(f"[rotate] saved {len(cookies)} live cookies for {self.active_phone}")
        except Exception as e:
            logger.warning(f"[rotate] could not save session for {self.active_phone}: {e}")

    async def _report_identity_block(self) -> None:
        """Say it once, in the run log and to the account's owner.

        Once per run: every listing hits the same wall, and twenty identical
        emails is the same as none.
        """
        if getattr(self, "_identity_reported", False):
            return
        self._identity_reported = True
        acct = self.active_phone or "—"
        msg = (f"دیوار از حساب {acct} خواسته هویتش را تأیید کند — تا وقتی این "
               f"کار انجام نشود هیچ شماره‌ای گرفته نمی‌شود")
        try:
            if self._job_id_str:
                from app.services import job_log as _jl
                await _jl.record(self._job_id_str, _jl.SESSION, msg,
                                 level="error", account=acct,
                                 url="https://divar.ir/my-divar/identity-confirmation")
        except Exception as e:
            logger.warning(f"[identity] could not record it on the run: {e}")

        # And the person who can actually fix it: the owner of that account.
        try:
            from app.database import async_session_maker
            from app.models.cookie import Cookie
            from app.models.user import User
            from app.services import forwarder as _fw, email_service, email_templates
            from sqlalchemy import select as _sel

            async with async_session_maker() as db:
                cks = (await db.execute(_sel(Cookie))).scalars().all()
                ids = {c.owner_user_id for c in cks
                       if getattr(c, "owner_user_id", None)
                       and _fw.same_phone(c.phone_number, acct)}
                tos = []
                if ids:
                    tos = [a for a in (await db.execute(_sel(User.email).where(
                        User.id.in_(ids), User.email.isnot(None),
                        User.is_active == True))).scalars().all() if a]  # noqa: E712
                if not tos:
                    tos = [a for a in (await db.execute(_sel(User.email).where(
                        User.role.in_(("root", "super_admin")),
                        User.email.isnot(None),
                        User.is_active == True))).scalars().all() if a]  # noqa: E712
                body = (
                    f"دیوار برای حساب {acct} «تأیید هویت» خواسته است.\n\n"
                    "تا وقتی این کار انجام نشود، اسکرپر هیچ شمارهٔ تماسی "
                    "نمی‌تواند بگیرد — دکمهٔ «اطلاعات تماس» برای این حساب "
                    "نمایش داده نمی‌شود.\n\n"
                    "برای رفع آن:\n"
                    "۱) با همین حساب وارد divar.ir شوید\n"
                    "۲) «دیوار من» ← «تأیید هویت»\n"
                    "۳) کد ملی را وارد و مراحل را کامل کنید\n\n"
                    "آدرس مستقیم: https://divar.ir/my-divar/identity-confirmation"
                )
                subj, html, text = email_templates.notification(
                    "دیوار تأیید هویت می‌خواهد — اسکرپر شماره نمی‌گیرد", body,
                    cta_label="تأیید هویت در دیوار",
                    cta_url="https://divar.ir/my-divar/identity-confirmation")
                for to in tos:
                    try:
                        await email_service.send(to, subj, html, text, db=db)
                    except Exception as se:
                        logger.warning(f"[identity] could not email {to}: {se}")
                logger.error(f"[identity] {acct} must verify — told {len(tos)} recipient(s)")
        except Exception as e:
            logger.warning(f"[identity] could not notify: {e}")

    async def _note_identity_required(self, page_text: str = "") -> None:
        """Divar wants this account to prove who it is. Record it everywhere
        a person might look, and stop offering the account.

        Three places, deliberately: the cookie row so it survives a restart
        and the pool can skip it; the in-memory registry the panel polls so a
        dialog opens within seconds; and the run log so the finish line says
        why this account produced nothing. Never raises.
        """
        phone = getattr(self, "active_phone", None)
        from app.scraper import otp_store as _os
        from app.services import job_log as _jl
        job_id = getattr(self, "_job_id_str", None)

        await _os.note_identity_required(phone or "", job_id=job_id, text=page_text)

        db = getattr(self, "db_session", None)
        if phone and db is not None:
            try:
                from app.models.cookie import Cookie as CookieModel
                row = (await db.execute(
                    select(CookieModel).where(CookieModel.phone_number == phone))).scalars().first()
                if row:
                    row.identity_required_at = datetime.now(timezone.utc)
                    await db.commit()
            except Exception as e:
                logger.warning(f"[identity] could not flag {phone}: {e}")
                try:
                    await db.rollback()
                except Exception:
                    pass

        if job_id:
            await _jl.record(
                job_id, _jl.CHALLENGE,
                f"دیوار برای شمارهٔ {phone or '؟'} احراز هویت با کد ملی می‌خواهد — "
                "اسکرپر نمی‌تواند این را انجام دهد؛ این شماره کنار گذاشته شد",
                level="error", phone=phone, kind="identity")

        # Same exit as a code challenge: this account is done for this run.
        self._force_rotate = True

    def _note_account_challenged(self) -> None:
        """Divar asked this account for an SMS code — rotate at the next chance.

        Called from ContactExtractor the moment the OTP modal appears, before it
        settles in to wait for a human. Deliberately synchronous and trivial: it
        runs while a modal is on screen, so it only records the fact. The switch
        itself happens between listings, where it is safe to navigate.
        """
        self._force_rotate = True
        self._challenges_this_run = getattr(self, "_challenges_this_run", 0) + 1
        # Divar just said «slow down». _space_out_reveal waits this out before
        # the next reveal, on whichever account rotation picks — the pace is
        # ours, not the account's, and moving to a fresh number at the same
        # speed only spends the fresh number.
        cooldown = float(getattr(settings, "challenge_cooldown_seconds", 0) or 0)
        if cooldown > 0:
            self._reveal_hold_until = time.monotonic() + cooldown
        from app import metrics as _mx
        _mx.scrape_challenges.inc()

    def _note_account(self, job) -> None:
        """The account the run is on right now, on the row: divar_phone is the
        current one, accounts_used every one so far — so the panel can say
        which number did the scraping, and that a rotated run used several."""
        phone = self.active_phone
        if not job or not phone:
            return
        used = list(job.accounts_used or [])
        if phone not in used:
            used.append(phone)
            job.accounts_used = used
        if job.divar_phone != phone:
            job.divar_phone = phone

    async def maybe_rotate_account(self) -> bool:
        """Switch Divar account once this one has revealed `cookie_rotate_every`
        phone numbers, or as soon as Divar challenges it.

        The threshold counts **contact-info reveals**, not listings processed.
        Divar's SMS check is triggered by asking for a phone number, and
        pre_contact_skip means a filtered run opens far more listings than it
        reveals — so a listing-based count drifted further from Divar's the more
        filtering was applied, and the setting looked like it was being ignored.

        Returns True when the active account actually changed.
        """
        # Somebody asked, from the panel, for a different number — or switched
        # the current one off. Before any threshold, and before the pin
        # below: that is a person saying the phone is not in their hand, not
        # a budget question.
        requested = await self._take_switch_request()
        if requested is not None:
            return await self._switch_on_request(requested)

        override = getattr(self, "_rotate_every_override", None)
        every = override if override is not None else (getattr(settings, "cookie_rotate_every", 0) or 0)

        forced = self._force_rotate

        if forced:
            # How much budget this account had actually spent when Divar
            # challenged it — the one number needed to tune the threshold, and
            # the one nothing recorded. Read here, before _mark_account_spent
            # below overwrites it with `every`. Measured the same way the
            # threshold measures, so the two are directly comparable.
            # Bookkeeping must never break a rotation, hence the guard.
            try:
                spent_at_challenge = max(
                    await self._account_reveals(), self._reveals_since_rotation)
                from app import metrics as _mx
                _mx.scrape_reveals_at_challenge.observe(spent_at_challenge)
                logger.info(
                    f"[rotate] Divar challenged {self.active_phone} after "
                    f"{spent_at_challenge} reveals "
                    f"(threshold {every if every > 0 else 'off'})")
            except Exception as e:
                logger.warning(f"[rotate] could not record the challenge budget: {e}")

        # An EXPLICIT rotate_every of 0 means «this account, full stop» —
        # not even a challenge moves it.
        #
        # The operator who sets it has a reason: today's was one phone in
        # hand. Rotating away after a challenge would answer the code on the
        # account that can be answered and then hop to one that cannot, where
        # the run parks for six hours. With rotation off, a challenge pauses
        # on the same account, the code goes in, and the run continues here.
        # The server default (override None) keeps the old behaviour: a
        # challenge still forces a move.
        if override == 0:
            if forced:
                logger.info(f"[rotate] pinned to {self.active_phone} (rotate_every=0) — "
                            f"staying despite the challenge")
                self._force_rotate = False
            return False

        # every <= 0 from the SERVER setting disables the threshold, but never
        # the challenge response: being asked for a code is Divar telling us
        # to move.
        if not forced:
            if every <= 0:
                return False
            # The account's total, not this job's slice. A fresh scraper is
            # built per job, so the in-memory counter restarted every run while
            # the account kept spending — which is why «۱۰۰ تا برای هر شماره»
            # never actually happened on a series of short jobs.
            spent = max(await self._account_reveals(), self._reveals_since_rotation)
            if spent < every:
                return False

        # Re-read the pool each time rather than caching it for the whole run:
        # a long job outlives the account list, so an account added or marked
        # invalid mid-run was previously never seen. Empty included: keeping
        # the cached list when the fresh one is empty rotated onto numbers
        # that had since been switched off or gone bad.
        self._rotation_pool = await self._load_rotation_pool()
        if len(self._rotation_pool) < 2:
            # Only one account exists — there is nothing to rotate to, and that
            # will not change by asking again on the next listing. Clear the
            # counters so this does not re-run the pool query every time.
            #
            # Say it in the run's own log, once. «چرخش هر ۱۰ شماره‌گیری» was
            # asked for and silently did not happen, and the only visible
            # consequence was listings whose number «گرفته نشد» — the cause
            # named nowhere. The pool is the caller's own accounts, so a
            # colleague's number does not count (that is the ownership rule,
            # not a bug): what the operator needs to hear is «add a second
            # number of your own, or the same one keeps spending».
            if not getattr(self, "_warned_no_rotation", False):
                self._warned_no_rotation = True
                try:
                    spent = max(await self._account_reveals(), self._reveals_since_rotation)
                except Exception:
                    spent = self._reveals_since_rotation
                logger.warning(
                    f"[rotate] nothing to rotate to — {self.active_phone} is the only "
                    f"account in this run's pool ({spent} reveals)")
                if getattr(self, "current_job", None):
                    try:
                        from app.services import job_log
                        await job_log.record(
                            self.current_job.job_id, job_log.CHALLENGE,
                            f"چرخش شماره انجام نشد: {self.active_phone} تنها حساب دیوار این اجراست "
                            f"({spent} شماره‌گیری). برای چرخش، یک حساب دیوار دیگر به نام خودتان اضافه کنید.",
                            level="warning")
                    except Exception as e:
                        logger.warning(f"[rotate] could not record the no-rotation note: {e}")
            self._reveals_since_rotation = 0
            self._force_rotate = False
            return False

        # pick the next phone after the current one
        try:
            idx = self._rotation_pool.index(self.active_phone) if self.active_phone in self._rotation_pool else -1
        except ValueError:
            idx = -1
        # Carry the current account's session forward before leaving it, so
        # returning later resumes it instead of replaying a stale snapshot.
        await self._persist_active_session()

        # If Divar challenged this one, its budget is gone whatever the counter
        # says — bank that, or «least spent first» would hand it straight back.
        if forced and every > 0:
            await self._mark_account_spent(self.active_phone, every)

        # A dead browser cannot be rotated onto. Without this the loop tried
        # every account in turn against a closed page — four accounts, twelve
        # seconds of polling each, and four healthy sessions reported as
        # expired at the end of it. The pool was never the problem.
        if not self.auth.browser_alive():
            logger.warning(
                "[rotate] the browser is gone — no account can be restored "
                "onto it. Leaving the pool untouched.")
            self._force_rotate = False
            return False

        for offset in range(1, len(self._rotation_pool) + 1):
            candidate = self._rotation_pool[(idx + offset) % len(self._rotation_pool)]
            if candidate == self.active_phone:
                continue
            if await self._switch_to(candidate, why="challenged" if forced else "threshold",
                                     every=every):
                return True
            logger.warning(f"[rotate] session for {candidate} not usable — trying next")

        # Every candidate failed. Leave the counter high so the next reveal
        # retries, but back it off a little: restoring a session navigates the
        # browser, and retrying that on every single listing would cost more
        # than the rotation saves.
        await self._return_to_active_browser()
        self._reveals_since_rotation = max(every - 5, 0) if every > 0 else 0
        self._force_rotate = False
        logger.info("[rotate] no alternative account could be restored; staying on current")
        return False

    async def _switch_to(self, candidate: str, *, why: str, every: int = 0) -> bool:
        """Move the run onto `candidate`: its browser profile, its session.

        True when the run is now on it. Shared by rotation and by a person
        asking for a number from the panel, so the two cannot drift — the
        manual switch is the same identity change, not a cheaper one.
        """
        try:
            # Switch the WHOLE identity, not just the user agent.
            #
            # Each account owns a browser profile now, and that profile is
            # where Divar's «this device already verified» lives. Swapping
            # only the UA would carry account B's cookies into account A's
            # localStorage, IndexedDB and device id — one machine claiming
            # to be two people, which is worse than not rotating at all.
            #
            # So: hand the outgoing account's jar back, close its profile,
            # open the candidate's. _open_browser_for does the device, the
            # proxy and the auth hand-off in one place.
            #
            # getattr throughout: the rotation tests build this object with
            # __new__, so nothing set in __init__ can be assumed.
            if getattr(self, "playwright", None) is not None:
                try:
                    _px = (await self._get_working_proxy(candidate)
                           if getattr(self, "proxy_enabled", False) else None)
                    await self._open_browser_for(candidate, _px)
                except Exception as e:
                    logger.warning(f"[rotate] could not open {candidate}'s profile: {e}")
                    return False
            else:
                _pg = getattr(self, "page", None)
                if _pg is not None and not _pg.is_closed():
                    await apply_device(_pg, Device.for_account(candidate))
            restored = await self.auth.restore_session(candidate)
        except Exception as e:
            logger.warning(f"[rotate] restore failed for {candidate}: {e}")
            restored = False
        if not restored:
            return False

        previous = self.active_phone
        self.active_phone = candidate
        # Say which account we moved to, in the run log, so rotation
        # can be watched live instead of inferred from reveal counts
        # after the fact.
        # getattr: the rotation tests build this object with __new__,
        # so nothing set in __init__ can be assumed to exist — the same
        # reason _persist_active_session guards its own attributes.
        _jid = getattr(self, "_job_id_str", None)
        if _jid:
            from app.services import job_log as _jl
            msg = (f"تعویض دستی شماره: از {previous or '—'} به {candidate}"
                   if why == "manual" else
                   f"چرخش شماره: از {previous or '—'} به {candidate}")
            await _jl.record(_jid, _jl.SESSION, msg,
                             previous=previous, now=candidate, why=why)

        # Save the jar the browser just refreshed.
        #
        # Restoring a session makes Divar hand back a new sAccessToken —
        # the short-lived half of a SuperTokens session, good for about
        # an hour. Persisting it immediately means the stored jar is the
        # fresh one, so the panel stops reporting a session it cannot
        # verify and, more usefully, the direct httpx calls that replay
        # /postlist/w/search carry a token Divar will still accept.
        #
        # Without this the stored jar kept whatever token it had at
        # login, and every request made outside the browser used it.
        await self._persist_active_session()
        # Reset here, not before the attempt. Resetting up front meant a
        # rotation that could not find a working session still consumed
        # the whole window, so the next try was a full threshold away —
        # on the very account that had just proved it needed replacing.
        from app import metrics as _mx
        _mx.scrape_rotations.labels(why).inc()
        self._reveals_since_rotation = 0
        self._force_rotate = False
        # Begin a new round only when there is genuinely nothing left.
        #
        # This used to infer it: "if the one we just moved to is already
        # spent, every account is". With five accounts that is simply
        # not true, and it was wrong on the very first challenge —
        #
        #   [rotate] Divar challenged 09017852452 after 1 reveals
        #   [rotate] 09017852452 marked spent after a Divar challenge
        #   [rotate] every account had spent its budget — new round for 5
        #
        # Resetting every counter to zero erases the "least reveals
        # first" ordering that rotation is built on, so the pool stops
        # spreading load and ping-pongs between whichever two accounts
        # it happens to pick. Two of five accounts were never used at
        # all across an entire run.
        if every > 0 and await self._unspent_account_count(every) == 0:
            await self._rest_all_accounts()
        logger.info(
            f"[rotate] switched Divar account {previous} → {candidate}"
            f"{' (Divar asked it for a code)' if why == 'challenged' else ''}"
            f"{' (asked for from the panel)' if why == 'manual' else ''}")
        # Let the restored session settle before it starts opening ads.
        # A switch followed instantly by a page load is the part that
        # reads as automated, not the overall pace.
        await self._human_like_delay(3.0, 6.0)
        return True

    async def _return_to_active_browser(self) -> None:
        """After every candidate failed, put the browser back on the account
        the run is still on.

        _switch_to opens each candidate's profile before trying its session,
        so a round of failures left the browser in the LAST candidate's
        profile, with that candidate's jar, while active_phone still named the
        old account. Every save after that — _persist_active_session on the
        next rotation, on a verified code, on a recycle — wrote the
        candidate's cookies onto the old account's row, and the next code
        prompt typed the old number into a session that was not its own.
        """
        if getattr(self, "playwright", None) is None:
            return
        active = getattr(self, "active_phone", None)
        browser_on = getattr(self, "_browser_account", active)
        if browser_on == active and getattr(self, "context", None) is not None:
            return
        try:
            _px = (await self._get_working_proxy(active)
                   if getattr(self, "proxy_enabled", False) else None)
            await self._open_browser_for(active, _px)
            if active and not await self.auth.restore_session(active):
                logger.warning(f"[rotate] {active}'s session did not come back after a "
                               "failed rotation — reveals will be challenged")
        except Exception as e:
            logger.warning(f"[rotate] could not return to {active}'s profile: {e}")

    async def _take_switch_request(self):
        """A switch somebody asked for from the panel, consumed once."""
        jid = getattr(self, "_job_id_str", None)
        if not jid:
            return None
        from app.scraper import otp_store as _os
        return await _os.take_switch(jid)

    async def _switch_on_request(self, req: dict) -> bool:
        """Honour «use a different number» from the panel, at a safe point.

        The person asking is the one who knows what the pool does not: the
        SIM behind the current number is not in their hand, and every code
        Divar sends it goes nowhere. So this moves even when rotation is
        pinned (rotate_every = 0) and even when no threshold was reached —
        and after it, OTP prompts that were suppressed for this run are
        allowed again, because the new number can be answered.

        `phone` names a number, or is empty for «the next one of mine».
        Either way it must be the run owner's own, switched-on, valid number:
        the request is checked where it is made and again here, because the
        pool can change between the two.
        """
        target = (req or {}).get("phone") or None
        previous = self.active_phone
        _d = lambda p: "".join(ch for ch in str(p or "") if ch.isdigit())[-10:]  # noqa: E731

        # «move off X» when the run is no longer on X is already done:
        # switching again would leave a good number for nothing and spend a
        # profile swap and a session restore doing it.
        away_from = (req or {}).get("from_phone")
        if away_from and _d(away_from) != _d(previous):
            logger.info(f"[rotate] switch away from {away_from} dropped — the run is on {previous}")
            return False

        pool = await self._load_rotation_pool()
        self._rotation_pool = pool
        if target:
            if previous and _d(target) == _d(previous):
                await self._log_run(f"اجرا همین حالا روی {previous} است — تعویضی لازم نبود")
                return False
            if not await self._account_usable(target):
                await self._log_run(
                    f"تعویض به {target} انجام نشد: این شماره متعلق به صاحب اجرا نیست، "
                    "خاموش است یا نشستش معتبر نیست", level="warning", phone=target)
                return False
            candidates = [target]
        else:
            candidates = [p for p in pool if _d(p) != _d(previous)]
        if not candidates:
            await self._log_run(
                "تعویض شماره انجام نشد: شمارهٔ روشن و معتبر دیگری به نام صاحب اجرا نیست",
                level="warning")
            return False

        await self._persist_active_session()
        # Divar challenged the number being left: bank it as spent, the way a
        # rotation does, or «least spent first» hands it straight back.
        if getattr(self, "_force_rotate", False):
            override = getattr(self, "_rotate_every_override", None)
            every = override if override is not None else (getattr(settings, "cookie_rotate_every", 0) or 0)
            if every > 0 and previous:
                await self._mark_account_spent(previous, every)
        if not self.auth.browser_alive() and getattr(self, "playwright", None) is None:
            await self._log_run("تعویض شماره انجام نشد: مرورگر اسکرپر بسته شده است",
                                level="warning")
            return False

        for cand in candidates:
            if await self._switch_to(cand, why="manual"):
                jid = getattr(self, "_job_id_str", None)
                if jid:
                    # The previous number's unanswered prompts said nothing
                    # about this one. Let it be asked, and answered.
                    from app.scraper import otp_store as _os
                    await _os.reset_cancel(jid)
                    await _os.clear_timeouts(jid)
                return True
            logger.warning(f"[rotate] manual switch: {cand} not usable — trying next")

        await self._return_to_active_browser()
        await self._log_run(
            f"تعویض شماره انجام نشد: نشست {'، '.join(candidates)} بازیابی نشد — "
            f"اجرا روی {previous or '—'} ادامه می‌دهد", level="warning")
        return False

    # Filter names as the person who set them sees them in the panel. The
    # tally buckets on the reason string are English field names, which are
    # useless in a message whose whole job is to say «this is the filter that
    # cost you the listings».
    _FILTER_LABELS_FA = {
        # Not filters, but they share the panel's label lookup. «failed» had
        # no label at all, so /jobs/{id}/skipped showed the English word.
        "failed": "ناموفق",
        "no_phone": "بدون شماره",
        "chat_only": "فقط چت دیوار",
        "needs_identity": "نیاز به تأیید هویت دیوار",
        "deposit": "ودیعه",
        "rent": "اجارهٔ ماهانه",
        "price": "قیمت",
        "price/m²": "قیمت هر متر",
        "area": "متراژ",
        "rooms": "تعداد اتاق",
        "posted": "تاریخ انتشار",
        "posted_at": "تاریخ انتشار",
        "advertiser_type": "نوع آگهی‌دهنده",
        "has_images": "داشتن عکس",
        "has_elevator": "آسانسور",
        "has_parking": "پارکینگ",
        "has_storage": "انباری",
        "has_balcony": "بالکن",
        "category": "خارج از دسته‌بندی",
        "deleted": GONE_FROM_DIVAR,
    }

    # Divar's own words for what kind of ad this is, mapped to the two the
    # validator knows. 'buy' is the scraper's term and 'sale' is the
    # validator's -- without this every sale listing would be graded against
    # the rent rules and reported as missing a rent price.
    _VALIDATOR_TYPES = {"buy": "sale", "sale": "sale", "rent": "rent"}

    # An agency posting as a private seller — Divar says «شخصی», the ad's own
    # words say «املاک هستم» — is kept and labelled, never dropped. What a run
    # owes the person who set it going is the number, so it is counted here and
    # written to the report at the end. Per job, so nothing carries over to the
    # next run on the same scraper.
    def _count_agency_posing(self, evidence: Optional[str]) -> None:
        job = getattr(self, "current_job", None)
        key = str(getattr(job, "job_id", None))
        tally: Dict[str, Dict[str, int]] = getattr(self, "_agency_posing", None) or {}
        self._agency_posing = tally
        per_phrase = tally.setdefault(key, {})
        phrase = evidence or "؟"
        per_phrase[phrase] = per_phrase.get(phrase, 0) + 1

    async def _report_agency_posing(self, job) -> None:
        """«n آگهی با برچسب «شخصی» دیوار، در متنشان مشاور املاک بود» — once, at
        the end of the run, and nothing when there were none."""
        per_phrase = (getattr(self, "_agency_posing", None) or {}).pop(
            str(getattr(job, "job_id", None)), None)
        if not per_phrase:
            return
        n = sum(per_phrase.values())
        from app.services import job_log
        await job_log.record(
            job.job_id, job_log.PAGE,
            f"{n} آگهی با برچسب «شخصی» دیوار، در متنشان مشاور املاک بود — "
            "حذف نشدند و با برچسب «املاکی» علامت خوردند",
            agency_looks_personal=n, evidence=per_phrase)

    def _grade_property(self, property_data: Dict[str, Any]) -> None:
        """Score a listing against PropertyDataValidator and attach the result.

        Writes quality_score / quality_issues onto property_data so they are
        saved with the row. Never raises and never changes any other field: a
        grading failure must not cost us a listing.
        """
        try:
            from app.scraper.property_validator import validate_property_data

            kind = self._VALIDATOR_TYPES.get(
                (property_data.get("listing_type") or "").lower())
            if kind is None:
                # An unknown category tells us nothing about which rules apply,
                # and guessing 'rent' would invent missing-rent errors on ads
                # that never had a rent. Leave it ungraded -- NULL is honest.
                return

            result = validate_property_data(property_data, property_type=kind)
            issues = list(result.errors) + list(result.warnings)
            property_data["quality_score"] = round(float(result.confidence_score), 3)
            # "" (not None) when the listing is clean, so re-scraping a
            # listing that has since been fixed clears its old flags: the
            # update path skips None values, and would otherwise keep stale
            # issue text on a row that no longer has any.
            # NULL therefore means "never graded", "" means "graded, clean".
            property_data["quality_issues"] = "؛ ".join(issues)[:2000]

            from app import metrics as _mx
            _mx.scrape_confidence.observe(result.confidence_score)
            _mx.scrape_quality.labels("complete" if result.is_valid else "flagged").inc()
            if not result.is_valid:
                logger.info(
                    f"Quality flags on {property_data.get('divar_id')} "
                    f"({kind}, score {result.confidence_score:.2f}): "
                    f"{'; '.join(result.errors)}")
        except Exception as e:
            logger.warning(f"Could not grade {property_data.get('divar_id')}: {e}")

    # How many moves to keep per listing. A price trail is read one property
    # at a time and the recent moves are the ones anybody acts on, so this is
    # bounded rather than unbounded — an eighteen-month history of a flat
    # relisted weekly is a row nobody wants to load.
    PRICE_TRAIL_MAX = 24

    # The fields a "price" can live in, by listing type. Divar puts a sale
    # price in total_price and a rental in rent+deposit, and a rental whose
    # deposit moves while the rent holds has still moved.
    _PRICE_FIELDS = ("total_price", "price", "rent_price", "deposit")

    def _record_price_move(self, existing, incoming: Dict[str, Any]) -> None:
        """Append to the price trail when a figure actually changed.

        Only on a real change. Writing a row on every scrape would add a
        thousand identical entries a day and bury the handful that mean
        something. Never raises: losing the trail for one listing is a
        regrettable gap, losing the listing is worse.
        """
        try:
            moved = {}
            for field in self._PRICE_FIELDS:
                new = incoming.get(field)
                if new is None:
                    continue
                old = getattr(existing, field, None)
                if old is not None and int(new) != int(old):
                    moved[field] = {"from": int(old), "to": int(new)}

            if not moved:
                return

            now = datetime.now()
            trail = list(getattr(existing, "price_history", None) or [])
            trail.append({
                "at": now.isoformat(),
                **{k: v["to"] for k, v in moved.items()},
                "from": {k: v["from"] for k, v in moved.items()},
            })
            existing.price_history = trail[-self.PRICE_TRAIL_MAX:]
            existing.price_changed_at = now

            # previous_price tracks the headline figure only — the one a
            # «قیمت کم شد» alert is about. A deposit shuffle on a rental is in
            # the trail but does not pretend to be a price cut.
            headline = moved.get("total_price") or moved.get("price")
            if headline:
                existing.previous_price = headline["from"]
                direction = "کاهش" if headline["to"] < headline["from"] else "افزایش"
                logger.info(
                    f"[price] {existing.divar_id}: {direction} "
                    f"{headline['from']:,} → {headline['to']:,}")
        except Exception as e:
            logger.warning(f"could not record the price move: {e}")

    async def property_exists(self, divar_id: str) -> bool:
        """Whether this listing is stored AND already has what we came for.

        A stored row with no phone number is not a duplicate worth skipping —
        it is a gap, and skipping it is why the gaps never close. 378 of 1209
        saved properties had no number, every one of them unreachable by any
        re-run, because the first thing the loop did was see the divar_id and
        move on.

        Phone numbers are the point of this scraper. A listing we have but
        cannot call is worth the second visit; one we have complete is not.
        """
        try:
            result = await self.db_session.execute(
                select(Property).where(Property.divar_id == divar_id)
            )
            row = result.scalar_one_or_none()
            if row is None:
                return False
            if not (row.phone_number or "").strip():
                if getattr(row, "contact_channel", None) == "chat_only":
                    # Not a gap. The poster hid the number; a second visit
                    # spends a reveal and produces the same nothing.
                    return True
                logger.info(
                    f"{divar_id} is already stored but has no phone number — "
                    "re-scraping to fill it in")
                return False
            return True
        except Exception as e:
            logger.error(f"Failed to check property existence: {e}")
            return False

    # At most this many numberless listings are retried per run, on top of
    # the run's own candidates: each one costs a reveal, and reveals are what
    # bring Divar's code prompts. A run asked for fewer takes fewer.
    PHONE_RETRIES_PER_RUN = 20

    @staticmethod
    def _set_counts(job, **counts) -> None:
        """Write the run's counters on its row. The one place the models'
        Column[int] typing is bridged, rather than an ignore on every line
        that moves a counter."""
        for name, value in counts.items():
            setattr(job, name, value)

    async def _with_phone_retries(self, job, pool: List[Dict[str, Any]],
                                  max_items: Optional[int]):
        """The pool with the numberless listings owed a retry put first.

        Owed: saved without a number by an earlier run of this city and
        category started by the same person — the account budget spent on
        them is that person's — while still stored and still numberless
        (skipped_listings.awaiting_phone). Returns (pool, their ids). Never
        raises: a retry that cannot be looked up must not cost the run.
        """
        try:
            cap = min(self.PHONE_RETRIES_PER_RUN, max_items) if max_items else self.PHONE_RETRIES_PER_RUN
            owed = await skipped_listings.awaiting_phone(
                self.db_session, city_id=job.city_id, category_id=job.category_id,
                owner_user_id=(job.config or {}).get("owner_user_id"), limit=cap)
        except Exception as e:
            logger.warning(f"[retry] numberless listings not looked up: {e}")
            try:
                await self.db_session.rollback()
            except Exception:
                pass
            return pool, set()
        if not owed:
            return pool, set()
        ids = {o["divar_id"] for o in owed}
        first = [{"divar_id": o["divar_id"], "title": o.get("title"),
                  "url": o.get("url") or f"https://divar.ir/v/{o['divar_id']}"} for o in owed]
        logger.info(f"[retry] {len(first)} listing(s) saved without a number earlier — trying them first")
        from app.services import job_log
        await job_log.record(
            job.job_id, job_log.PAGE,
            f"{len(first)} آگهیِ بدون شماره از اجراهای قبلیِ همین شهر و دسته دوباره "
            "برای شماره امتحان می‌شود — اول از همه، و بدون فیلترهای این اجرا",
            retries=len(first))
        return first + [lst for lst in pool if lst["divar_id"] not in ids], ids

    async def save_property(self, property_data: Dict[str, Any]) -> Optional[Property]:
        """Save property to database, surviving a dropped connection.

        Issue #10: run 92 scraped 144 listings and lost five AT THE SAVE —
        three InterfaceError, two PendingRollbackError. Those are one fault
        seen twice: the connection going away under an in-flight statement,
        and then the session still holding the transaction that just died.
        The listing had been fully scraped; a reveal had been spent on it; and
        it was counted «failed» over a socket.

        With NullPool the next statement after a rollback opens a fresh
        connection, so one retry on a clean session is the whole fix. Only
        connection-class errors are retried — a constraint violation would
        fail the same way twice and is not a socket's fault.
        """
        # Whether this call rolled the RUN's session back.
        #
        # The rollback below is what makes the retry work, and it also expires
        # every ORM object on that session — including `job`. The caller's very
        # next line is `job.new_items += 1`, and touching an expired attribute
        # triggers a synchronous lazy reload, which outside a greenlet is
        # MissingGreenlet. Run 109 died at listing 7 that way: the retry had
        # already succeeded («Updated property: gajmvaRR») and the counter
        # after it killed the run.
        self._last_save_rolled_back = False
        for attempt in (1, 2):
            try:
                return await self._save_property_attempt(property_data)
            except _DroppedConnection as e:
                self._last_save_rolled_back = True
                try:
                    await self.db_session.rollback()
                except Exception:
                    pass
                if attempt == 1:
                    logger.warning(
                        f"[save] connection dropped mid-save "
                        f"({type(e.cause).__name__}) — retrying on a fresh one")
                    await asyncio.sleep(0.5)
                    continue
                logger.error(f"[save] dropped twice: {e.cause}")
                self._last_save_error = f"{type(e.cause).__name__} (×2)"
                return None
        return None

    async def _number_recovered(self, prop: Property) -> None:
        """A property has a phone number: make every other record agree.

        «اسکرپ کرد ولی باید همه جاهایی که مربوط می‌شه اضافه بشه و از این بخش
        هم حذف بشه.» The property row was the only thing a re-scrape used to
        update. The lead kept its «---», and the skipped list kept offering
        the listing for a retry it no longer needed, with the count beside
        «بدون شماره» never going down. Called on every successful save, so a
        single scrape, a resume and the next full run all heal the same way.
        Never raises: a listing saved must not be un-saved over bookkeeping.
        """
        if not (getattr(prop, "phone_number", None) or "").strip():
            return
        try:
            from app.crm.lead_service import fill_lead_from_property
            await fill_lead_from_property(self.db_session, prop)
        except Exception as e:
            logger.warning(f"[recovered] lead not filled for {prop.divar_id}: {e}")
        try:
            await skipped_listings.resolve(prop.divar_id)
        except Exception as e:
            logger.warning(f"[recovered] skipped rows not cleared for {prop.divar_id}: {e}")

    async def _save_property_attempt(self, property_data: Dict[str, Any]) -> Optional[Property]:
        """One attempt. Sets _last_save_error on every path that gives up, so
        the run can put the reason on the skipped row — «ذخیره نشد» is the
        observation, not the cause. Raises _DroppedConnection for the one
        kind of failure the caller can do something about."""
        self._last_save_error = None
        # Which of the two things this call did. The caller cannot tell from
        # the returned Property — an update and an insert both return a row —
        # and it was counting every success as «جدید».
        self._last_save_created = False
        try:
            divar_id = property_data.get('divar_id')
            
            # Validate required fields
            if not divar_id:
                logger.warning("Cannot save property: missing divar_id")
                self._last_save_error = "شناسهٔ آگهی نبود"
                return None

            if not property_data.get('title'):
                logger.warning(f"Cannot save property {divar_id}: missing title")
                self._last_save_error = "عنوان نبود"
                return None

            if not property_data.get('url'):
                logger.warning(f"Cannot save property {divar_id}: missing url")
                self._last_save_error = "آدرس نبود"
                return None
            
            # Check if exists
            result = await self.db_session.execute(
                select(Property).where(Property.divar_id == divar_id)
            )
            existing = result.scalar_one_or_none()
            
            if existing:
                # Record the move BEFORE the overwrite below destroys it.
                #
                # This is the whole point: a listing is re-scraped, the loop
                # underneath setattr()s the new price over the old one, and the
                # previous figure is gone. Every price drop this database has
                # ever seen was thrown away at exactly this line, on a schedule.
                self._record_price_move(existing, property_data)

                # Update existing — only update owner_phone if it's not set yet
                for key, value in property_data.items():
                    if hasattr(existing, key) and value is not None:
                        setattr(existing, key, value)
                if self.active_phone and not existing.owner_phone:
                    existing.owner_phone = self.active_phone
                # Sync has_images flag
                if existing.images:
                    existing.has_images = True
                existing.updated_at = datetime.now()
                # The row gained a number it did not have. Everything that
                # was told it had none has to hear: the lead the CRM shows,
                # and the skipped list that still offers it for a retry.
                await self._number_recovered(existing)
                await self.db_session.commit()
                self._last_save_created = False
                logger.info(f"Updated property: {divar_id}")
                return existing
            else:
                # Create new
                property_data['tag_number'] = self._generate_tag_number()
                property_data['serial_no'] = await allocate_serial_no(self.db_session)
                property_data['scraped_at'] = datetime.now()
                if self.active_phone:
                    property_data['owner_phone'] = self.active_phone

                # Remove non-model fields
                property_data.pop('descriptions', None)
                property_data.pop('category_hint', None)

                new_property = Property(**property_data)
                self.db_session.add(new_property)
                await self.db_session.commit()
                self._last_save_created = True
                logger.info(f"Saved new property: {divar_id} with tag {property_data['tag_number']}")
                # A listing recorded as skipped in an earlier run and created
                # fresh now is not skipped any more either.
                await self._number_recovered(new_property)

                # Trigger CRM pipeline (lead + notification)
                try:
                    from app.crm.pipeline import process_new_property
                    await process_new_property(self.db_session, new_property)
                except Exception as crm_err:
                    logger.warning(f"CRM pipeline error (non-fatal): {crm_err}")

                return new_property
                
        except Exception as e:
            if _is_dropped_connection(e):
                raise _DroppedConnection(e)
            logger.error(f"Failed to save property: {e}")
            self._last_save_error = type(e).__name__
            await self.db_session.rollback()
            return None
    
    async def start_scraping_job(
        self,
        city: str,
        category: str,
        max_items: int = 100,
        download_images: bool = True,
        job_id: Optional[str] = None,
        min_price: Optional[int] = None,
        max_price: Optional[int] = None,
        min_deposit: Optional[int] = None,
        max_deposit: Optional[int] = None,
        min_rent: Optional[int] = None,
        max_rent: Optional[int] = None,
        min_price_per_meter: Optional[int] = None,
        max_price_per_meter: Optional[int] = None,
        min_area: Optional[int] = None,
        max_area: Optional[int] = None,
        min_rooms: Optional[int] = None,
        max_rooms: Optional[int] = None,
        has_images: Optional[bool] = None,
        has_elevator: Optional[bool] = None,
        has_parking: Optional[bool] = None,
        has_storage: Optional[bool] = None,
        has_balcony: Optional[bool] = None,
        advertiser_type: Optional[str] = None,
        max_age_hours: Optional[int] = None,
        posted_date: Optional[str] = None,
        rotate_every: Optional[int] = None,
        urls: Optional[List[str]] = None,
        divar_filters: Optional[Dict[str, Any]] = None,
    ) -> ScrapingJob:
        """Start a complete scraping job for a city and category.

        With `urls`, the pool is those listings and nothing is collected: the
        same run — the same reveal, OTP prompt, pacing, rotation, log and
        skipped-list bookkeeping — pointed at an explicit list. This is what
        «اسکرپ تکی» and «بازاسکرپ همه» are now: a job of one, or of many, so a
        code prompt does not have to be answered inside an HTTP request and
        the finish line says what actually happened.
        """

        # Date mode: scrape listings published on this exact day. There,
        # max_items is an optional cap (None = the whole day); in normal
        # mode it falls back to 100.
        target_day = None
        if posted_date:
            try:
                target_day = datetime.fromisoformat(posted_date).date()
            except ValueError:
                logger.warning(f"Invalid posted_date {posted_date!r} — ignoring")
        date_mode = target_day is not None
        # per-job cookie-rotation interval (None → server default)
        if rotate_every is not None:
            self._rotate_every_override = max(int(rotate_every), 0)
        if not date_mode:
            max_items = max_items or 100
        
        # Get or create job record
        if job_id:
            # Use existing job
            result = await self.db_session.execute(
                select(ScrapingJob).where(ScrapingJob.job_id == job_id)
            )
            job = result.scalar_one_or_none()
            if not job:
                raise ValueError(f"Job {job_id} not found")
            # A job can be cancelled while still «pending» — the background task
            # starts a moment later, and claiming "running" here would bring a
            # job the user already stopped back to life. One conditional write,
            # not a read and then a write: a cancel committed in between was
            # written over.
            self.current_job = job
            started = await self._move_status("running", only_from=("pending",))
            await self.db_session.commit()
            if not started:
                logger.info(f"Job {job_id} was {job.status} before it started — not running it")
                return job
            job.started_at = datetime.now()
            self._note_account(job)
            from app.services import job_log
            self._job_id_str = str(job.job_id)
            # The browser has just restored a session and Divar handed back a
            # fresh access token; store it before anything makes an HTTP call
            # with the old one.
            await self._persist_active_session()
            await job_log.prune()
            await skipped_listings.prune()
            await job_log.record(job.job_id, job_log.START, "اسکرپ شروع شد")
        else:
            # Create new job record
            job = ScrapingJob(
                status="running",
                started_at=datetime.now()
            )
        
        # Get city and category IDs
        city_result = await self.db_session.execute(
            select(City).where(City.slug == city)
        )
        city_obj = city_result.scalar_one_or_none()
        if city_obj:
            job.city_id = city_obj.id
        
        cat_result = await self.db_session.execute(
            select(Category).where(Category.slug == category)
        )
        cat_obj = cat_result.scalar_one_or_none()
        if cat_obj:
            job.category_id = cat_obj.id
        
        self.db_session.add(job)
        await self.db_session.commit()
        self.current_job = job
        
        try:
            active_filters = {k: v for k, v in {
                'min_price': min_price, 'max_price': max_price,
                'min_deposit': min_deposit, 'max_deposit': max_deposit,
                'min_rent': min_rent, 'max_rent': max_rent,
                'min_price_per_meter': min_price_per_meter, 'max_price_per_meter': max_price_per_meter,
                'min_area': min_area, 'max_area': max_area,
                'min_rooms': min_rooms, 'max_rooms': max_rooms,
                'has_images': has_images, 'has_elevator': has_elevator,
                'has_parking': has_parking, 'has_storage': has_storage,
                'has_balcony': has_balcony, 'advertiser_type': advertiser_type,
                'max_age_hours': max_age_hours, 'posted_date': posted_date,
                'divar_filters': divar_filters or None,
            }.items() if v is not None}
            logger.info(f"Starting scraping job for {city}/{category} | filters={active_filters}")
            # Every filter as the form holds it, for plan_filters (#27).
            _filter_kw: Dict[str, Any] = dict(
                advertiser_type=advertiser_type, has_images=has_images,
                min_price=min_price, max_price=max_price,
                min_deposit=min_deposit, max_deposit=max_deposit,
                min_rent=min_rent, max_rent=max_rent,
                min_price_per_meter=min_price_per_meter, max_price_per_meter=max_price_per_meter,
                min_area=min_area, max_area=max_area,
                min_rooms=min_rooms, max_rooms=max_rooms,
                has_elevator=has_elevator, has_parking=has_parking,
                has_storage=has_storage, has_balcony=has_balcony,
                posted_date=posted_date, max_age_hours=max_age_hours,
                divar_filters=divar_filters,
            )
            _plan = None

            # ── Collect listings ────────────────────────────────────────────────
            # max_items is the number of *kept* (post-filter) listings the user
            # asked for. Off-category (زمین/باغ) and filter drops mean we must
            # collect a larger candidate pool and keep scraping until max_items
            # are actually saved, then stop.
            if date_mode:
                # Listings feeds are newest-first; the collector paginates
                # until the feed cursor moves past the target day, so the pool
                # covers every post of that day (however many there are)
                cap_label = f"up to {max_items}" if max_items else "ALL"
                logger.info(f"Target: {cap_label} listings posted on {target_day}")
                collect_target = 400  # DOM-phase batch; API phase is date-driven
            else:
                logger.info(f"Target: {max_items} NEW listings")
                # The target counts new listings only — anything already in the
                # database is an update and does not advance it. A pool the size
                # of the target could therefore only satisfy it on a city that
                # had never been scraped, and this was additionally capped at
                # 200, so asking for 200 new could never return 200 new.
                # Twice what the run asked for plus a page, capped. It was
                # max_items × 5, so a run for 50 first collected 250 — most of
                # a city — before opening its first listing. Filters drop
                # candidates, so more than asked is still collected; five times
                # more was the wait, not the safety.
                collect_target = min(max(max_items * 2 + 24, 60), 1500)

            # Hand Divar every filter the category has, before the feed is
            # loaded — and nothing it does not: one filter too many and Divar
            # refuses the search from its second page on (#27). The category's
            # form comes from Divar itself (app/services/divar_filters.py);
            # what it leaves out is said in the log and checked per listing
            # after the ad is opened.
            try:
                from app.services import divar_count as _dc
                from app.services import divar_filters as _df
                await _df.current()
                _plan = _dc.plan_filters(category, **_filter_kw)
                self._search_query = _plan.query
                # The same filters in the shape the search API takes, for the
                # collection that no longer needs a browser.
                self._search_form = _plan.form
                # An explicit list searches nothing, so there is nothing to say.
                if not urls and self._search_query:
                    logger.info(f"[collect] Divar-side filters: {self._search_query}")
                    await job_log.record(
                        job.job_id, job_log.PAGE,
                        f"فیلترها به خود دیوار داده شد: {self._search_query}",
                        query=self._search_query)
                if not urls and _plan.recent_ads and posted_date:
                    await job_log.record(
                        job.job_id, job_log.PAGE,
                        f"تاریخ انتشار به دیوار به‌صورت «آگهی‌های اخیر: {_plan.recent_ads}» "
                        "داده شد تا فهرست کوتاه‌تر شود؛ روز دقیق را اسکرپر خودش بررسی می‌کند",
                        recent_ads=_plan.recent_ads)
                for _note in ([] if urls else _plan.notes):
                    logger.info(f"[collect] filter not sent: {_note}")
                    await job_log.record(job.job_id, job_log.PAGE, _note, level="warning")
            except Exception as e:
                # A filter we cannot express is not a reason to abandon the run;
                # it just means the local pass does more work, as before.
                logger.warning(f"[collect] could not build the Divar query: {e}")
                self._search_query = ""
                _plan = None

            if urls:
                # An explicit list: no search, no collection, no count. The
                # listings are whatever was handed over, in that order.
                all_listings = []
                _seen = set()
                for u in urls:
                    tok = self._token_from_url(u)
                    if tok and tok not in _seen:
                        _seen.add(tok)
                        all_listings.append({
                            "divar_id": tok, "url": f"https://divar.ir/v/{tok}",
                            "title": None, "descriptions": [],
                        })
                self._collect_stop = ("explicit", None)
                await job_log.record(
                    job.job_id, job_log.PAGE,
                    f"{len(all_listings)} آگهی از فهرست داده‌شده — بدون جست‌وجو",
                    collected=len(all_listings), via="urls")
            else:
                all_listings = await self._collect_listings_robust(
                    city, category, collect_target,
                    until_day=target_day if date_mode else None,
                )
            seen_ids: set = {lst['divar_id'] for lst in all_listings}

            # Collection is the heaviest thing the browser ever does: the feed
            # page ends up holding hundreds of rendered cards and their images,
            # and Chromium does not hand that back when we navigate away. The
            # detail phase then runs hundreds more navigations on top of it.
            # Starting that phase in a fresh browser is what keeps a 500-item
            # run costing the same as a 50-item one.
            if len(all_listings) > 40:
                await self._recycle_browser(
                    f"listing collection finished ({len(all_listings)} candidates)")

            # ── Did collection finish, or was it cut off? ──────────────────────
            #
            # Until now these were the same thing: the collector returned a list
            # and the caller had no way to ask whether that list was everything.
            # A run that Divar refused at listing 42 of 250 looked exactly like a
            # run that had genuinely reached the end of the feed, and both
            # reported «تکمیل شده».
            from app.services import job_log
            _stop, _detail = (self._collect_stop or ("unknown", None))
            _short = len(all_listings) < collect_target

            if _stop == "refused" or (_stop in self._CUT_SHORT and not all_listings):
                # Nothing to walk: Divar stopped the walk dead, or it broke
                # before a single listing came back. A failed run, not a short
                # one — saying otherwise was the bug a refusal used to be.
                if _stop == "refused":
                    counts = ", ".join(f"HTTP {k}×{v}" for k, v in sorted((_detail or {}).items()))
                    msg = (f"دیوار در حین جمع‌آوری آگهی‌ها دسترسی را رد کرد ({counts}). "
                           f"فقط {len(all_listings)} آگهی از فهرست خوانده شد — "
                           "این اسکرپ کامل نیست.")
                elif _stop == "error" and not isinstance(_detail, dict):
                    msg = (f"جمع‌آوری فهرست آگهی‌ها با خطا متوقف شد ({_detail}). "
                           f"{len(all_listings)} آگهی تا آن لحظه خوانده شده بود.")
                else:
                    msg = (f"جمع‌آوری فهرست آگهی‌ها متوقف شد: "
                           f"{self._what_cut_collection(_stop, _detail, collect_target)}. "
                           "هیچ آگهی‌ای از فهرست خوانده نشد.")
                logger.error(f"[collect] {msg}")
                await job_log.record(job.job_id,
                                     job_log.CHALLENGE if "refused" in _stop else job_log.ERROR,
                                     msg, level="error", collected=len(all_listings),
                                     target=collect_target,
                                     refusals=_detail if _stop == "refused" else None)
                # Conditional, like every status write of a run: a cancel
                # pressed during a minutes-long collection stays a cancel.
                if await self._move_status("failed"):
                    job.error_message = msg
                    job.finish_reason = msg
                    job.completed_at = datetime.now()
                await self.db_session.commit()
                return job

            if _stop in self._CUT_SHORT:
                # Short, with listings in hand. They are walked all the same,
                # and the run ends «ناقص» unless it meets its target on them —
                # never «تکمیل شده» with «بیشتر از این در دیوار نبود» (#28).
                await self._record_cut_short(job.job_id, _stop, _detail,
                                             collected=len(all_listings), pool=collect_target,
                                             asked=max_items)
            elif _short and _stop == "exhausted":
                # Not refused and not an error: the feed really did run out.
                # Still worth saying, because «۴۲ از ۲۵۰» with no explanation is
                # what made this look broken.
                await job_log.record(
                    job.job_id, job_log.PAGE,
                    f"فهرست دیوار با این فیلترها {len(all_listings)} آگهی داشت "
                    f"(ظرفیت جست‌وجو {collect_target} بود) — بیشتر از این در دیوار نبود",
                    collected=len(all_listings), target=collect_target, stop=_stop)
            else:
                await job_log.record(
                    job.job_id, job_log.PAGE,
                    f"{len(all_listings)} آگهی از فهرست جمع‌آوری شد",
                    collected=len(all_listings), target=collect_target)

            # What Divar itself says, asked again at run time and written down
            # beside what the listing page actually gave up. Not for an
            # explicit list — there is no search to count.
            #
            # «۱۱۳ آگهی با این فیلترها در دیوار هست» and «۱۱۹ نامزد جمع شد» are
            # answers to two different questions — an estimate Divar computed
            # when the button was pressed, and what the page yielded now — and
            # having only one of them on screen is what made the pair read as a
            # contradiction. Advisory only: it must not become the progress
            # bar's denominator, because it can be larger or smaller than the
            # pool the run actually walks, and either way the bar would lie.
            try:
                if urls:
                    raise StopAsyncIteration   # caught below: nothing to ask
                from app.services import divar_count as dc
                # The very form the collection searched with, so the two
                # numbers answer the same question.
                _form = _plan.form if _plan is not None else dc.build_form_data(category, **_filter_kw)
                _divar_total, _count_err = await dc.fetch_post_count(city, _form)
                if _divar_total is not None:
                    job.divar_count = int(_divar_total)
                    _gap = len(all_listings) - _divar_total
                    _msg = (f"دیوار می‌گوید {_divar_total} آگهی با این فیلترها دارد؛ "
                            f"{len(all_listings)} نامزد جمع شد")
                    if _gap:
                        _msg += f" — {abs(_gap)} تا " + ("بیشتر" if _gap > 0 else "کمتر")
                    await job_log.record(
                        job.job_id, job_log.PAGE, _msg,
                        divar_count=_divar_total, collected=len(all_listings))
                elif _count_err:
                    logger.info(f"[count] Divar's own total unavailable: {_count_err}")
            except StopAsyncIteration:
                pass
            except Exception as e:
                # Advisory. It must never cost a run.
                logger.warning(f"[count] could not ask Divar for its total: {e}")

            # Listings earlier runs of this city and category saved without a
            # phone number. Their skipped rows promise that the next run tries
            # again, and that was only ever true when Divar's feed happened to
            # hand the same listing over again — never, for a daily run of
            # another day (#32). They go first, so a run that meets its target
            # early still reaches them, and no filter of this run drops them
            # (see _skip below): the run that saved them already judged them.
            retry_ids: set = set()
            if not urls:
                all_listings, retry_ids = await self._with_phone_retries(
                    job, all_listings, max_items)
                # Already in the pool: a top-up page that brings one of them
                # again must not add it twice — a second reveal on the owner's
                # number, and a second «بدون شماره» row off its three tries.
                seen_ids |= retry_ids

            # «کل» is this run's own pool: what the loop walks (#29).
            #
            # It was Divar's count for the filters when Divar answered, and
            # that count ignores the day — Divar does not filter by it — so a
            # run for one day of 24 candidates read «251 / 251». Divar's number
            # stays on the row as divar_count, and the panel shows it beside
            # this one as «دیوار می‌گوید», where the two can be compared.
            self._set_counts(job, total_items=len(all_listings))
            await self.db_session.commit()

            logger.info(
                f"Collected {len(all_listings)} candidate listings; "
                + (f"keeping {cap_label} from {target_day}" if date_mode
                   else f"will keep scraping until {max_items} are saved")
            )
            # A run in date mode used to stop after 15 consecutive listings
            # published before the target day, on the theory that the day was
            # exhausted. It was not a safe inference: Divar interleaves promoted
            # and pinned posts, which are routinely older, so fifteen in a row
            # says nothing about how much of the day is left — and a listing
            # whose date could not be parsed neither broke the streak nor
            # extended it, so unparsed ones silently pushed it toward the limit.
            #
            # Nothing is needed in its place. The collection phase already
            # bounds itself by date rather than by count: with until_day set it
            # paginates until the feed cursor moves past the day, so the pool
            # holds that day and little else, and the publish-date filter below
            # drops whatever spills over. Walking the rest of a bounded pool
            # costs time; stopping early cost listings and reported success.
            # Why this run ended, in the user's words. Left None when the run
            # simply hit its target, which needs no explanation.
            finish_reason: Optional[str] = None
            # why listings were dropped, tallied by reason. A scrape that
            # saves nothing is otherwise indistinguishable from a broken one.
            skip_tally: Dict[str, int] = {}
            fail_tally: Dict[str, int] = {}
            # Listings Divar had deleted: neither a failure nor a filter's
            # doing, and every sentence built from skip_tally says «با فیلترها».
            gone = 0
            category_drops: List[str] = []   # a handful, for the log
            # Handed to each detail scrape so it can tell, before asking Divar
            # for contact info, whether this ad is going to be discarded anyway.
            _listing_type = CATEGORIES.get(category, {}).get('type', 'unknown')
            _pre_filters = {
                'advertiser_type': advertiser_type,
                'min_price': min_price, 'max_price': max_price,
                'min_deposit': min_deposit, 'max_deposit': max_deposit,
                'min_rent': min_rent, 'max_rent': max_rent,
                'min_price_per_meter': min_price_per_meter,
                'max_price_per_meter': max_price_per_meter,
                'min_area': min_area, 'max_area': max_area,
                'min_rooms': min_rooms, 'max_rooms': max_rooms,
                'has_elevator': has_elevator, 'has_parking': has_parking,
                'has_storage': has_storage, 'has_balcony': has_balcony,
                'has_images': has_images,
                'target_day': target_day, 'max_age_hours': max_age_hours,
            }
            # What Divar already filtered is not checked again, and neither is
            # a filter that means nothing for this category (a deposit on a
            # sale). A filter Divar does not have for it stays here — the
            # safety net for what Divar lets through. Not for an explicit
            # list: nothing was searched, so Divar filtered nothing.
            if _plan is not None and not urls:
                for _name in _plan.local_off:
                    if _name in _pre_filters:
                        _pre_filters[_name] = None
            
            # Scrape each property detail
            examined = 0
            # «تکراری»: stored with its number already, so not opened and not
            # written. Not «بروز», which is a stored listing this run opened
            # again and saved over — job 43 counted both as updated_items, and
            # its log, its finish line and its table column each called that
            # one number something different (#32).
            duplicates = 0
            # Numberless listings owed a retry that got their number this time.
            recovered = 0
            # Read once: after a rollback `job` is expired, and reading an
            # expired attribute is a lazy load outside the greenlet.
            _job_uuid = job.job_id
            for i, listing in enumerate(all_listings):
                _counted = False
                try:
                    # Stop as soon as the numeric target is reached
                    # (in whole-day mode max_items is None — no cap).
                    if max_items and job.new_items >= max_items:
                        logger.info(f"Reached target of {max_items} saved listings — stopping")
                        break

                    # Check if job was cancelled — or failed by the queue's
                    # sweep: either way the row says the run is over, and a
                    # run that carries on does work nobody sees, on a number a
                    # «ادامه» of it is about to want.
                    await self.db_session.refresh(job)
                    if job.status in _STOPPED_STATUSES:
                        logger.info(f"Job {job.job_id} is {job.status}, stopping scraping")
                        return job

                    # Counted here rather than from `i`, so that candidates the
                    # run never reached are not reported as candidates it
                    # dropped. The two are different answers to «where did they
                    # go?».
                    examined += 1
                    _counted = True
                    # A numberless listing an earlier run saved (see
                    # _with_phone_retries): no filter of this run applies.
                    _retry = listing['divar_id'] in retry_ids

                    # The last candidate, and the target still unmet: page on
                    # into Divar's search now, so the walk carries on into
                    # what comes next instead of ending «آگهی بیشتری پیدا نشد»
                    # with the rest of the city unread (#30).
                    if max_items and i == len(all_listings) - 1:
                        _left = max_items - int(getattr(job, "new_items", 0) or 0)
                        # The refresh above opened a transaction, and the top-up
                        # pages Divar over HTTP for as long as it needs: closed
                        # first, so Postgres's idle-in-transaction timeout does
                        # not kill the connection under it (see the commit below).
                        await self.db_session.commit()
                        if await self._top_up_pool(all_listings, seen_ids, 2 * _left + 24,
                                                   max_items):
                            # «کل» is this run's own pool (#29), so it grows with
                            # it — or the row reads «82 / 60» and a full bar.
                            self._set_counts(job, total_items=len(all_listings))

                    # Check if already scraped. Not for an explicit list: a
                    # listing named by hand is one somebody wants opened,
                    # whatever the table already holds about it.
                    if not urls and await self.property_exists(listing['divar_id']):
                        # Already stored AND complete: «تکراری». Nothing is
                        # written, so it is not «بروز» — but it was examined,
                        # and «بررسی» says so now rather than at the next
                        # listing the run opens.
                        logger.info(f"Property already exists: {listing['divar_id']}")
                        duplicates += 1
                        self._set_counts(job, scraped_items=examined)
                        await self.db_session.commit()
                        continue
                    
                    # Close the read transaction before the slow part.
                    #
                    # Postgres here runs idle_in_transaction_session_timeout =
                    # 60s. The refresh(job) and property_exists() above open a
                    # transaction, and scrape_property_detail then spends
                    # anywhere from ten seconds to a minute in the browser —
                    # loading the page, revealing a contact, downloading images
                    # — with that transaction sitting idle. Past 60s Postgres
                    # terminates the connection, and the run dies on the next
                    # query with "the underlying connection is closed", which
                    # SQLAlchemy then reports as MissingGreenlet.
                    #
                    # Two runs of 222 candidates died this way at listing 1.
                    # Slower pacing made it certain: the delays went from
                    # 0.35-0.9s to 2-5s with occasional 20s pauses, which is
                    # the right thing for Divar and pushed the idle window past
                    # the timeout.
                    #
                    # idle_session_timeout is 0, so a connection idle OUTSIDE a
                    # transaction is left alone indefinitely. Committing here
                    # costs nothing — there is nothing pending — and it is what
                    # keeps the connection alive across the browser work.
                    await self.db_session.commit()

                    # Scrape detail page
                    detail = await self.scrape_property_detail(
                        listing['url'], target_category=category,
                        source_title=listing.get('title'),
                        wants_contact=None if _retry else lambda pd: self.pre_contact_skip(
                            pd, _listing_type, _pre_filters),
                    )
                    # A cancel that landed while the ad was open — as often as
                    # not while it sat on a code prompt — stops the run here,
                    # before the photos and the save. Only a number already
                    # revealed is kept: that reveal is spent either way.
                    if not (detail and detail.get("phone_number")) and await self._cancelled_now():
                        logger.info(f"Job {self._job_id_str} was stopped during a listing — "
                                    "not finishing it")
                        return job

                    if detail:
                        # Merge with listing data
                        property_data = {**listing, **detail}
                        # The run's city/category name the row — unless the run
                        # is an explicit list, where they are only labels («—»,
                        # «بازاسکرپ») and the page's own breadcrumb is the truth.
                        if CITIES.get(city):
                            property_data['city_name'] = CITIES[city].get('name', city)
                        elif not urls:
                            property_data['city_name'] = city
                        if CATEGORIES.get(category):
                            property_data['category_name'] = CATEGORIES[category].get('name', category)
                        elif not urls:
                            property_data['category_name'] = category
                        listing_type = CATEGORIES.get(category, {}).get('type', 'unknown')
                        # A label like «اسکرپ تکی» is not a category: its «unknown»
                        # must not replace the buy/rent the page's breadcrumb said.
                        if listing_type != 'unknown' or not property_data.get('listing_type'):
                            property_data['listing_type'] = listing_type

                        did = listing['divar_id']

                        _why: Dict[str, str] = {}

                        def _skip(reason: str) -> bool:
                            if _retry:  # noqa: B023 — called in this same iteration
                                # Owed a number by an earlier run, which kept
                                # it under its own filters. A daily run's date
                                # filter would otherwise drop yesterday's
                                # listing every time, before the reveal.
                                logger.info(f"{did}: {reason} — not applied to a phone retry")  # noqa: B023
                                return False
                            logger.info(f"Skipping {did}: {reason}")
                            bucket = reason.split()[0] if reason else "other"
                            skip_tally[bucket] = skip_tally.get(bucket, 0) + 1
                            # Kept for the row written below. _skip is called
                            # from a dozen places and stays synchronous; making
                            # it async to write here would mean an await on
                            # every one of them and a missed await on the first
                            # one anybody adds.
                            _why["bucket"], _why["detail"] = bucket, reason
                            return True

                        skip = False

                        # ── The run's own filters ──────────────────────────────────
                        # Price, price per metre, deposit and rent, area, rooms,
                        # photos, elevator, parking, storage, balcony and the
                        # advertiser type — one function, the same one
                        # pre_contact_skip asked before the reveal, so the two
                        # cannot disagree about an ad. (They did: a deposit of
                        # 0 was dropped there and kept here, and the ad was
                        # saved with no number because the reveal never
                        # happened.) An undetermined advertiser type is a miss,
                        # not a match — letting it through is what put agency ads
                        # in the results of a «شخصی» scrape.
                        _filter_why = self.local_filter_skip(detail, listing_type, _pre_filters)
                        if _filter_why:
                            skip = _skip(_filter_why)

                        # ── Publish-date filters: age, or the exact day ────────────
                        if not skip:
                            why = self._date_skip(detail.get('posted_at'), target_day, max_age_hours)
                            if why:
                                skip = _skip(why)

                        if skip:
                            # Same as the other site: a filtered-out listing is
                            # still a listing we processed.
                            self._set_counts(job, scraped_items=examined)
                            await self.db_session.commit()
                            # …and one somebody may want to look at by hand. A
                            # filter saying no is usually right and occasionally
                            # is the filter being wrong; either way the listing
                            # should still be reachable afterwards.
                            await skipped_listings.record(
                                job.job_id, divar_id=did, url=listing.get('url'),
                                title=listing.get('title'),
                                reason=_why.get("bucket", "other"),
                                detail=_why.get("detail"))
                            # Still ask, because this is a safe point to switch
                            # and a challenge may be pending from the previous
                            # listing. It no longer *advances* anything: a
                            # dropped listing never reached ContactExtractor, so
                            # Divar was never asked for anything on its behalf.
                            # The counter moves where the reveal happens.
                            await self.maybe_rotate_account()
                            self._note_account(job)
                            # Nothing may hold a transaction across a sleep —
                            # see the note at the other delay below.
                            await self.db_session.commit()
                            await self._human_like_delay(stop_on_cancel=True)
                            continue

                        # Download images if enabled — replace the Divar (webp)
                        # URLs with our converted local JPEG URLs so the panel
                        # always serves .jpg
                        if download_images and property_data.get('images'):
                            local_images = await self.download_images(
                                property_data['images'],
                                property_data['divar_id']
                            )
                            if local_images:
                                property_data['images'] = local_images
                                property_data['thumbnail_url'] = local_images[0]
                                property_data['images_downloaded'] = True
                                # Fingerprints of the images we actually kept —
                                # the ones a duplicate check compares.
                                hashes = getattr(self, '_pending_hashes', None)
                                if hashes:
                                    property_data['image_hashes'] = list(hashes)
                                graded = getattr(self, '_pending_quality', None)
                                if graded:
                                    from app.services import image_quality as _iq
                                    property_data['image_quality'] = _iq.summarise(graded)
                        
                        # Who actually posted this, according to the ad.
                        #
                        # Divar's own answer is already in advertiser_type and
                        # is not always right — a listing whose description
                        # ends «املاک هستم» arrived through a «شخصی» filter.
                        # This adds ours beside it rather than over it.
                        from app.services import advertiser_signals
                        advertiser_signals.annotate(property_data)
                        if advertiser_signals.disagrees_with_divar(property_data):
                            logger.info(
                                f"{did}: Divar says personal, the ad says "
                                f"{property_data.get('agency_evidence')!r}")
                            # Kept, labelled — and counted, for the run's report.
                            self._count_agency_posing(property_data.get('agency_evidence'))

                        # Grade the record before storing it. Recorded, never
                        # enforced: we already spent a contact reveal on this
                        # listing, so a flagged row beats a dropped one. The
                        # score is what makes "is the scraper getting the data
                        # right?" a question with an answer.
                        self._grade_property(property_data)

                        # Save to database
                        saved = await self.save_property(property_data)
                        if getattr(self, "_last_save_rolled_back", False):
                            # The save rolled this session back to get a fresh
                            # connection, so `job` is expired and every counter
                            # below would lazy-load on attribute access. Re-read
                            # it once, here, where the failure is contained —
                            # the same guard the failure branch has always had.
                            try:
                                await self.db_session.refresh(job)
                            except Exception as _re:
                                logger.warning(
                                    f"could not re-read the job row after a save "
                                    f"retry: {_re}")
                                try:
                                    await self.db_session.rollback()
                                    await self.db_session.refresh(job)
                                except Exception:
                                    logger.error(
                                        "job row unreadable after a save retry — "
                                        "stopping rather than writing nonsense counters")
                                    raise
                        if saved and self._phone_required and not property_data.get("phone_number"):
                            # Stored, but not a «تازه»: the phone number is the
                            # product, and a listing without one reported as a
                            # win is how a run of 50 new could hold 40 nobody
                            # can call.
                            #
                            # Two very different reasons hide behind that blank,
                            # and calling both «ناموفق» made run 110 — which
                            # got a number from every listing that had one —
                            # report nineteen failures.
                            _ch = property_data.get("contact_channel")
                            # What happens to it next, said as it is (#32). A
                            # search run's numberless listings are owed a retry
                            # by the next run of its city and category
                            # (_with_phone_retries); an explicit list has
                            # neither, so nothing picks it up by itself.
                            _next = (f"اجرای بعدیِ همین کاربر در همین شهر و دسته دوباره "
                                     f"امتحانش می‌کند (تا {skipped_listings.PHONE_ATTEMPTS} بار)"
                                     if not urls else "با «بازاسکرپ» دوباره امتحانش کنید")
                            if _ch == "needs_identity":
                                # Ours, not the poster's, and temporary: the
                                # listing is retried once the account is
                                # verified. Never chat_only, which is forever.
                                job.failed_items += 1
                                fail_tally["نیاز به تأیید هویت"] = fail_tally.get("نیاز به تأیید هویت", 0) + 1
                                await skipped_listings.record(
                                    self._job_id_str, divar_id=did,
                                    url=listing.get("url"), title=property_data.get("title"),
                                    reason="needs_identity",
                                    detail=f"دیوار از این حساب تأیید هویت خواسته — {_next}")
                            elif _ch == "chat_only":
                                # The poster chose Divar chat. There is no
                                # number to get, no run will ever find one, and
                                # property_exists already declines to re-scrape
                                # it. Not a failure, and not retried — so the
                                # detail must not promise a retry.
                                skip_tally["chat_only"] = skip_tally.get("chat_only", 0) + 1
                                await skipped_listings.record(
                                    self._job_id_str, divar_id=did,
                                    url=listing.get("url"), title=property_data.get("title"),
                                    reason="chat_only",
                                    detail="آگهی‌دهنده فقط از راه چت دیوار تماس می‌گیرد — شماره‌ای برای گرفتن نیست")
                                logger.info(f"{did}: contact is chat-only — stored, not a failure")
                            else:
                                job.failed_items += 1
                                fail_tally["بدون شماره"] = fail_tally.get("بدون شماره", 0) + 1
                                await skipped_listings.record(
                                    self._job_id_str, divar_id=did,
                                    url=listing.get("url"), title=property_data.get("title"),
                                    reason="no_phone",
                                    detail=f"ذخیره شد ولی شمارهٔ تماس گرفته نشد — {_next}")
                                logger.warning(f"{did}: saved without a phone number — counted as failed, not new")
                        elif saved and getattr(self, "_last_save_created", True):
                            job.new_items += 1
                        elif saved:
                            # An UPDATE, not an insert: «بروز». save_property
                            # returns a Property either way, so every success
                            # was counted as «جدید» — and the row most often
                            # updated is a stored listing that had no phone
                            # number, which property_exists deliberately lets
                            # through for a second visit. Job 106 reported 32
                            # new against 28 rows actually created; job 102,
                            # 50 against 43.
                            job.updated_items += 1
                            if _retry:
                                recovered += 1
                        else:
                            # save_property rolled back the shared session, which
                            # expires `job`. Refreshing re-reads it so the counter
                            # below does not touch an expired object — but the
                            # refresh is itself a query on a session that just
                            # failed, so it can raise too. Losing the whole run
                            # over a failed bookkeeping read would turn one bad
                            # listing into a dead job.
                            try:
                                await self.db_session.refresh(job)
                            except Exception as refresh_err:
                                logger.warning(
                                    f"could not refresh job after a failed save: {refresh_err}")
                                try:
                                    await self.db_session.rollback()
                                    await self.db_session.refresh(job)
                                except Exception:
                                    logger.error(
                                        "job row unreadable after a failed save — "
                                        "stopping this run rather than writing nonsense counters")
                                    raise
                            job.failed_items += 1
                            _save_why = getattr(self, "_last_save_error", None)
                            _save_why = (f"ذخیره نشد — {_save_why}" if _save_why
                                         else "ذخیره نشد")
                            fail_tally[_save_why] = fail_tally.get(_save_why, 0) + 1
                            await skipped_listings.record(
                                job.job_id, divar_id=listing['divar_id'],
                                url=listing.get('url'), title=listing.get('title'),
                                reason="failed", detail=_save_why)
                    elif detail is None and getattr(self, "_last_detail_error", None) == self.GONE_FROM_DIVAR:
                        # Deleted on Divar. Nothing went wrong here and a retry
                        # will find it just as gone, so it is not «ناموفق»: a
                        # bucket of its own, with Divar's own words beside it.
                        gone += 1
                        await skipped_listings.record(
                            job.job_id, divar_id=listing['divar_id'],
                            url=listing.get('url'), title=listing.get('title'),
                            reason="deleted",
                            detail="دیوار می‌گوید این آگهی حذف شده یا دیگر وجود ندارد")
                    elif detail is None:
                        # None = real scrape error (network failure, parse error, etc.)
                        job.failed_items += 1
                        # …and «۳ ناموفق» with no reason beside it is a number
                        # nobody can act on. A page Divar bounced us off is a
                        # different problem from a page that threw.
                        _reason = getattr(self, "_last_detail_error", None) or "نامعلوم"
                        fail_tally[_reason] = fail_tally.get(_reason, 0) + 1
                        await skipped_listings.record(
                            job.job_id, divar_id=listing['divar_id'],
                            url=listing.get('url'), title=listing.get('title'),
                            reason="failed", detail=_reason)
                    elif detail is False:
                        # Off-category. Not a failure — but not nothing either,
                        # and until now counted nowhere at all. One run put 32
                        # of its 119 candidates through this branch and the
                        # panel could only say 82 had been handled, with no
                        # account of the rest. A silent drop is indistinguishable
                        # from a bug, which is how it was reported.
                        skip_tally["category"] = skip_tally.get("category", 0) + 1
                        _what = getattr(self, "_last_category_drop", None)
                        if _what and len(category_drops) < 6:
                            category_drops.append(_what)
                        await skipped_listings.record(
                            job.job_id, divar_id=listing['divar_id'],
                            url=listing.get('url'),
                            title=listing.get('title') or _what,
                            reason="category", detail=_what)

                    # Progress is listings PROCESSED, not listings newly saved.
                    #
                    # It used to be min(new_items, max_items), so a run over
                    # listings we already had sat at «۰٪ / در حال اجرا» for its
                    # whole length while doing real work on every one of them.
                    # A bar that cannot move is worse than no bar.
                    self._set_counts(job, scraped_items=examined)
                    await self.db_session.commit()

                    # spread the load across saved Divar accounts
                    await self.maybe_rotate_account()
                    self._note_account(job)     # flushed with the next progress write

                    # Nothing may hold a transaction across a sleep.
                    #
                    # maybe_rotate_account queries and can write, and the delay
                    # below is now 2-5s normally, up to four times that on the
                    # long-pause branch, and up to five MINUTES when Divar has
                    # refused us and the backoff is engaged. Postgres closes a
                    # connection idle in a transaction for 60s, so any of those
                    # would end the run.
                    await self.db_session.commit()
                    await self._human_like_delay(stop_on_cancel=True)

                except Exception as e:
                    logger.error(f"Failed to process listing: {e}")
                    if not _counted:
                        # Reached, and failed before it was counted: still one
                        # of the listings this run examined, or «بررسی» and
                        # the failures stop adding up.
                        examined += 1
                    fail_tally[type(e).__name__] = fail_tally.get(type(e).__name__, 0) + 1
                    try:
                        await skipped_listings.record(
                            _job_uuid, divar_id=str(listing.get('divar_id') or ''),
                            url=listing.get('url'), title=listing.get('title'),
                            reason="failed", detail=type(e).__name__)
                    except Exception:
                        pass
                    # Counted after the rollback, not before it. The rollback
                    # expires `job` and discards whatever was not committed —
                    # the failure counted here used to vanish with it, so the
                    # run's «ناموفق» came up short of its own tally.
                    try:
                        await self.db_session.rollback()
                        await self.db_session.refresh(job)
                        self._set_counts(job, failed_items=(job.failed_items or 0) + 1,
                                         scraped_items=examined)
                        await self.db_session.commit()
                    except Exception as _count_err:
                        logger.warning(f"could not count the failed listing: {_count_err}")
            
            # Complete job — or «ناقص»: a collection that stopped short of
            # Divar's list, on a run that did not meet its target on what it
            # had, is not complete, and the row must not say it is (#28).
            # Unless a cancel or the sweep got there first — then that stays.
            _saved = int(getattr(job, "new_items", 0) or 0)
            _cut_reason = self._collection_shortfall(
                category, collected=len(all_listings),
                divar_total=getattr(job, "divar_count", None),
                pool=len(all_listings), saved=_saved, asked=max_items)
            final_status = ("partial" if _cut_reason and not (max_items and _saved >= max_items)
                            else "completed")
            _finished = await self._finish_status(final_status)
            if _finished:
                self._set_counts(job, completed_at=datetime.now())
            # «بررسی» stays what the run examined. It was set to «کل» here, so
            # a run that met its target at 2 of 10 read «10 / 10» (#29); the
            # bar of a finished run is full because it is finished
            # (ScrapingJob.progress), not because the counter was bent.
            self._set_counts(job, scraped_items=examined)
            await self.db_session.commit()
            if not _finished:
                # Stopped from outside during the last listing: a cancel, or
                # the sweep's «failed» with its own «ادامه» sentence. That is
                # the row's last word — no «اسکرپ تمام شد» and no finish reason
                # of this run written over it. The session is still worth keeping.
                await self._persist_active_session()
                return job
            # The FINISH event is recorded further down, AFTER finish_reason has
            # been composed. Written here it always said «تمام شد» with no
            # reason attached, because the reason does not exist yet at this
            # point — which is exactly what made a cut-short run read as a
            # clean one in the log.
            
            # The account that finished the job has the freshest session of all;
            # losing it would make the next job start from a stale snapshot.
            await self._persist_active_session()

            logger.info(f"Scraping job {final_status}. New: {job.new_items}, Updated: {job.updated_items}, "
                        f"Duplicates: {duplicates}, Failed: {job.failed_items}")
            # «تکراری» and «بروز» in one phrase, for the sentences below that
            # explain a short run by what was already held.
            _held = "، ".join(p for p in (f"{duplicates} تکراری" if duplicates else "",
                                          f"{job.updated_items} بروز" if job.updated_items else "") if p)
            # Say so when the feed ran dry before the target was met, rather
            # than completing at «۴۰ / ۲۰۰» with no explanation.
            #
            # This used to skip date_mode entirely, which is the one case that
            # most needs saying: a single day holds however many ads it holds,
            # so a run capped at 126 finishing at 42 is the day being smaller
            # than the cap, not a fault. Unexplained, it reads as a fault.
            #
            # And only when the list really did run dry. «یا دیوار آگهی دیگری
            # ندارد یا فیلترها خیلی تنگ‌اند» on a run whose page 2 Divar refused
            # sent the reader after the filters for a problem that was Divar's.
            if final_status == "partial":
                logger.warning(f"Collection cut short: {_cut_reason}")
                finish_reason = (_cut_reason or "").rstrip(".")  # the tally may follow
            elif max_items and job.new_items < max_items and urls:
                # An explicit list has no feed to run dry.
                finish_reason = (f"{job.new_items} از {max_items} آگهی فهرست تازه ذخیره شد. "
                                 f"{job.updated_items} آگهی از قبل در پایگاه داده بود")
            elif max_items and job.new_items < max_items:
                if date_mode:
                    logger.info(
                        f"Day exhausted: {job.new_items}/{max_items} new for {target_day}. "
                        f"{duplicates} duplicates, {job.updated_items} updated."
                    )
                    if not finish_reason:
                        from app.services.dpa_service import to_jalali
                        finish_reason = (
                            f"آن روز ({to_jalali(target_day)}) بیش از این آگهی نداشت — "
                            f"{job.new_items} از {max_items} درخواستی"
                        )
                else:
                    logger.warning(
                        f"Ran out of candidates: {job.new_items}/{max_items} new from a pool of "
                        f"{len(all_listings)}. {duplicates} duplicates, {job.updated_items} updated. "
                        "Divar has no more matching listings, or the filters are too tight."
                    )
                    finish_reason = (
                        f"آگهی بیشتری پیدا نشد — {job.new_items} از {max_items} درخواستی"
                        + (f" ({_held})" if _held else "")
                        + ". یا دیوار آگهی دیگری ندارد یا فیلترها خیلی تنگ‌اند"
                    )

            # Which filter actually cost the run its listings. Asking for 78 and
            # getting 3 is not mysterious once you know 125 of 200 candidates
            # failed the deposit band alone — but that number only ever existed
            # in the log, so the panel showed a completed job and no reason to
            # doubt the filters. Name the biggest offenders on the job itself.
            if skip_tally:
                top = sorted(skip_tally.items(), key=lambda kv: -kv[1])[:3]
                named = "، ".join(
                    f"{self._FILTER_LABELS_FA.get(k, k)}: {v}" for k, v in top)
                dropped = f"{sum(skip_tally.values())} آگهی با فیلترها حذف شد ({named})"
                finish_reason = f"{finish_reason}؛ {dropped}" if finish_reason else dropped
            if gone:
                _gone = f"{gone} آگهی در دیوار حذف شده بود"
                finish_reason = f"{finish_reason}؛ {_gone}" if finish_reason else _gone

            # A run whose OTP prompts went unanswered finishes fast and looks
            # normal, but half its listings have no phone number. Say so.
            try:
                from app.scraper import otp_store
                if await otp_store.is_cancelled(job.job_id):
                    note = ("کد تأیید دیوار وارد نشد — آگهی‌ها ذخیره شدند "
                            "ولی شمارهٔ تماس بعضی‌شان خالی است")
                    finish_reason = f"{finish_reason}؛ {note}" if finish_reason else note
            except Exception as e:
                logger.warning(f"could not check OTP suppression: {e}")

            # The column holds 300 characters, and a reason assembled from
            # several clauses can run past that — which Postgres refuses at
            # the commit, turning a finished run into a failed one.
            finish_reason = finish_reason[:300] if finish_reason else finish_reason
            job.finish_reason = finish_reason
            await self.db_session.commit()

            from app.services import job_log
            # The number the pacing is tuned against. «۱۴۰ افشا، ۱ چالش» is
            # the goal met; «۳۰ افشا، ۴ چالش» is the setting to raise.
            _rv = getattr(self, "_reveals_this_run", 0)
            _ch = getattr(self, "_challenges_this_run", 0)
            _goal = int(getattr(settings, "challenge_goal_reveals", 100) or 100)
            if _rv:
                _ratio = (f"هر {_rv // _ch} افشا یک چالش" if _ch
                          else "بدون چالش")
                _verdict = ("✓ هدف" if (not _ch or _rv // _ch >= _goal)
                            else f"✗ هدف هر {_goal}")
                await job_log.record(
                    job.job_id, job_log.CHALLENGE,
                    f"{_rv} افشا، {_ch} چالش کد — {_ratio} ({_verdict})",
                    level="info" if (not _ch or _rv // _ch >= _goal) else "warning",
                    reveals=_rv, challenges=_ch,
                    reveals_per_challenge=(_rv // _ch if _ch else None), goal=_goal)
            # The same three words, for the same three numbers, as the tally
            # below and the table: تازه (created), بروز (stored, opened again
            # and saved over), تکراری (stored complete, not opened).
            _summary = (("اسکرپ ناقص تمام شد" if final_status == "partial" else "اسکرپ تمام شد")
                        + f" — {job.new_items} تازه، "
                        f"{job.updated_items} بروز، {duplicates} تکراری، "
                        f"{job.failed_items} ناموفق")
            if finish_reason:
                _summary += f"\n{finish_reason}"
            # A run that asked for N and saved fewer is worth flagging even when
            # the reason is benign, so it does not read as an unqualified success
            # — and so is a partial one, which may have no N at all (a day).
            _short_of_target = (final_status == "partial"
                                or bool(max_items and job.new_items < max_items))
            await job_log.record(
                job.job_id, job_log.FINISH, _summary,
                level="warning" if _short_of_target else "info",
                new=job.new_items, updated=job.updated_items, duplicates=duplicates,
                failed=job.failed_items, pages=job.scraped_pages,
                requested=max_items, candidates=len(all_listings),
                skipped=(sum(skip_tally.values()) or None), status=final_status)
            # Where the candidates went, per reason. This tally has always been
            # computed and only ever written to a log file nobody reads per-job,
            # so «۴۲ نامزد، ۳ ذخیره» looked like a fault when it was usually the
            # filters doing exactly what they were told.
            if duplicates:
                # Only the duplicates: a «بروز» listing WAS saved again, and
                # this sentence used to count those too.
                await job_log.record(
                    job.job_id, job_log.PAGE,
                    f"{duplicates} آگهی تکراری بود — از قبل با شماره در پایگاه داده بود "
                    "و دوباره باز نشد",
                    duplicates=duplicates)
            if retry_ids:
                await job_log.record(
                    job.job_id, job_log.PAGE,
                    f"از {len(retry_ids)} آگهیِ بدون شمارهٔ اجراهای قبل، {recovered} شماره گرفت",
                    retries=len(retry_ids), recovered=recovered)

            # Every candidate, accounted for.
            #
            # Asked «۱۱۳ آگهی هست ولی ۸۲ تا اسکرپ شد — کدام غلط است؟», neither
            # number was wrong: 119 candidates became 71 saved + 11 already
            # held + 5 filtered + 32 off-category, and only the first three of
            # those had ever been written down. A total that does not add up
            # reads as a fault whether or not there is one, so make it add up —
            # and when it still does not, say that too rather than let the
            # difference pass unremarked.
            _dropped = sum(skip_tally.values())
            _accounted = (job.new_items + job.updated_items + duplicates
                          + job.failed_items + _dropped + gone)
            _parts = [f"{job.new_items} تازه", f"{job.updated_items} بروز",
                      f"{duplicates} تکراری"]
            if job.failed_items:
                _named = "، ".join(f"{k}: {v}" for k, v in
                                   sorted(fail_tally.items(), key=lambda kv: -kv[1]))
                _parts.append(f"{job.failed_items} ناموفق"
                              + (f" ({_named})" if _named else ""))
            _parts += [f"{v} {self._FILTER_LABELS_FA.get(k, k)}"
                       for k, v in sorted(skip_tally.items(), key=lambda kv: -kv[1])]
            if gone:
                _parts.append(f"{gone} {self.GONE_FROM_DIVAR}")
            _unreached = len(all_listings) - examined
            if _unreached > 0:
                _parts.append(f"{_unreached} بررسی‌نشده")
            _unaccounted = examined - _accounted
            if _unaccounted > 0:
                _parts.append(f"{_unaccounted} بی‌حساب")
            # The same account, kept on the row: the table's «تازه» cell says
            # where the rest went from this, and there is no column for
            # duplicates (JSON on the run's config, so no migration). A job
            # without a config is one nothing can resume, and stays without.
            if isinstance(job.config, dict):
                job.config = {**job.config, "outcome": {
                    "pool": len(all_listings), "examined": examined,
                    "duplicate": duplicates, "failed": dict(fail_tally),
                    "skipped": dict(skip_tally), "gone": gone,
                    "unreached": max(_unreached, 0),
                    "retried": len(retry_ids), "recovered": recovered,
                }}
                await self.db_session.commit()
            await job_log.record(
                job.job_id, job_log.PAGE,
                f"{len(all_listings)} نامزد — " + "، ".join(_parts),
                level="warning" if _unaccounted > 0 else "info",
                candidates=len(all_listings), examined=examined,
                unreached=(_unreached or None),
                unaccounted=(_unaccounted if _unaccounted > 0 else None))
            if category_drops:
                await job_log.record(
                    job.job_id, job_log.PAGE,
                    "نمونه‌ای از آگهی‌هایی که خارج از دسته‌بندی شمرده شدند: "
                    + "؛ ".join(category_drops),
                    samples=len(category_drops))
            await self._report_agency_posing(job)

            if skip_tally:
                breakdown = ", ".join(f"{k}={v}" for k, v in
                                      sorted(skip_tally.items(), key=lambda kv: -kv[1]))
                logger.info(f"Filters dropped {sum(skip_tally.values())} listings — {breakdown}")
                await job_log.record(
                    job.job_id, job_log.PAGE,
                    f"{sum(skip_tally.values())} آگهی با فیلترها حذف شد — {breakdown}",
                    level="warning" if not job.new_items else "info",
                    **{f"skip_{k}": v for k, v in skip_tally.items()})
                if not job.new_items:
                    logger.warning(
                        "Job saved nothing: every candidate was dropped by a filter. "
                        f"Loosen whichever of these is doing it — {breakdown}")
                    await job_log.record(
                        job.job_id, job_log.FINISH,
                        "هیچ آگهی ذخیره نشد — همهٔ نامزدها با فیلترها حذف شدند. "
                        f"فیلتری که بیشترین حذف را کرده: {breakdown}",
                        level="warning")
            
        except Exception as e:
            # Only a run still live becomes «ناموفق»: a cancel, the sweep's
            # «failed», or a finish already written (a later step raised)
            # stays what it is.
            try:
                moved = await self._move_status("failed")
            except Exception:
                moved = True          # the session cannot say: the plain write, as before
                self._set_counts(job, status="failed")
            if moved:
                self._set_counts(job, error_message=str(e), completed_at=datetime.now())
            await self.db_session.commit()
            logger.error(f"Scraping job failed: {e}")
            from app.services import job_log
            await job_log.record(
                job.job_id, job_log.ERROR,
                f"اسکرپ با خطا متوقف شد: {type(e).__name__}: {e}",
                level="error", error_type=type(e).__name__,
                new=job.new_items, updated=job.updated_items)
        
        return job
    