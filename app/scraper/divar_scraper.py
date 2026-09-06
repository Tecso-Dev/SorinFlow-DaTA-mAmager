"""
SorinFlow Divar Scraper - Main Scraper Module
Handles scraping property listings from Divar.ir
"""
import asyncio
import random
import re
import time
import uuid
from datetime import datetime, timedelta, date
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
from app.scraper.stealth import StealthConfig, STEALTH_JS, get_browser_args, get_context_options
from app.scraper.auth import DivarAuth
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


class DivarScraper:
    """Main scraper class for Divar.ir real estate listings"""

    BASE_URL = "https://divar.ir"
    # How many listings the browser-scroll phase may gather before the
    # cheaper API pagination takes over.
    DOM_COLLECT_CAP = 200

    # Maps our category slug → substrings expected in the Divar detail-page URL.
    # Divar builds URLs from the listing *title*, not the category name, so we
    # use property-type nouns (آپارتمان، خانه …) rather than action-prefix combos
    # (خرید-خانه) which almost never appear in real listing URLs.
    CATEGORY_URL_PATTERNS: Dict[str, List[str]] = {
        # Apartment: title may use آپارتمان, واحد (unit), or مسکن (housing)
        # e.g. اجاره-واحد-۱۲۵-متر / واحد-۱۱۰-متری / اجاره-مسکن / اجاره-تک-واحدی
        #
        # …or none of those. «۸۵ متری، ۲ خوابه، طبقه سوم» is an ordinary way to
        # title an apartment and names no property type at all, so the list
        # above rejected it. These candidates arrive from a search Divar itself
        # filtered by category, so the check here is only a guard against the
        # promoted and related ads Divar injects into a result page — and a job
        # ad or a plot of land does not advertise «۲ خوابه». The residential
        # lists below have trusted exactly these signals for the same reason;
        # the apartment lists were simply never given them.
        'rent-apartment': ['اجاره-آپارتمان', 'اجاره-اپارتمان', 'کرایه-آپارتمان',
                           'آپارتمان', 'اپارتمان', 'واحد', 'اجاره-مسکن',
                           'سرویس', 'سویس', 'خوابه', 'طبقه', 'نوساز'],
        'buy-apartment':  ['آپارتمان', 'اپارتمان', 'واحد',
                           'سرویس', 'سویس', 'خوابه', 'طبقه', 'نوساز'],

        # Residential (broad): title is the property type alone — no buy/rent prefix
        # Strong residential signals (سرویس/سویس/خوابه/طبقه/نوساز) accept units
        # whose title omits the property type, while land/گاردن listings — which
        # never carry these — still fall through and get dropped.
        'rent-residential': ['آپارتمان', 'اپارتمان', 'خانه', 'ویلا', 'مسکونی', 'واحد', 'سوئیت', 'اجاره-مسکن', 'ساختمان', 'دوبلکس', 'منزل', 'سرویس', 'سویس', 'خوابه', 'طبقه', 'نوساز'],
        'buy-residential':  ['آپارتمان', 'اپارتمان', 'خانه', 'ویلا', 'مسکونی', 'واحد', 'سوئیت', 'کلنگی', 'ساختمان', 'دوبلکس', 'منزل', 'سرویس', 'سویس', 'خوابه', 'طبقه', 'نوساز'],

        # Villa
        'rent-villa': ['ویلا', 'باغ-ویلا'],
        'buy-villa':  ['ویلا', 'باغ-ویلا'],

        # Old house
        'buy-old-house': ['کلنگی', 'خانه-کلنگی'],

        # Commercial
        'rent-commercial-property': ['اجاره-اداری', 'اجاره-تجاری', 'مغازه', 'اداری', 'تجاری'],
        'rent-office':  ['دفتر', 'اداری'],
        'rent-store':   ['مغازه', 'فروشگاه'],
        'buy-commercial-property':  ['مغازه', 'اداری', 'تجاری'],
        'buy-office':   ['دفتر', 'اداری'],
        'buy-store':    ['مغازه', 'فروشگاه'],

        # Industrial / Agricultural
        'buy-industrial-agricultural-property':  ['صنعتی', 'کشاورزی', 'کارخانه', 'کارگاه', 'زمین'],
        'rent-industrial-agricultural-property': ['صنعتی', 'کشاورزی', 'کارخانه', 'کارگاه', 'زمین'],

        # Temporary rental
        'rent-temporary': ['اجاره-کوتاه', 'اجاره-روزانه', 'اجاره-موقت', 'روزانه', 'کوتاه-مدت'],
    }
    
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
    
    async def initialize(self, restore_session: bool = True, phone_number: str = None) -> bool:
        """Initialize scraper with browser and optional session restoration"""
        try:
            self.playwright = await async_playwright().start()

            # Get proxy if enabled
            proxy = None
            if self.proxy_enabled:
                proxy = await self._get_working_proxy()

            self.browser = await self.playwright.chromium.launch(
                headless=self.headless,
                args=get_browser_args()
            )

            context_options = get_context_options(self.stealth_config, proxy)
            self.context = await self.browser.new_context(**context_options)

            # Add stealth script
            await self.context.add_init_script(STEALTH_JS)

            self.page = await self.context.new_page()

            # Restore authentication session
            if restore_session:
                # Explicit phone takes priority, then env var, then auto-select from DB
                phone_number = phone_number or settings.divar_phone_number
                # If DIVAR_PHONE_NUMBER not configured, find any valid cookie in DB
                if not phone_number and self.db_session:
                    try:
                        from app.models.cookie import Cookie as CookieModel
                        from sqlalchemy import select as _select
                        # Least-spent first, oldest-used to break the tie.
                        #
                        # This used to take the most recently *updated* row, and
                        # saving a session on rotation bumps updated_at — so the
                        # account that had just been used was always the one
                        # picked next, and with up to three jobs at once they
                        # all landed on the same number. One account absorbed
                        # every reveal while the others sat idle, which is what
                        # the constant SMS was.
                        _res = await self.db_session.execute(
                            _select(CookieModel)
                            .where(CookieModel.is_valid == True)
                            .order_by(CookieModel.reveals.asc(),
                                      CookieModel.last_used_at.asc().nullsfirst())
                            .limit(1)
                        )
                        _rec = _res.scalar_one_or_none()
                        if _rec:
                            phone_number = _rec.phone_number
                            logger.info(f"Auto-selected Divar session for {phone_number}")
                    except Exception as _e:
                        logger.warning(f"Could not auto-select session: {_e}")

                if phone_number:
                    self.auth.context = self.context
                    self.auth.page = self.page
                    self.auth.browser = self.browser

                    restored = await self.auth.restore_session(phone_number)
                    if not restored:
                        logger.warning(f"Session not restored for {phone_number}. Trying other saved sessions...")
                        # Fall back to any other valid session in DB
                        phone_number = None
                        if self.db_session:
                            try:
                                from app.models.cookie import Cookie as CookieModel
                                from sqlalchemy import select as _select
                                _res = await self.db_session.execute(
                                    _select(CookieModel)
                                    .where(CookieModel.is_valid == True)
                                    .order_by(CookieModel.updated_at.desc())
                                    .limit(1)
                                )
                                _rec = _res.scalar_one_or_none()
                                if _rec:
                                    phone_number = _rec.phone_number
                                    logger.info(f"Falling back to session for {phone_number}")
                                    from app.services import job_log
                                    await job_log.record(
                                        self.current_job.job_id if self.current_job else None,
                                        job_log.SESSION,
                                        f"نشست اصلی کار نکرد — با شمارهٔ {phone_number} ادامه می‌دهیم",
                                        level="warning", phone=phone_number)
                            except Exception as _e:
                                logger.warning(f"Could not find fallback session: {_e}")

                        if phone_number:
                            restored = await self.auth.restore_session(phone_number)
                            if not restored:
                                logger.warning("Fallback session also failed. Phone numbers will not be extracted.")
                                return False
                            self.active_phone = phone_number
                            logger.info(f"Session restored successfully using fallback: {phone_number}")
                        else:
                            logger.warning("No valid session found. Phone numbers will not be extracted.")
                            from app.services import job_log
                            await job_log.record(
                                self.current_job.job_id if self.current_job else None,
                                job_log.SESSION,
                                "هیچ نشست معتبر دیواری پیدا نشد — شمارهٔ تماس آگهی‌ها استخراج نمی‌شود",
                                level="warning")
                            return False
                    else:
                        self.active_phone = phone_number
                        logger.info("Session restored successfully")
                else:
                    logger.warning("No Divar session configured — phone numbers will not be extracted.")

            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize scraper: {e}")
            return False
    
    def _client(self) -> httpx.AsyncClient:
        """The shared HTTP client, created on first use and closed in close()."""
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=30.0, follow_redirects=True)
        return self._http

    async def close(self):
        """Close browser and cleanup resources"""
        try:
            if self._http is not None and not self._http.is_closed:
                await self._http.aclose()
            if self.page:
                await self.page.close()
            if self.context:
                await self.context.close()
            if self.browser:
                await self.browser.close()
            if self.playwright:
                await self.playwright.stop()
            logger.info("Scraper closed successfully")
        except Exception as e:
            logger.error(f"Error closing scraper: {e}")
    
    async def _get_working_proxy(self) -> Optional[str]:
        """Get a working proxy from the database"""
        try:
            result = await self.db_session.execute(
                select(Proxy).where(
                    and_(Proxy.is_active == True, Proxy.is_working == True)
                ).order_by(Proxy.success_count.desc()).limit(1)
            )
            proxy = result.scalar_one_or_none()
            if proxy:
                return proxy.url
            return None
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

    async def _human_like_delay(self, min_delay: float = None, max_delay: float = None):
        """Wait between actions, and wait out any backoff we owe Divar.

        Two changes from a flat random.uniform(0.35, 0.9):

        * The cooldown is honoured first. Without it a 429 changed nothing and
          the scraper kept knocking at the rate that had just been refused.
        * The distribution is heavy-tailed. Real browsing is bursty: mostly
          quick, occasionally a long pause while somebody reads something. A
          tight uniform window is a signature in itself, and it is also simply
          harder on the server than the same work spread out.
        """
        now = time.monotonic()
        if now < self._cooldown_until:
            owed = self._cooldown_until - now
            logger.info(f"[pace] cooling down for {owed:.0f}s before the next request")
            await asyncio.sleep(owed)

        min_d = min_delay or self.stealth_config.min_delay
        max_d = max_delay or self.stealth_config.max_delay
        if random.random() < 0.12:
            # the pause where a person actually reads the ad
            delay = random.uniform(max_d, max_d * 4)
        else:
            delay = random.uniform(min_d, max_d)
        await asyncio.sleep(delay)
    
    async def _simulate_scroll(self):
        """Simulate human-like scrolling"""
        try:
            for _ in range(self.stealth_config.scroll_steps):
                scroll_distance = self.stealth_config.get_random_scroll_distance()
                await self.page.evaluate(f"window.scrollBy(0, {scroll_distance})")
                await asyncio.sleep(self.stealth_config.scroll_delay)
        except Exception as e:
            logger.warning(f"Scroll simulation failed: {e}")
    
    async def _mouse_movement(self):
        """Simulate random mouse movements"""
        try:
            viewport = self.stealth_config.get_viewport()
            for _ in range(random.randint(2, 5)):
                x = random.randint(100, viewport["width"] - 100)
                y = random.randint(100, viewport["height"] - 100)
                await self.page.mouse.move(x, y)
                await asyncio.sleep(random.uniform(0.1, 0.3))
        except Exception as e:
            logger.warning(f"Mouse movement simulation failed: {e}")
    
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
        for closer, what in ((getattr(self, "page", None), "page"),
                             (getattr(self, "context", None), "context"),
                             (getattr(self, "browser", None), "browser")):
            if closer is None:
                continue
            try:
                await closer.close()
            except Exception as e:
                logger.warning(f"[memory] closing the {what} failed: {e}")
        self.page = self.context = self.browser = None

        await asyncio.sleep(1)

        try:
            if self.playwright is None:
                self.playwright = await async_playwright().start()
            proxy = await self._get_working_proxy() if self.proxy_enabled else None
            self.browser = await self.playwright.chromium.launch(
                headless=self.headless, args=get_browser_args())
            self.context = await self.browser.new_context(
                **get_context_options(self.stealth_config, proxy))
            await self.context.add_init_script(STEALTH_JS)
            self.page = await self.context.new_page()
        except Exception as e:
            logger.error(f"[memory] could not start a fresh browser: {e}")
            raise

        self.request_count = 0
        self.session_start = datetime.now()
        if phone:
            try:
                await self.auth.restore_session(phone)
                self.active_phone = phone
                logger.info(f"[memory] session for {phone} restored after recycle")
            except Exception as e:
                logger.error(f"[memory] could not restore {phone} after recycle: {e}")

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
        
        # Check requests per minute
        elapsed = (datetime.now() - self.session_start).total_seconds()
        if elapsed > 0:
            rpm = (self.request_count / elapsed) * 60
            if rpm > self.stealth_config.max_requests_per_minute:
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
        """Fetch listings by calling Divar's internal JSON API directly via httpx.

        Returns (listings, last_post_date).
        """
        listings: List[Dict[str, Any]] = []
        next_last_post_date: Optional[int] = None

        # Pass the browser's session cookies so the API returns real listings
        cookie_header = ""
        try:
            if self.context:
                browser_cookies = await self.context.cookies()
                cookie_header = "; ".join(
                    f"{c['name']}={c['value']}" for c in browser_cookies
                    if 'divar.ir' in c.get('domain', '')
                )
        except Exception:
            pass

        headers = {
            "User-Agent": self.stealth_config.get_random_user_agent(),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "fa-IR,fa;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": f"https://divar.ir/s/{city}/{category}",
            "Origin": "https://divar.ir",
            "x-render-type": "CSR",
            "x-standard-divar-error": "true",
        }
        if cookie_header:
            headers["Cookie"] = cookie_header

        # ── Preferred: replay the real /postlist/w/search POST the browser made.
        #    Reuses Divar's own request body (correct city_id + category enum),
        #    only advancing the pagination cursor. Far more reliable than guessing
        #    the legacy GET endpoint, which now returns 0 results.
        template = self._search_req_template
        # The cursor is no longer part of the gate.
        #
        # It used to be: `and last_post_date`. But last_post_date starts as None
        # and was only ever set from a SUCCESSFUL call to this function, so page
        # one could never take this branch, always fell through to the legacy
        # GET below — which returns a BLOCKING_VIEW «نیاز به بروزرسانی» and zero
        # listings — and therefore never produced the cursor that would have let
        # the branch run. The API phase has contributed nothing to any run since
        # it was written, which is why depth was capped at whatever the DOM
        # scroll managed.
        if template and template.get('post_data'):
            try:
                import json as _json
                body = _json.loads(template['post_data'])
                pd = body.get('pagination_data')
                if not isinstance(pd, dict):
                    pd = {"@type": "type.googleapis.com/post_list.PaginationData"}
                # Only send a cursor we actually have. Writing None here would
                # post `"last_post_date": null`; the dead endpoint's -1 is
                # filtered at the source.
                if isinstance(last_post_date, int) and last_post_date > 0:
                    pd['last_post_date'] = last_post_date
                pd['page'] = page_num
                if 'layer_page' in pd:
                    pd['layer_page'] = page_num
                body['pagination_data'] = pd
                async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
                    resp = await client.post(
                        template['url'],
                        headers={**headers, "Content-Type": "application/json"},
                        json=body,
                    )
                    logger.info(f"Direct API replay POST {template['url']} → {resp.status_code}")
                    if resp.status_code == 200:
                        parsed, lpd = self._parse_api_response(resp.json())
                        if parsed:
                            logger.info(f"Got {len(parsed)} listings via replayed postlist/w/search")
                            return parsed, lpd
            except Exception as e:
                logger.debug(f"Direct API replay failed: {e}")

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
        # Everything real now comes from replaying the browser's own
        # /postlist/w/search POST above. Without a captured template there is
        # nothing useful to try, and saying so beats three requests that cannot
        # work.
        if not template:
            logger.info("[api] no captured search request to replay — "
                        "listing collection is DOM-only for this run")

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
                await asyncio.sleep(1.2)

                clicked_more = await self._click_load_more()
                if clicked_more:
                    no_button_streak = 0
                    await asyncio.sleep(3.5)  # wait for the next batch to render
                else:
                    no_button_streak += 1
                    # No button found — fall back to wheel events (infinite-scroll variant)
                    for _ in range(12):
                        await self.page.mouse.wheel(0, 700)
                        await asyncio.sleep(0.1)
                    await asyncio.sleep(2.5)

                if gained == 0:
                    no_new_streak += 1
                    # Stop only when no new items AND no load-more button for a while
                    if no_new_streak >= 6 and no_button_streak >= 3:
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
        """Convert the API's last_post_date cursor (epoch in s/ms/µs/ns) to a datetime."""
        if not lpd:
            return None
        v = float(lpd)
        for div in (1, 1e3, 1e6, 1e9):
            ts = v / div
            if 1e9 <= ts < 4e9:  # plausible epoch-seconds range (2001..2096)
                try:
                    return datetime.fromtimestamp(ts)
                except (OverflowError, OSError, ValueError):
                    return None
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
        """
        all_listings: List[Dict[str, Any]] = []
        seen_ids: set = set()

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

        if until_day is None and len(all_listings) >= target_count:
            return all_listings[:target_count]

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
                        break
                    cursor_dt = self._cursor_to_datetime(last_post_date)
                    if cursor_dt and cursor_dt.date() < until_day:
                        logger.info(
                            f"[robust] feed cursor {cursor_dt} moved past "
                            f"{until_day} — day fully covered"
                        )
                        break
                elif len(all_listings) >= target_count:
                    break
                # Count pages that added NOTHING NEW, not pages that came back
                # empty. While the replay was dead every batch was empty and
                # this worked by accident; with it alive, a stuck cursor returns
                # the same non-empty page forever, new_count stays 0, and the
                # loop would burn all 75 pages re-fetching one page of results.
                if new_count == 0:
                    consecutive_empty += 1
                    if consecutive_empty >= 2:
                        logger.info(
                            f"[robust] two pages with nothing new (cursor "
                            f"{last_post_date}) — stopping")
                        break
                else:
                    consecutive_empty = 0
                await asyncio.sleep(random.uniform(0.8, 1.5))
            except Exception as e:
                logger.error(f"[robust] API page={page_num} failed: {e}")
                break

        return all_listings if until_day else all_listings[:target_count]

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
    
    def pre_contact_skip(self, detail: Dict[str, Any], listing_type: str,
                         f: Dict[str, Any]) -> Optional[str]:
        """Why this ad would be dropped, judged from the page alone.

        Only filters that need nothing beyond what the ad page already gave us.
        The phone is deliberately not among them: deciding this *before* asking
        for it is the whole point. The full filter set still runs afterwards in
        the scrape loop and remains the authority — this only avoids paying for
        an answer we are going to discard.
        """
        adv = f.get("advertiser_type")
        if adv:
            actual = detail.get("advertiser_type")
            if not actual:
                return f"advertiser_type unknown; {adv} filter active"
            if actual != adv:
                return f"advertiser_type {actual} != {adv}"

        if listing_type == "rent":
            bands = (("deposit", f.get("min_deposit"), f.get("max_deposit")),
                     ("rent_price", f.get("min_rent"), f.get("max_rent")))
        else:
            bands = (("__price__", f.get("min_price"), f.get("max_price")),
                     ("price_per_meter", f.get("min_price_per_meter"),
                      f.get("max_price_per_meter")))
        for field, lo, hi in bands:
            value = (detail.get("total_price") or detail.get("price")
                     if field == "__price__" else detail.get(field))
            if value is None:
                continue
            if lo and value < lo:
                return f"{field} {value} < min {lo}"
            if hi and value > hi:
                return f"{field} {value} > max {hi}"

        for field, lo, hi in (("area", f.get("min_area"), f.get("max_area")),
                              ("rooms", f.get("min_rooms"), f.get("max_rooms"))):
            value = detail.get(field)
            if value is None:
                continue
            if lo is not None and value < lo:
                return f"{field} {value} < min {lo}"
            if hi is not None and value > hi:
                return f"{field} {value} > max {hi}"

        for key, wanted in (("has_elevator", f.get("has_elevator")),
                            ("has_parking", f.get("has_parking")),
                            ("has_storage", f.get("has_storage")),
                            ("has_balcony", f.get("has_balcony")),
                            ("has_images", f.get("has_images"))):
            if wanted is None:
                continue
            actual = bool(detail.get(key))
            if wanted and not actual:
                return f"{key} required but not present"
            if not wanted and actual:
                return f"{key} must be absent"
        return None

    @staticmethod
    def _category_matches(text: str, patterns) -> bool:
        """Does this text carry one of the category's words?

        Divar writes the same phrase two ways — «اجاره-آپارتمان» in a URL slug
        and «اجاره آپارتمان» in a title — and the pattern lists were written
        for slugs. So «اجاره-مسکن» could never match a real ad titled «اجاره
        مسکن مهر کوثر»: the hyphen was doing the rejecting, not the words.
        Both sides collapse to single spaces before comparing.
        """
        if not text:
            return False
        flat = " ".join(text.replace("-", " ").replace("_", " ").split())
        return any(" ".join(p.replace("-", " ").split()) in flat for p in patterns)

    async def scrape_property_detail(
        self, url: str, target_category: Optional[str] = None,
        source_title: Optional[str] = None,
        wants_contact=None,
    ) -> Optional[Dict[str, Any]]:
        """Scrape detailed information from a property page.

        target_category: if provided, the final URL (or the search-result title)
        must match the expected patterns for that category (prevents off-category
        listings from being saved).
        source_title: the listing title captured from the category-filtered
        search results, used as a fallback category signal when Divar serves a
        bare /v/<token> URL with no descriptive slug.
        """
        self._last_detail_error = None
        try:
            logger.info(f"Scraping property detail: {url}")

            await self._check_rate_limit()
            await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)

            # If Divar redirected to a CAPTCHA or home page, skip this property
            actual_url = self.page.url
            if '/v/' not in actual_url:
                logger.warning(f"Detail page redirected away from property: {url} → {actual_url}, skipping")
                self._last_detail_error = "صفحه باز نشد"
                return None

            from urllib.parse import unquote
            decoded_url = unquote(actual_url)

            # ── Category-specific URL check (tight) ──────────────────────────
            category_unconfirmed = False
            patterns = self.CATEGORY_URL_PATTERNS.get(target_category or "", ())
            # When a target category is known, we require the redirected URL to
            # contain at least one of the expected substrings for that category.
            # This blocks job ads, factory listings, etc. that share keywords
            # with real-estate (e.g. "دفتری" matching "دفتر").
            if patterns:
                # Listing URLs are built as bare /v/<token>; Divar only adds a
                # descriptive slug for some of them on redirect, so the URL alone
                # carries no category signal for the rest. Fall back to the title
                # from the (already category-filtered) search result so bare-token
                # listings aren't all dropped — reject only when NEITHER matches.
                haystack = f"{decoded_url} {source_title or ''}"
                page_title = ""
                if not self._category_matches(haystack, patterns):
                    # Neither the URL nor the search-result title says what this
                    # is — which is not the same as saying it is the wrong
                    # thing. Divar's own page title does say («اجاره آپارتمان ۸۵
                    # متری در …»), and we are already standing on the page, so
                    # ask it before throwing the listing away. Only asked when
                    # the cheap signals came up empty, so the common case pays
                    # nothing for it.
                    try:
                        page_title = (await self.page.title()) or ""
                    except Exception as e:
                        logger.debug(f"could not read the page title: {e}")
                    haystack = f"{haystack} {page_title}"
                if not self._category_matches(haystack, patterns):
                    # Nothing here says what this is — and that is not the same
                    # as saying it is the wrong thing.
                    #
                    # Dropping on it cost one run seventeen listings, and the
                    # panel's own list of them showed all seventeen were real
                    # Urmia apartment rentals: «گلشهر ۲ تمام رهن», «اجاره رهن
                    # ۱۴۵متر», «۲۰۰ متر بر دانشکده». None names a property type
                    # because ads written by people often do not, and the page
                    # title was «سایت دیوار» because React had not replaced it
                    # yet at domcontentloaded.
                    #
                    # Divar's own breadcrumb does say, authoritatively, and the
                    # parse below already reads it. So do not decide here on an
                    # absence — carry the doubt to where the answer is.
                    logger.info(
                        f"Category unconfirmed for '{target_category}' "
                        f"(URL: {decoded_url}, title: {source_title!r}, "
                        f"page title: {page_title!r}) — deferring to the breadcrumb"
                    )
                    category_unconfirmed = True
            else:
                # Fallback broad check when no category is known
                REAL_ESTATE_URL_KEYWORDS = [
                    'خرید', 'اجاره', 'رهن', 'فروش', 'مسکن', 'ملک',
                    'آپارتمان', 'اپارتمان', 'خانه', 'ساختمان',
                    'ویلا', 'سوئیت', 'واحد', 'مغازه',
                    'buy', 'rent', 'residential', 'apartment', 'villa',
                ]
                if not any(kw in decoded_url for kw in REAL_ESTATE_URL_KEYWORDS):
                    logger.info(f"Skipping non-real-estate listing (URL: {decoded_url})")
                    self._last_detail_error = "ملک نبود"
                    return None

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
            try:
                import re as _re
                crumbs = [a.get_text(strip=True) for a in soup.select('a.kt-breadcrumbs__action')]
                crumbs = [c for c in crumbs if c and c != 'املاک']
                if crumbs:
                    leaf = crumbs[-1]
                    property_data.setdefault('category_name', leaf)
                    ptype = _re.sub(r'^(پیش[‌ ]?فروش|فروش|اجارهٔ|اجاره|رهن|خرید)\s+', '', leaf).strip()
                    if ptype and ptype != leaf:
                        property_data.setdefault('property_type', ptype)
                    joined = ' '.join(crumbs)
                    if 'اجاره' in joined or 'رهن' in joined:
                        property_data['listing_type'] = 'rent'
                    elif 'فروش' in joined or 'خرید' in joined:
                        property_data['listing_type'] = 'buy'
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
            if category_unconfirmed:
                leaf = property_data.get("category_name") or ""
                if leaf and not self._category_matches(leaf, patterns):
                    logger.info(
                        f"Skipping off-category listing for '{target_category}' "
                        f"— Divar's breadcrumb says {leaf!r}")
                    self._last_category_drop = f"{leaf} — {source_title or decoded_url}"[:80]
                    return False  # sentinel: category skip — not a scrape error
                # No breadcrumb either. Keep it: this listing came out of a
                # search Divar itself filtered by category, and that is better
                # evidence than a word we could not find.
                if not leaf:
                    logger.info(
                        f"No breadcrumb for {property_data.get('divar_id')} — keeping it; "
                        f"Divar's own category filter is the better evidence")

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
            # so the dashboard clearly shows it as paused → running.
            async def _pause_job():
                if self.current_job:
                    self.current_job.status = "paused"
                    await self.db_session.commit()
                    logger.info(f"Job {self.current_job.job_id} PAUSED — awaiting OTP code")
                    from app.services import job_log
                    await job_log.record(
                        self.current_job.job_id, job_log.PAUSE,
                        "دیوار کد تأیید خواست — اسکرپ متوقف شد تا کد وارد شود",
                        level="warning")

            async def _resume_job():
                if self.current_job:
                    # don't override a cancellation that happened meanwhile
                    await self.db_session.refresh(self.current_job)
                    if self.current_job.status == "paused":
                        self.current_job.status = "running"
                        await self.db_session.commit()
                        logger.info(f"Job {self.current_job.job_id} RESUMED")
                        from app.services import job_log
                        await job_log.record(self.current_job.job_id, job_log.RESUME,
                                             "کد وارد شد — اسکرپ ادامه پیدا کرد")

            async def _job_cancelled():
                if not self.current_job:
                    return False
                await self.db_session.refresh(self.current_job)
                return self.current_job.status == "cancelled"

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

            # This is the moment Divar counts, so it is the moment we count —
            # against the account, which is what Divar is counting against.
            self._reveals_since_rotation += 1
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
            )
            # How many sessions rotation can still reach. An unanswered code
            # prompt suppresses phone numbers for the whole job only once every
            # account has been tried — with one account that is the old
            # behaviour, and with five it is four more chances.
            contact_extractor.account_count = await self._usable_account_count()
            phone_number = await contact_extractor.get_phone_number()
            if phone_number:
                property_data["phone_number"] = phone_number
                # A reveal worked, so the pool is not exhausted after all.
                from app.scraper import otp_store as _os
                _os.clear_timeouts(self._job_id_str)

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
                // Small text elements that contain relative time keywords
                const candidates = document.querySelectorAll(
                    'p[class*="--small"], span[class*="--small"], [class*="publish"], [class*="date"]'
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
            return self._parse_relative_time(raw)
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
                client = self._client()
                for i, url in enumerate(images):
                    try:
                        # Streamed with a running byte cap. client.get() reads
                        # the whole body first, so a single oversized file was
                        # already in memory by the time anyone could object.
                        raw = bytearray()
                        too_big = False
                        async with client.stream("GET", url, timeout=30) as response:
                            if response.status_code != 200:
                                continue
                            declared = response.headers.get("content-length")
                            if declared and declared.isdigit() and int(declared) > max_bytes:
                                logger.warning(
                                    f"{divar_id}: image {i+1} declares "
                                    f"{int(declared)}B > {max_bytes}B cap — skipped")
                                continue
                            async for chunk in response.aiter_bytes():
                                raw.extend(chunk)
                                if len(raw) > max_bytes:
                                    too_big = True
                                    break
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
    
    async def _load_rotation_pool(self) -> List[str]:
        """Valid saved Divar accounts, least-spent first — the rotation
        candidates, in the order they should be reached for."""
        if not self.db_session:
            return []
        try:
            from app.models.cookie import Cookie as CookieModel
            rows = (await self.db_session.execute(
                select(CookieModel)
                .where(CookieModel.is_valid == True)
                .order_by(CookieModel.reveals.asc(),
                          CookieModel.last_used_at.asc().nullsfirst())
            )).scalars().all()
            # de-dupe while keeping order (one entry per phone)
            seen, pool = set(), []
            for r in rows:
                if r.phone_number and r.phone_number not in seen:
                    seen.add(r.phone_number)
                    pool.append(r.phone_number)
            return pool
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
            n = (await self.db_session.execute(
                _select(func.count()).select_from(Cookie)
                .where(Cookie.is_valid == True)  # noqa: E712
            )).scalar() or 0
            return max(1, int(n))
        except Exception as e:
            logger.warning(f"[rotate] could not count usable accounts: {e}")
            return 1

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
            await db.commit()
            logger.info(f"[rotate] {phone} marked spent after a Divar challenge")
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
            return int((await db.execute(
                select(_func.count()).select_from(CookieModel).where(
                    CookieModel.is_valid == True,          # noqa: E712
                    _func.coalesce(CookieModel.reveals, 0) < every,
                ))).scalar() or 0)
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
            rows = (await db.execute(
                select(CookieModel).where(CookieModel.is_valid == True))).scalars().all()  # noqa: E712
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

    def _note_account_challenged(self) -> None:
        """Divar asked this account for an SMS code — rotate at the next chance.

        Called from ContactExtractor the moment the OTP modal appears, before it
        settles in to wait for a human. Deliberately synchronous and trivial: it
        runs while a modal is on screen, so it only records the fact. The switch
        itself happens between listings, where it is safe to navigate.
        """
        self._force_rotate = True
        from app import metrics as _mx
        _mx.scrape_challenges.inc()

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

        # every <= 0 disables the threshold, but never the challenge response:
        # being asked for a code is Divar telling us to move.
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
        # invalid mid-run was previously never seen.
        pool = await self._load_rotation_pool()
        if pool:
            self._rotation_pool = pool
        if len(self._rotation_pool) < 2:
            # Only one account exists — there is nothing to rotate to, and that
            # will not change by asking again on the next listing. Clear the
            # counters so this does not re-run the pool query every time.
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
            try:
                restored = await self.auth.restore_session(candidate)
            except Exception as e:
                logger.warning(f"[rotate] restore failed for {candidate}: {e}")
                restored = False
            if restored:
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
                    await _jl.record(
                        _jid, _jl.SESSION,
                        f"چرخش شماره: از {previous or '—'} به {candidate}",
                        previous=previous, now=candidate)

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
                _mx.scrape_rotations.labels("challenged" if forced else "threshold").inc()
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
                    f"{' (Divar asked it for a code)' if forced else ''}")
                # Let the restored session settle before it starts opening ads.
                # A switch followed instantly by a page load is the part that
                # reads as automated, not the overall pace.
                await self._human_like_delay(3.0, 6.0)
                return True
            logger.warning(f"[rotate] session for {candidate} not usable — trying next")

        # Every candidate failed. Leave the counter high so the next reveal
        # retries, but back it off a little: restoring a session navigates the
        # browser, and retrying that on every single listing would cost more
        # than the rotation saves.
        self._reveals_since_rotation = max(every - 5, 0) if every > 0 else 0
        self._force_rotate = False
        logger.info("[rotate] no alternative account could be restored; staying on current")
        return False

    # Filter names as the person who set them sees them in the panel. The
    # tally buckets on the reason string are English field names, which are
    # useless in a message whose whole job is to say «this is the filter that
    # cost you the listings».
    _FILTER_LABELS_FA = {
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
    }

    # Divar's own words for what kind of ad this is, mapped to the two the
    # validator knows. 'buy' is the scraper's term and 'sale' is the
    # validator's -- without this every sale listing would be graded against
    # the rent rules and reported as missing a rent price.
    _VALIDATOR_TYPES = {"buy": "sale", "sale": "sale", "rent": "rent"}

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
                logger.info(
                    f"{divar_id} is already stored but has no phone number — "
                    "re-scraping to fill it in")
                return False
            return True
        except Exception as e:
            logger.error(f"Failed to check property existence: {e}")
            return False
    
    async def save_property(self, property_data: Dict[str, Any]) -> Optional[Property]:
        """Save property to database.

        Sets _last_save_error on every path that gives up, so the run can put
        the reason on the skipped row. Six listings came back as «ذخیره نشد»
        with nothing else, and «ذخیره نشد» is the observation, not the cause.
        """
        self._last_save_error = None
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
                await self.db_session.commit()
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
                logger.info(f"Saved new property: {divar_id} with tag {property_data['tag_number']}")

                # Trigger CRM pipeline (lead + notification)
                try:
                    from app.crm.pipeline import process_new_property
                    await process_new_property(self.db_session, new_property)
                except Exception as crm_err:
                    logger.warning(f"CRM pipeline error (non-fatal): {crm_err}")

                return new_property
                
        except Exception as e:
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
    ) -> ScrapingJob:
        """Start a complete scraping job for a city and category"""

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
            # job the user already stopped back to life.
            if job.status == "cancelled":
                logger.info(f"Job {job_id} was cancelled before it started — not running it")
                return job
            job.status = "running"
            job.started_at = datetime.now()
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
            }.items() if v is not None}
            logger.info(f"Starting scraping job for {city}/{category} | filters={active_filters}")

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
                collect_target = min(max(max_items * 5, max_items + 100), 1500)

            # Hand Divar the filters it can apply itself, before the feed is
            # loaded. Everything it will not narrow on (rooms, amenities) is
            # still checked per listing after the ad is opened.
            try:
                from app.services.divar_count import build_search_query
                self._search_query = build_search_query(
                    advertiser_type=advertiser_type, has_images=has_images,
                    min_price=min_price, max_price=max_price,
                    min_deposit=min_deposit, max_deposit=max_deposit,
                    min_rent=min_rent, max_rent=max_rent,
                    min_area=min_area, max_area=max_area,
                )
                if self._search_query:
                    logger.info(f"[collect] Divar-side filters: {self._search_query}")
                    await job_log.record(
                        job.job_id, job_log.PAGE,
                        f"فیلترها به خود دیوار داده شد: {self._search_query}",
                        query=self._search_query)
            except Exception as e:
                # A filter we cannot express is not a reason to abandon the run;
                # it just means the local pass does more work, as before.
                logger.warning(f"[collect] could not build the Divar query: {e}")
                self._search_query = ""

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

            if _stop in ("refused", "partly-refused"):
                counts = ", ".join(f"HTTP {k}×{v}" for k, v in sorted((_detail or {}).items()))
                msg = (f"دیوار در حین جمع‌آوری آگهی‌ها دسترسی را رد کرد ({counts}). "
                       f"فقط {len(all_listings)} آگهی از فهرست خوانده شد — "
                       "این اسکرپ کامل نیست.")
                logger.error(f"[collect] {msg}")
                await job_log.record(job.job_id, job_log.CHALLENGE, msg,
                                     level="error", collected=len(all_listings),
                                     target=collect_target, refusals=_detail)
                # A refusal that stopped us dead is a failed run, not a short one.
                # Saying otherwise is the bug being fixed here.
                if _stop == "refused":
                    job.status = "failed"
                    job.error_message = msg
                    job.finish_reason = msg
                    job.completed_at = datetime.now()
                    await self.db_session.commit()
                    return job

            elif _stop == "error":
                msg = (f"جمع‌آوری فهرست آگهی‌ها با خطا متوقف شد ({_detail}). "
                       f"{len(all_listings)} آگهی تا آن لحظه خوانده شده بود.")
                logger.error(f"[collect] {msg}")
                await job_log.record(job.job_id, job_log.ERROR, msg, level="error",
                                     collected=len(all_listings), target=collect_target)
                job.status = "failed"
                job.error_message = msg
                job.finish_reason = msg
                job.completed_at = datetime.now()
                await self.db_session.commit()
                return job

            elif _short:
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
            # beside what the listing page actually gave up.
            #
            # «۱۱۳ آگهی با این فیلترها در دیوار هست» and «۱۱۹ نامزد جمع شد» are
            # answers to two different questions — an estimate Divar computed
            # when the button was pressed, and what the page yielded now — and
            # having only one of them on screen is what made the pair read as a
            # contradiction. Advisory only: it must not become the progress
            # bar's denominator, because it can be larger or smaller than the
            # pool the run actually walks, and either way the bar would lie.
            try:
                from app.services import divar_count as dc
                _form = dc.build_form_data(
                    category,
                    advertiser_type=advertiser_type, has_images=has_images,
                    min_price=min_price, max_price=max_price,
                    min_deposit=min_deposit, max_deposit=max_deposit,
                    min_rent=min_rent, max_rent=max_rent,
                    min_area=min_area, max_area=max_area,
                )
                _divar_total, _count_err = await dc.fetch_post_count(city, _form)
                if _divar_total is not None:
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
            except Exception as e:
                # Advisory. It must never cost a run.
                logger.warning(f"[count] could not ask Divar for its total: {e}")

            # Progress is position in the candidate pool.
            #
            # It used to be measured against max_items, which is a target of
            # *saved* listings — a different quantity from the candidates being
            # counted into it. A pool of 119 against a target of 100 therefore
            # read 100% at candidate 100 and then went on scraping for another
            # nineteen. The two only ever coincided when every candidate was
            # saved, which is the case that never happens.
            #
            # The loop ends when the pool runs out or the target is met,
            # whichever comes first, so the pool is the honest denominator: the
            # bar cannot fill early, and the completion below fills it for the
            # run that stops at its target with candidates to spare.
            job.total_items = len(all_listings)
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
            }
            
            # Scrape each property detail
            examined = 0
            for i, listing in enumerate(all_listings):
                try:
                    # Stop as soon as the numeric target is reached
                    # (in whole-day mode max_items is None — no cap).
                    if max_items and job.new_items >= max_items:
                        logger.info(f"Reached target of {max_items} saved listings — stopping")
                        break

                    # Check if job was cancelled
                    await self.db_session.refresh(job)
                    if job.status == "cancelled":
                        logger.info(f"Job {job.job_id} was cancelled, stopping scraping")
                        return job

                    # Counted here rather than from `i`, so that candidates the
                    # run never reached are not reported as candidates it
                    # dropped. The two are different answers to «where did they
                    # go?».
                    examined += 1

                    # Check if already scraped
                    if await self.property_exists(listing['divar_id']):
                        logger.info(f"Property already exists: {listing['divar_id']}")
                        job.updated_items += 1
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
                        wants_contact=lambda pd: self.pre_contact_skip(
                            pd, _listing_type, _pre_filters),
                    )
                    
                    if detail:
                        # Merge with listing data
                        property_data = {**listing, **detail}
                        property_data['city_name'] = CITIES.get(city, {}).get('name', city)
                        property_data['category_name'] = CATEGORIES.get(category, {}).get('name', category)
                        listing_type = CATEGORIES.get(category, {}).get('type', 'unknown')
                        property_data['listing_type'] = listing_type

                        did = listing['divar_id']

                        _why: Dict[str, str] = {}

                        def _skip(reason: str) -> bool:
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

                        # ── Price filters (listing-type specific) ──────────────────
                        if listing_type == 'buy':
                            price = detail.get('total_price') or detail.get('price')
                            if min_price and price and price < min_price:
                                skip = _skip(f"price {price} < min {min_price}")
                            elif max_price and price and price > max_price:
                                skip = _skip(f"price {price} > max {max_price}")
                            ppm = detail.get('price_per_meter')
                            if not skip and min_price_per_meter and ppm and ppm < min_price_per_meter:
                                skip = _skip(f"price/m² {ppm} < min {min_price_per_meter}")
                            elif not skip and max_price_per_meter and ppm and ppm > max_price_per_meter:
                                skip = _skip(f"price/m² {ppm} > max {max_price_per_meter}")
                        elif listing_type == 'rent':
                            deposit = detail.get('deposit')
                            rent = detail.get('rent_price')
                            if min_deposit and deposit and deposit < min_deposit:
                                skip = _skip(f"deposit {deposit} < min {min_deposit}")
                            elif max_deposit and deposit and deposit > max_deposit:
                                skip = _skip(f"deposit {deposit} > max {max_deposit}")
                            elif min_rent and rent and rent < min_rent:
                                skip = _skip(f"rent {rent} < min {min_rent}")
                            elif max_rent and rent and rent > max_rent:
                                skip = _skip(f"rent {rent} > max {max_rent}")

                        # ── Area filter ────────────────────────────────────────────
                        if not skip:
                            area = detail.get('area')
                            if min_area and area and area < min_area:
                                skip = _skip(f"area {area} < min {min_area}")
                            elif max_area and area and area > max_area:
                                skip = _skip(f"area {area} > max {max_area}")

                        # ── Rooms filter ───────────────────────────────────────────
                        if not skip:
                            rooms = detail.get('rooms')
                            if min_rooms is not None and rooms is not None and rooms < min_rooms:
                                skip = _skip(f"rooms {rooms} < min {min_rooms}")
                            elif max_rooms is not None and rooms is not None and rooms > max_rooms:
                                skip = _skip(f"rooms {rooms} > max {max_rooms}")

                        # ── Boolean amenity filters ────────────────────────────────
                        bool_filters = [
                            ('has_images', has_images),
                            ('has_elevator', has_elevator),
                            ('has_parking', has_parking),
                            ('has_storage', has_storage),
                            ('has_balcony', has_balcony),
                        ]
                        for field, wanted in bool_filters:
                            if not skip and wanted is not None:
                                actual = bool(detail.get(field) or (field == 'has_images' and detail.get('images')))
                                if wanted and not actual:
                                    skip = _skip(f"{field} required but not present")
                                elif not wanted and actual:
                                    skip = _skip(f"{field} must be absent")

                        # ── Advertiser type filter ─────────────────────────────────
                        # An undetermined type is a miss, not a match. Letting it
                        # through is what put agency ads in the results of a
                        # «شخصی» scrape: every ad whose type could not be read
                        # satisfied the filter by default. The publish-date
                        # filter below has always treated unknown this way.
                        if not skip and advertiser_type:
                            actual_type = detail.get('advertiser_type')
                            if not actual_type:
                                skip = _skip(
                                    f"advertiser_type unknown; {advertiser_type} filter active")
                            elif actual_type != advertiser_type:
                                skip = _skip(f"advertiser_type {actual_type} != {advertiser_type}")

                        # ── Age filter ─────────────────────────────────────────────
                        if not skip and max_age_hours and not date_mode:
                            posted = detail.get('posted_at')
                            if posted and posted < datetime.now() - timedelta(hours=max_age_hours):
                                skip = _skip(f"posted_at {posted} older than {max_age_hours}h")

                        # ── Exact publish-date filter (date mode) ─────────────────
                        if not skip and date_mode:
                            posted = detail.get('posted_at')
                            if not posted:
                                skip = _skip("posted_at unknown; date filter active")
                            elif posted.date() > target_day:
                                skip = _skip(f"posted {posted.date()} is after {target_day}")
                            elif posted.date() < target_day:
                                skip = _skip(
                                    f"posted {posted.date()} is before {target_day}")

                        if skip:
                            # Same as the other site: a filtered-out listing is
                            # still a listing we processed.
                            job.scraped_items = i + 1
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
                            # Nothing may hold a transaction across a sleep —
                            # see the note at the other delay below.
                            await self.db_session.commit()
                            await self._human_like_delay()
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
                        
                        # Grade the record before storing it. Recorded, never
                        # enforced: we already spent a contact reveal on this
                        # listing, so a flagged row beats a dropped one. The
                        # score is what makes "is the scraper getting the data
                        # right?" a question with an answer.
                        self._grade_property(property_data)

                        # Save to database
                        saved = await self.save_property(property_data)
                        if saved:
                            job.new_items += 1
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
                    job.scraped_items = i + 1
                    await self.db_session.commit()

                    # spread the load across saved Divar accounts
                    await self.maybe_rotate_account()

                    # Nothing may hold a transaction across a sleep.
                    #
                    # maybe_rotate_account queries and can write, and the delay
                    # below is now 2-5s normally, up to four times that on the
                    # long-pause branch, and up to five MINUTES when Divar has
                    # refused us and the backoff is engaged. Postgres closes a
                    # connection idle in a transaction for 60s, so any of those
                    # would end the run.
                    await self.db_session.commit()
                    await self._human_like_delay()

                except Exception as e:
                    logger.error(f"Failed to process listing: {e}")
                    job.failed_items += 1
                    fail_tally[type(e).__name__] = fail_tally.get(type(e).__name__, 0) + 1
                    try:
                        await skipped_listings.record(
                            job.job_id, divar_id=listing.get('divar_id'),
                            url=listing.get('url'), title=listing.get('title'),
                            reason="failed", detail=type(e).__name__)
                    except Exception:
                        pass
                    try:
                        await self.db_session.rollback()
                        await self.db_session.commit()
                    except Exception:
                        pass
            
            # Complete job
            job.status = "completed"
            job.completed_at = datetime.now()
            # A run that met its target stops with candidates left over. The
            # work is over, so the bar reads full rather than freezing at the
            # candidate it happened to stop on.
            job.scraped_items = job.total_items
            await self.db_session.commit()
            # The FINISH event is recorded further down, AFTER finish_reason has
            # been composed. Written here it always said «تمام شد» with no
            # reason attached, because the reason does not exist yet at this
            # point — which is exactly what made a cut-short run read as a
            # clean one in the log.
            
            # The account that finished the job has the freshest session of all;
            # losing it would make the next job start from a stale snapshot.
            await self._persist_active_session()

            logger.info(f"Scraping job completed. New: {job.new_items}, Updated: {job.updated_items}, Failed: {job.failed_items}")
            # Say so when the feed ran dry before the target was met, rather
            # than completing at «۴۰ / ۲۰۰» with no explanation.
            #
            # This used to skip date_mode entirely, which is the one case that
            # most needs saying: a single day holds however many ads it holds,
            # so a run capped at 126 finishing at 42 is the day being smaller
            # than the cap, not a fault. Unexplained, it reads as a fault.
            if max_items and job.new_items < max_items:
                if date_mode:
                    logger.info(
                        f"Day exhausted: {job.new_items}/{max_items} new for {target_day}. "
                        f"{job.updated_items} were already in the database."
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
                        f"{len(all_listings)}. {job.updated_items} were already in the database. "
                        "Divar has no more matching listings, or the filters are too tight."
                    )
                    finish_reason = (
                        f"آگهی بیشتری پیدا نشد — {job.new_items} از {max_items} درخواستی. "
                        f"{job.updated_items} آگهی از قبل در پایگاه داده بود. "
                        "یا دیوار آگهی دیگری ندارد یا فیلترها خیلی تنگ‌اند"
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

            # A run whose OTP prompts went unanswered finishes fast and looks
            # normal, but half its listings have no phone number. Say so.
            try:
                from app.scraper import otp_store
                if otp_store.is_cancelled(job.job_id):
                    note = ("کد تأیید دیوار وارد نشد — آگهی‌ها ذخیره شدند "
                            "ولی شمارهٔ تماس بعضی‌شان خالی است")
                    finish_reason = f"{finish_reason}؛ {note}" if finish_reason else note
            except Exception as e:
                logger.warning(f"could not check OTP suppression: {e}")

            job.finish_reason = finish_reason
            await self.db_session.commit()

            from app.services import job_log
            _summary = (f"اسکرپ تمام شد — {job.new_items} تازه، "
                        f"{job.updated_items} از قبل ذخیره شده بود، "
                        f"{job.failed_items} ناموفق")
            if finish_reason:
                _summary += f"\n{finish_reason}"
            # A run that asked for N and saved fewer is worth flagging even when
            # the reason is benign, so it does not read as an unqualified success.
            _short_of_target = bool(max_items and job.new_items < max_items)
            await job_log.record(
                job.job_id, job_log.FINISH, _summary,
                level="warning" if _short_of_target else "info",
                new=job.new_items, updated=job.updated_items,
                failed=job.failed_items, pages=job.scraped_pages,
                requested=max_items, candidates=len(all_listings),
                skipped=(sum(skip_tally.values()) or None))
            # Where the candidates went, per reason. This tally has always been
            # computed and only ever written to a log file nobody reads per-job,
            # so «۴۲ نامزد، ۳ ذخیره» looked like a fault when it was usually the
            # filters doing exactly what they were told.
            if job.updated_items:
                await job_log.record(
                    job.job_id, job_log.PAGE,
                    f"{job.updated_items} آگهی از قبل در پایگاه داده بود و دوباره ذخیره نشد",
                    duplicates=job.updated_items)

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
            _accounted = (job.new_items + job.updated_items
                          + job.failed_items + _dropped)
            _parts = [f"{job.new_items} تازه", f"{job.updated_items} تکراری"]
            if job.failed_items:
                _named = "، ".join(f"{k}: {v}" for k, v in
                                   sorted(fail_tally.items(), key=lambda kv: -kv[1]))
                _parts.append(f"{job.failed_items} ناموفق"
                              + (f" ({_named})" if _named else ""))
            _parts += [f"{v} {self._FILTER_LABELS_FA.get(k, k)}"
                       for k, v in sorted(skip_tally.items(), key=lambda kv: -kv[1])]
            _unreached = len(all_listings) - examined
            if _unreached > 0:
                _parts.append(f"{_unreached} بررسی‌نشده")
            _unaccounted = examined - _accounted
            if _unaccounted > 0:
                _parts.append(f"{_unaccounted} بی‌حساب")
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
            job.status = "failed"
            job.error_message = str(e)
            job.completed_at = datetime.now()
            await self.db_session.commit()
            logger.error(f"Scraping job failed: {e}")
            from app.services import job_log
            await job_log.record(
                job.job_id, job_log.ERROR,
                f"اسکرپ با خطا متوقف شد: {type(e).__name__}: {e}",
                level="error", error_type=type(e).__name__,
                new=job.new_items, updated=job.updated_items)
        
        return job
    