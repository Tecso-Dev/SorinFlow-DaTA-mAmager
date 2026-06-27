"""
SorinFlow Divar Scraper - Main Scraper Module
Handles scraping property listings from Divar.ir
"""
import asyncio
import random
import re
import uuid
from datetime import datetime, timedelta
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
from app.models.property import Property, City, Category
from app.models.scraping_job import ScrapingJob
from app.models.proxy import Proxy
from app.scraper.stealth import StealthConfig, STEALTH_JS, get_browser_args, get_context_options
from app.scraper.auth import DivarAuth
from app.scraper.contact_extractor import ContactExtractor
from app.scraper.parsers import (
    extract_property_details as _parse_property_details,
    extract_price_info as _parse_price_info,
)

settings = get_settings()


class DivarScraper:
    """Main scraper class for Divar.ir real estate listings"""

    BASE_URL = "https://divar.ir"

    # Maps our category slug → substrings expected in the Divar detail-page URL.
    # Divar builds URLs from the listing *title*, not the category name, so we
    # use property-type nouns (آپارتمان، خانه …) rather than action-prefix combos
    # (خرید-خانه) which almost never appear in real listing URLs.
    CATEGORY_URL_PATTERNS: Dict[str, List[str]] = {
        # Apartment: title may use آپارتمان, واحد (unit), or مسکن (housing)
        # e.g. اجاره-واحد-۱۲۵-متر / واحد-۱۱۰-متری / اجاره-مسکن / اجاره-تک-واحدی
        'rent-apartment': ['اجاره-آپارتمان', 'اجاره-اپارتمان', 'کرایه-آپارتمان',
                           'آپارتمان', 'اپارتمان', 'واحد', 'اجاره-مسکن'],
        'buy-apartment':  ['آپارتمان', 'اپارتمان', 'واحد'],

        # Residential (broad): title is the property type alone — no buy/rent prefix
        'rent-residential': ['آپارتمان', 'اپارتمان', 'خانه', 'ویلا', 'مسکونی', 'واحد', 'سوئیت', 'اجاره-مسکن'],
        'buy-residential':  ['آپارتمان', 'اپارتمان', 'خانه', 'ویلا', 'مسکونی', 'واحد', 'سوئیت', 'کلنگی'],

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

        self.current_job: Optional[ScrapingJob] = None
        self.request_count = 0
        self.session_start = datetime.now()
    
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
                        _res = await self.db_session.execute(
                            _select(CookieModel)
                            .where(CookieModel.is_valid == True)
                            .order_by(CookieModel.updated_at.desc())
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
    
    async def close(self):
        """Close browser and cleanup resources"""
        try:
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
    
    async def _human_like_delay(self, min_delay: float = None, max_delay: float = None):
        """Add human-like random delay"""
        min_d = min_delay or self.stealth_config.min_delay
        max_d = max_delay or self.stealth_config.max_delay
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
    
    async def _check_rate_limit(self):
        """Check and enforce rate limiting"""
        self.request_count += 1
        
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
            logger.info("Session request limit reached. Restarting browser...")
            await self.close()
            await asyncio.sleep(10)
            await self.initialize()
            self.request_count = 0
            self.session_start = datetime.now()
    
    async def scrape_listing_page(
        self,
        city: str,
        category: str,
        page_num: int = 1,
        last_post_date: Optional[int] = None,
    ) -> tuple:
        """Scrape a listing page to get property cards.

        Returns (listings, last_post_date) where last_post_date is the cursor
        for Divar's cursor-based pagination (None if unavailable).
        """
        listings = []
        next_last_post_date: Optional[int] = None

        # ── Pages 2+: use direct API as primary method.
        #    Browser re-navigation for subsequent pages is unreliable (often
        #    intercepts 0 API responses). Direct httpx with the cursor is faster
        #    and more consistent.
        if page_num > 1:
            direct_listings, direct_lpd = await self._fetch_listings_direct_api(
                city, category, page_num, last_post_date
            )
            if direct_listings:
                logger.info(f"Page {page_num}: {len(direct_listings)} listings via direct API (cursor={last_post_date})")
                return direct_listings, direct_lpd
            logger.warning(f"Page {page_num}: direct API returned 0, falling back to browser")

        # ── Approach 1: intercept the API responses the React app loads ──────
        captured_api_responses: list = []

        expected_path = f'/s/{city}/{category}'

        async def _on_response(response):
            try:
                if 'api.divar.ir' in response.url and response.status == 200:
                    ct = response.headers.get('content-type', '')
                    if 'json' in ct:
                        url = response.url
                        # Capture both the classic web-search URL and the
                        # newer postlist/w/search endpoint the browser actually uses
                        is_search = (
                            (city in url and category in url)
                            or '/postlist/w/search' in url
                        )
                        if is_search:
                            data = await response.json()
                            captured_api_responses.append(data)
                            logger.info(f"Intercepted API: {url}")
            except Exception:
                pass

        self.page.on("response", _on_response)

        try:
            url = f"{self.BASE_URL}/s/{city}/{category}"
            if last_post_date:
                url += f"?last_post_date={last_post_date}"
            elif page_num > 1:
                url += f"?page={page_num}"

            logger.info(f"Scraping listing page: {url}")
            await self._check_rate_limit()

            await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3)  # Give React time to fire API requests
            await self._simulate_scroll()
            await asyncio.sleep(2)

            # Wait for listing links (or timeout gracefully)
            try:
                await self.page.wait_for_selector('a[href*="/v/"]', timeout=20000)
            except Exception:
                logger.warning("Primary selector timed out, continuing...")

            actual_url = self.page.url
            if actual_url != url:
                logger.warning(f"Redirected: {url} → {actual_url}")
                # If redirected away from the target category (e.g. CAPTCHA page, home page)
                # stop scraping this page to avoid collecting unrelated listings
                if expected_path not in actual_url:
                    logger.warning(f"Redirected away from target category — skipping page {page_num}")
                    return [], None

            # Try intercepted API data first
            logger.info(f"Captured {len(captured_api_responses)} API responses on page {page_num}")
            for idx, api_data in enumerate(captured_api_responses):
                if isinstance(api_data, dict):
                    import json as _json
                    logger.info(f"[API#{idx}] keys={list(api_data.keys())[:12]}")
                    # Dump first 1500 chars so we can see the exact response shape
                    logger.info(f"[API#{idx}] body={_json.dumps(api_data, ensure_ascii=False)[:1500]}")
                parsed, lpd = self._parse_api_response(api_data)
                logger.info(f"[API#{idx}] parsed={len(parsed)} lpd={lpd}")
                if parsed:
                    listings.extend(parsed)
                if lpd and lpd > 0 and not next_last_post_date:
                    next_last_post_date = lpd

            # Fallback: extract last_post_date from the page's JS state
            if not next_last_post_date:
                try:
                    lpd_js = await self.page.evaluate("""
                        (() => {
                            const nd = window.__NEXT_DATA__;
                            if (nd) {
                                const m = JSON.stringify(nd).match(/"last_post_date"\s*:\s*(-?\d+)/);
                                if (m) return parseInt(m[1]);
                            }
                            return null;
                        })()
                    """)
                    if lpd_js and lpd_js > 0:
                        next_last_post_date = lpd_js
                        logger.info(f"Got last_post_date={lpd_js} from page JS state")
                except Exception as e:
                    logger.debug(f"JS last_post_date extraction failed: {e}")

            if listings:
                # Remove duplicates by divar_id
                seen = set()
                listings = [l for l in listings if not (l['divar_id'] in seen or seen.add(l['divar_id']))]
                logger.info(f"Got {len(listings)} listings via API interception on page {page_num}")
                return listings, next_last_post_date

            # ── Approach 2: parse rendered HTML ──────────────────────────────
            content = await self.page.content()
            soup = BeautifulSoup(content, 'lxml')

            cards = soup.select('a.kt-post-card__action')
            if not cards:
                cards = soup.select('div.post-card-item a')
            if not cards:
                cards = soup.select('article a[href*="/v/"]')
            if not cards:
                cards = soup.select('a[href*="/v/"]')

            for card in cards:
                try:
                    listing = self._parse_listing_card(card)
                    if listing:
                        listings.append(listing)
                except Exception as e:
                    logger.warning(f"Failed to parse listing card: {e}")

            if listings:
                logger.info(f"Got {len(listings)} listings via HTML parsing on page {page_num}")
                return listings, next_last_post_date

            # Log diagnostics and save screenshot when both approaches fail
            page_title = soup.title.get_text(strip=True) if soup.title else "(no title)"
            body_text = soup.get_text(separator=' ', strip=True)[:400]
            logger.warning(
                f"No listing cards found on page {page_num} | "
                f"title='{page_title}' | url={actual_url} | "
                f"api_responses={len(captured_api_responses)} | "
                f"body_snippet={body_text!r}"
            )
            try:
                screenshot_path = self.images_dir / f"debug_listing_p{page_num}.png"
                await self.page.screenshot(path=str(screenshot_path))
                logger.info(f"Saved debug screenshot: {screenshot_path}")
            except Exception:
                pass

        except Exception as e:
            logger.error(f"Failed to scrape listing page via Playwright: {e}")
        finally:
            self.page.remove_listener("response", _on_response)

        # ── Approach 3: direct httpx call to Divar JSON API ──────────────────
        if not listings:
            listings, next_last_post_date = await self._fetch_listings_direct_api(
                city, category, page_num, last_post_date
            )

        logger.info(f"Found {len(listings)} listings on page {page_num}")
        return listings, next_last_post_date

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

        base_url = f"https://api.divar.ir/v8/web-search/{city}/{category}"
        params: dict = {}
        if last_post_date:
            params['last_post_date'] = last_post_date
        elif page_num > 1:
            params['page'] = page_num

        endpoints = [
            base_url + (f"?{'&'.join(f'{k}={v}' for k, v in params.items())}" if params else ""),
            base_url,  # fallback without params
        ]
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
                for api_url in endpoints:
                    try:
                        resp = await client.get(api_url, headers=headers)
                        logger.info(f"Direct API GET {api_url} → {resp.status_code}")
                        if resp.status_code == 200:
                            data = resp.json()
                            parsed, lpd = self._parse_api_response(data)
                            if parsed:
                                logger.info(f"Got {len(parsed)} listings via direct API")
                                return parsed, lpd
                            else:
                                top_keys = list(data.keys())[:6] if isinstance(data, dict) else type(data).__name__
                                logger.info(f"Direct API returned 200 but 0 parsed — top keys: {top_keys}")
                    except Exception as e:
                        logger.debug(f"Direct API attempt failed ({api_url}): {e}")

                # POST variant – some Divar endpoints accept JSON body
                try:
                    post_body: dict = {"city_ids": [city]}
                    if last_post_date:
                        post_body['last_post_date'] = last_post_date
                    elif page_num > 1:
                        post_body['page'] = page_num
                    resp = await client.post(
                        base_url,
                        headers={**headers, "Content-Type": "application/json"},
                        json=post_body,
                    )
                    logger.info(f"Direct API POST {base_url} → {resp.status_code}")
                    if resp.status_code == 200:
                        data = resp.json()
                        parsed, lpd = self._parse_api_response(data)
                        if parsed:
                            logger.info(f"Got {len(parsed)} listings via direct API (POST)")
                            return parsed, lpd
                except Exception as e:
                    logger.debug(f"Direct API POST failed: {e}")
        except Exception as e:
            logger.warning(f"Direct API fetch failed: {e}")
        return listings, next_last_post_date

    async def _collect_listings_api(
        self, city: str, category: str, target_count: int
    ) -> List[Dict[str, Any]]:
        """Collect listings via direct API calls with cursor-based pagination.

        More reliable than scroll-based collection because it doesn't depend
        on Divar's infinite-scroll UI (which often defaults to map view).
        Falls back to scroll if the direct API yields nothing.
        """
        all_listings: List[Dict[str, Any]] = []
        seen_ids: set = set()
        last_post_date: Optional[int] = None
        page_num = 1
        consecutive_empty = 0
        max_pages = max(10, (target_count // 20) + 3)

        for _ in range(max_pages):
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
                f"[API collect] page={page_num} got={len(batch)} "
                f"new={new_count} total={len(all_listings)}/{target_count}"
            )

            if lpd:
                last_post_date = lpd

            if len(all_listings) >= target_count:
                break

            if not batch:
                consecutive_empty += 1
                if consecutive_empty >= 2:
                    logger.info("[API collect] 2 empty pages — stopping")
                    break
            else:
                consecutive_empty = 0

            page_num += 1
            await asyncio.sleep(random.uniform(0.8, 1.5))

        if not all_listings:
            logger.warning("[API collect] Direct API yielded nothing — falling back to scroll")
            all_listings = await self._collect_listings_scroll(city, category, target_count)
        elif len(all_listings) < target_count:
            logger.info(
                f"[API collect] Got {len(all_listings)}/{target_count} from direct API; "
                f"supplementing with scroll"
            )
            extra = await self._collect_listings_scroll(
                city, category, target_count - len(all_listings)
            )
            for lst in extra:
                if lst['divar_id'] not in seen_ids:
                    seen_ids.add(lst['divar_id'])
                    all_listings.append(lst)

        return all_listings[:target_count]

    async def _switch_to_list_view(self) -> bool:
        """Attempt to switch Divar from map view to list view. Returns True if switched."""
        for sel in [
            'button[data-testid="list-tab"]',
            'button[data-testid="LIST"]',
            '[aria-label="لیست"]',
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
                const keywords = ['لیست', 'list', 'LIST', 'فهرست'];
                const elems = [...document.querySelectorAll('button, [role=tab], a')];
                for (const kw of keywords) {
                    const el = elems.find(e => (e.innerText || '').includes(kw) || e.getAttribute('aria-label') === kw);
                    if (el) { el.click(); return true; }
                }
                return false;
            }""")
            if clicked:
                await asyncio.sleep(2)
                logger.info("[view] switched to list view via JS keyword search")
                return True
        except Exception as e:
            logger.debug(f"[view] JS switch failed: {e}")

        logger.warning("[view] Could not switch to list view — proceeding in current view")
        return False

    async def _collect_from_browser_dom(
        self, city: str, category: str, target_count: int
    ) -> List[Dict[str, Any]]:
        """Collect listings by extracting /v/ token links from the live rendered DOM.

        Works regardless of API response format changes — reads what Divar has
        already rendered via JS, including cards that appear after scrolling.
        Also intercepts API responses as a bonus to get richer metadata.
        """
        all_listings: List[Dict[str, Any]] = []
        seen_ids: set = set()
        pending_api: list = []

        async def _on_resp(response):
            try:
                if 'api.divar.ir' not in response.url or response.status != 200:
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

        self.page.on("response", _on_resp)
        try:
            url = f"{self.BASE_URL}/s/{city}/{category}"
            logger.info(f"[dom] Loading {url} | target={target_count}")
            await self._check_rate_limit()
            try:
                await self.page.goto(url, wait_until="networkidle", timeout=45000)
            except Exception:
                # networkidle timeout is OK — page is still usable
                logger.info("[dom] networkidle timeout — continuing with loaded content")
            await asyncio.sleep(3)

            await self._switch_to_list_view()
            await asyncio.sleep(3)

            vp = self.page.viewport_size or {"width": 1280, "height": 720}
            await self.page.mouse.move(vp["width"] // 2, vp["height"] // 2)

            no_new_streak = 0
            max_scrolls = max(40, target_count // 3)

            for scroll_n in range(max_scrolls):
                prev = len(all_listings)

                # Drain any captured API responses for richer metadata
                for data in list(pending_api):
                    pending_api.remove(data)
                    parsed, _ = self._parse_api_response(data)
                    for lst in parsed:
                        if lst['divar_id'] not in seen_ids:
                            seen_ids.add(lst['divar_id'])
                            all_listings.append(lst)

                # Extract /v/ links directly from live rendered DOM
                dom_items = await self.page.evaluate("""() => {
                    return [...document.querySelectorAll('a[href*="/v/"]')]
                        .map(a => ({
                            href: a.href,
                            title: (a.innerText || '').trim().substring(0, 120)
                        }));
                }""")
                for item in (dom_items or []):
                    href = item.get('href', '')
                    if '/v/' not in href:
                        continue
                    raw = href.split('/v/', 1)[1].split('?')[0].split('/')[0]
                    if not raw or len(raw) < 4 or raw in seen_ids:
                        continue
                    seen_ids.add(raw)
                    all_listings.append({
                        'divar_id': raw,
                        'url': f"https://divar.ir/v/{raw}",
                        'title': item.get('title') or None,
                        'descriptions': [],
                    })

                gained = len(all_listings) - prev
                logger.info(
                    f"[dom scroll #{scroll_n}] +{gained} items | total {len(all_listings)}/{target_count}"
                )

                if len(all_listings) >= target_count:
                    break

                if gained == 0:
                    no_new_streak += 1
                    if no_new_streak >= 5:
                        logger.info("[dom] 5 scrolls with no new items — stopping")
                        break
                else:
                    no_new_streak = 0

                for _ in range(15):
                    await self.page.mouse.wheel(0, 400)
                    await asyncio.sleep(0.1)
                await asyncio.sleep(2.5)

            if not all_listings:
                try:
                    ss_path = self.images_dir / "debug_collect_zero.png"
                    await self.page.screenshot(path=str(ss_path))
                    logger.warning(f"[dom] Zero results — saved debug screenshot to {ss_path}")
                except Exception:
                    pass

        except Exception as e:
            logger.error(f"[dom] _collect_from_browser_dom failed: {e}")
        finally:
            self.page.remove_listener("response", _on_resp)

        logger.info(f"[dom] collected {len(all_listings)} listings (target={target_count})")
        return all_listings[:target_count]

    async def _collect_listings_robust(
        self, city: str, category: str, target_count: int
    ) -> List[Dict[str, Any]]:
        """Primary collection method: browser DOM extraction with direct API supplement.

        Strategy 1 — browser DOM: navigate the listing page, switch to list view,
        scroll while reading live DOM links + intercepting API responses.
        Strategy 2 — direct API: httpx GET to api.divar.ir with cursor pagination.
        """
        all_listings: List[Dict[str, Any]] = []
        seen_ids: set = set()

        # Strategy 1: live DOM extraction (independent of API response format)
        try:
            dom_listings = await self._collect_from_browser_dom(city, category, target_count)
            for lst in dom_listings:
                if lst['divar_id'] not in seen_ids:
                    seen_ids.add(lst['divar_id'])
                    all_listings.append(lst)
            logger.info(f"[robust] DOM strategy: {len(all_listings)}/{target_count}")
        except Exception as e:
            logger.error(f"[robust] DOM strategy failed: {e}")

        if len(all_listings) >= target_count:
            return all_listings[:target_count]

        # Strategy 2: direct API with cursor pagination
        remaining = target_count - len(all_listings)
        last_post_date: Optional[int] = None
        consecutive_empty = 0
        for page_num in range(1, max(8, (remaining // 20) + 3) + 1):
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
                    f"new={new_count} total={len(all_listings)}/{target_count}"
                )
                if lpd:
                    last_post_date = lpd
                if len(all_listings) >= target_count:
                    break
                if not batch:
                    consecutive_empty += 1
                    if consecutive_empty >= 2:
                        break
                else:
                    consecutive_empty = 0
                await asyncio.sleep(random.uniform(0.8, 1.5))
            except Exception as e:
                logger.error(f"[robust] API page={page_num} failed: {e}")
                break

        return all_listings[:target_count]

    async def _collect_listings_scroll(
        self, city: str, category: str, target_count: int
    ) -> List[Dict[str, Any]]:
        """Collect listings by scrolling Divar's infinite-scroll listing page.

        Divar loads more items as the user scrolls down (IntersectionObserver /
        infinite scroll), NOT via page URL changes.  Each scroll-to-bottom
        triggers a /postlist/w/search API call that returns the next batch.

        Returns up to target_count unique listings.
        """
        all_listings: List[Dict[str, Any]] = []
        seen_ids: set = set()
        pending: list = []

        async def _on_resp(response):
            try:
                if 'api.divar.ir' not in response.url or response.status != 200:
                    return
                if 'json' not in response.headers.get('content-type', ''):
                    return
                is_search = (
                    (city in response.url and category in response.url)
                    or '/postlist/w/search' in response.url
                    or '/v8/web-search' in response.url
                )
                if is_search:
                    data = await response.json()
                    pending.append(data)
                    top_keys = list(data.keys()) if isinstance(data, dict) else type(data).__name__
                    logger.info(f"[scroll] captured: {response.url} | top_keys={top_keys}")
            except Exception as e:
                logger.debug(f"[scroll] _on_resp error: {e}")

        self.page.on("response", _on_resp)
        try:
            url = f"{self.BASE_URL}/s/{city}/{category}"
            logger.info(f"Scroll-collect {url} | target={target_count}")
            await self._check_rate_limit()
            await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3)

            # ── Switch to list view if Divar loaded map view ──────────────────
            # Divar's new default UI shows a map.  Try clicking the list-view
            # toggle so we get a scrollable card list instead.
            switched = False
            for sel in [
                'button[data-testid="list-tab"]',
                'button:has-text("لیست")',
                '[aria-label="لیست"]',
                'button:has-text("list")',
                '.kt-action-header__button:has-text("لیست")',
            ]:
                try:
                    btn = await self.page.query_selector(sel)
                    if btn and await btn.is_visible():
                        await btn.click()
                        await asyncio.sleep(2)
                        logger.info(f"[scroll] switched to list view via '{sel}'")
                        switched = True
                        break
                except Exception:
                    pass

            if not switched:
                # Try JS-based search for any button containing "لیست"
                try:
                    clicked = await self.page.evaluate("""() => {
                        const btns = [...document.querySelectorAll('button')];
                        const t = btns.find(b => b.innerText.trim().includes('لیست'));
                        if (t) { t.click(); return true; }
                        return false;
                    }""")
                    if clicked:
                        await asyncio.sleep(2)
                        logger.info("[scroll] switched to list view via JS button search")
                except Exception:
                    pass

            try:
                await self.page.wait_for_selector('a[href*="/v/"]', timeout=15000)
            except Exception:
                logger.warning("[scroll] listing cards not found after list-view switch")

            # Move mouse to page centre so wheel events are received by the page
            vp = self.page.viewport_size or {"width": 1280, "height": 720}
            await self.page.mouse.move(vp["width"] // 2, vp["height"] // 2)

            no_new_streak = 0
            max_scrolls = max(30, target_count // 5)

            for scroll_n in range(max_scrolls):
                # Drain any newly captured API responses
                prev = len(all_listings)
                for data in list(pending):
                    pending.remove(data)
                    parsed, _ = self._parse_api_response(data)
                    for lst in parsed:
                        if lst['divar_id'] not in seen_ids:
                            seen_ids.add(lst['divar_id'])
                            all_listings.append(lst)

                gained = len(all_listings) - prev
                logger.info(
                    f"[scroll #{scroll_n}] +{gained} items | total {len(all_listings)}/{target_count}"
                )

                if len(all_listings) >= target_count:
                    break

                if gained == 0:
                    no_new_streak += 1
                    if no_new_streak >= 4:
                        logger.info("[scroll] 4 scrolls with no new items — stopping")
                        break
                else:
                    no_new_streak = 0

                # ── Scroll down using real mouse-wheel events ─────────────────
                # window.scrollTo() does NOT fire scroll/wheel events that
                # IntersectionObserver relies on.  page.mouse.wheel() sends
                # actual WheelEvent + scroll events, triggering Divar's loader.
                for _ in range(20):
                    await self.page.mouse.wheel(0, 400)
                    await asyncio.sleep(0.08)
                # Wait for the network request to be made and responded
                await asyncio.sleep(3)

            # Final drain
            for data in list(pending):
                parsed, _ = self._parse_api_response(data)
                for lst in parsed:
                    if lst['divar_id'] not in seen_ids:
                        seen_ids.add(lst['divar_id'])
                        all_listings.append(lst)

            # HTML fallback if API capture yielded nothing
            if not all_listings:
                logger.warning("[scroll] API capture empty — falling back to HTML")
                content = await self.page.content()
                soup = BeautifulSoup(content, 'lxml')
                for card in (
                    soup.select('a.kt-post-card__action')
                    or soup.select('a[href*="/v/"]')
                ):
                    lst = self._parse_listing_card(card)
                    if lst and lst['divar_id'] not in seen_ids:
                        seen_ids.add(lst['divar_id'])
                        all_listings.append(lst)

        except Exception as e:
            logger.error(f"_collect_listings_scroll failed: {e}")
        finally:
            self.page.remove_listener("response", _on_resp)

        logger.info(f"[scroll] collected {len(all_listings)} listings (target was {target_count})")
        return all_listings[:target_count]


    def _parse_api_response(self, data: dict) -> tuple:
        """Parse Divar API JSON response (handles multiple known response shapes).

        Returns (listings, last_post_date) where last_post_date is an int Unix
        timestamp used as the cursor for the next page, or None if unavailable.
        """
        listings: List[Dict[str, Any]] = []
        last_post_date: Optional[int] = None

        if not isinstance(data, dict):
            return listings, last_post_date

        # Extract pagination cursor — Divar uses last_post_date as cursor
        raw_lpd = (
            data.get('last_post_date')
            or data.get('pagination', {}).get('last_post_date')
            or data.get('meta', {}).get('last_post_date')
        )
        if raw_lpd:
            try:
                last_post_date = int(raw_lpd)
            except (TypeError, ValueError):
                pass

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
                if not token:
                    continue

                # Track the sort/post date of this widget to use as last_post_date cursor
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
    
    def _parse_listing_card(self, card) -> Optional[Dict[str, Any]]:
        """Parse a listing card element"""
        try:
            href = card.get('href', '')
            if not href or '/v/' not in href:
                return None
            
            url = urljoin(self.BASE_URL, href)
            divar_id = self._extract_divar_id(url)
            
            # Extract basic info - try multiple selectors
            title_elem = card.select_one('.kt-post-card__title, .post-title, h2, h3')
            title = title_elem.get_text(strip=True) if title_elem else None
            
            # Extract descriptions (price, rooms, area)
            descriptions = card.select('.kt-post-card__description, .post-description, span.description')
            desc_texts = [d.get_text(strip=True) for d in descriptions]
            
            # Extract thumbnail
            img_elem = card.select_one('.kt-image-block__image, img')
            thumbnail_url = img_elem.get('src') or img_elem.get('data-src') if img_elem else None
            
            # Extract bottom info
            bottom_desc = card.select_one('.kt-post-card__bottom-description, .post-location')
            category_hint = bottom_desc.get_text(strip=True) if bottom_desc else None
            
            return {
                "url": url,
                "divar_id": divar_id,
                "title": title,
                "descriptions": desc_texts,
                "thumbnail_url": thumbnail_url,
                "category_hint": category_hint
            }
            
        except Exception as e:
            logger.warning(f"Failed to parse card: {e}")
            return None
    
    async def scrape_property_detail(
        self, url: str, target_category: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Scrape detailed information from a property page.

        target_category: if provided, the final URL must match the expected
        patterns for that category (prevents off-category listings from being saved).
        """
        try:
            logger.info(f"Scraping property detail: {url}")

            await self._check_rate_limit()
            await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)

            # If Divar redirected to a CAPTCHA or home page, skip this property
            actual_url = self.page.url
            if '/v/' not in actual_url:
                logger.warning(f"Detail page redirected away from property: {url} → {actual_url}, skipping")
                return None

            from urllib.parse import unquote
            decoded_url = unquote(actual_url)

            # ── Category-specific URL check (tight) ──────────────────────────
            # When a target category is known, we require the redirected URL to
            # contain at least one of the expected substrings for that category.
            # This blocks job ads, factory listings, etc. that share keywords
            # with real-estate (e.g. "دفتری" matching "دفتر").
            if target_category and target_category in self.CATEGORY_URL_PATTERNS:
                patterns = self.CATEGORY_URL_PATTERNS[target_category]
                if not any(p in decoded_url for p in patterns):
                    logger.info(
                        f"Skipping off-category listing for '{target_category}' "
                        f"(URL: {decoded_url})"
                    )
                    return False  # sentinel: category skip — not a scrape error
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
                    return None

            await asyncio.sleep(1.5)
            # Wait for property specs to be rendered by React
            try:
                await self.page.wait_for_selector(
                    '.kt-group-row-item, .kt-unexpandable-row, .kt-base-row',
                    timeout=8000
                )
            except Exception:
                pass
            try:
                await self.page.wait_for_selector(
                    '[class*="description-row__text"], .kt-description-row',
                    timeout=3000
                )
            except Exception:
                pass
            await self._simulate_scroll()
            await asyncio.sleep(0.5)

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
            
            # Extract images (use Playwright JS to capture all gallery slides)
            property_data["images"] = await self._extract_images_from_page()
            if not property_data["images"]:
                property_data["images"] = self._extract_images(soup)
            
            # Set has_images flag if images were found
            if property_data.get("images"):
                property_data["has_images"] = True
            
            # Extract advertiser type and posting time
            advertiser_type = await self._extract_advertiser_type()
            if advertiser_type:
                property_data["advertiser_type"] = advertiser_type

            posted_at = await self._extract_posted_at()
            if posted_at:
                property_data["posted_at"] = posted_at

            # Get phone number (requires login)
            _otp_key = f"{self.current_job.job_id}:{property_data.get('divar_id','')}" if self.current_job else None
            contact_extractor = ContactExtractor(self.page, self.images_dir, otp_key=_otp_key)
            phone_number = await contact_extractor.get_phone_number()
            if phone_number:
                property_data["phone_number"] = phone_number

            return property_data
            
        except Exception as e:
            logger.error(f"Failed to scrape property detail: {e}")
            return None
    
    def _extract_price_info(self, soup) -> Dict[str, Any]:
        """Extract price information from property page"""
        price_info = {}
        
        try:
            # Look for price rows - multiple selectors for different Divar layouts
            rows = soup.select('.kt-base-row, .kt-unexpandable-row')
            
            for row in rows:
                # Try multiple selector combinations
                title = row.select_one('.kt-base-row__title, .kt-unexpandable-row__title, .kt-group-row-item__title')
                value = row.select_one('.kt-unexpandable-row__value, .kt-base-row__end, .kt-group-row-item__value')
                
                if not title or not value:
                    continue
                
                title_text = title.get_text(strip=True)
                value_text = value.get_text(strip=True)
                
                # Extract prices with more specific matching
                if 'قیمت کل' in title_text:
                    price_info['total_price'] = self._parse_persian_number(value_text)
                elif 'قیمت هر متر' in title_text:
                    price_info['price_per_meter'] = self._parse_persian_number(value_text)
                elif 'قیمت' in title_text and 'متر' not in title_text and 'total_price' not in price_info:
                    # Generic price field (for buy properties)
                    price_info['total_price'] = self._parse_persian_number(value_text)
                elif 'اجاره' in title_text or 'اجارهٔ ماهانه' in title_text or 'اجاره‌بها' in title_text:
                    price_info['rent_price'] = self._parse_persian_number(value_text)
                elif 'ودیعه' in title_text or 'رهن' in title_text or 'پیش پرداخت' in title_text:
                    price_info['deposit'] = self._parse_persian_number(value_text)
            
            # Set default price field for compatibility
            if 'total_price' in price_info and 'price' not in price_info:
                price_info['price'] = price_info['total_price']
            elif 'rent_price' in price_info and 'price' not in price_info:
                price_info['price'] = price_info['rent_price']
            
        except Exception as e:
            logger.warning(f"Failed to extract price info: {e}")
        
        return price_info
    
    def _extract_property_details(self, soup) -> Dict[str, Any]:
        """Extract property details like area, rooms, floor, etc."""
        details = {}

        def _is_negated(v: str) -> bool:
            """Return True if value explicitly says the feature is absent."""
            return any(neg in v for neg in ('ندارد', 'خیر', 'نه', 'بدون'))

        def _apply(title_text: str, value_text: str):
            """Map a single title/value pair onto details dict.

            For boolean amenities: presence of the title alone means True
            (Divar shows chips without a value when the feature exists).
            We only set False if the value *explicitly* negates it.
            """
            t = title_text.strip()
            v = value_text.strip()
            if not t:
                return
            if 'متراژ زمین' in t or 'مساحت زمین' in t:
                if v: details.setdefault('land_area', self._parse_persian_number(v))
            elif 'متراژ' in t or 'مساحت' in t or 'زیربنا' in t:
                if v:
                    key = 'land_area' if 'زمین' in t else ('built_area' if 'زیربنا' in t else 'area')
                    details.setdefault(key, self._parse_persian_number(v))
            elif 'اتاق' in t or 'خواب' in t:
                if v:
                    rooms = 0 if ('بدون اتاق' in v or 'بدون خواب' in v) else self._parse_persian_number(v)
                    details.setdefault('rooms', rooms)
            elif 'سال ساخت' in t or 'سن بنا' in t:
                if v:
                    details.setdefault('year_built', self._parse_persian_number(v))
                    details.setdefault('building_age', v)
            elif 'طبقه' in t:
                if v:
                    if 'از' in v:
                        parts = v.split('از')
                        details.setdefault('floor', self._parse_persian_number(parts[0]))
                        details.setdefault('total_floors', self._parse_persian_number(parts[1]))
                    else:
                        details.setdefault('floor', self._parse_persian_number(v))
            elif 'آسانسور' in t:
                # chip without value = has it; only False when explicitly negated
                details['has_elevator'] = not _is_negated(v)
            elif 'پارکینگ' in t:
                details['has_parking'] = not _is_negated(v)
            elif 'انباری' in t:
                details['has_storage'] = not _is_negated(v)
            elif 'بالکن' in t or 'تراس' in t:
                details['has_balcony'] = not _is_negated(v)
            elif 'جهت' in t:
                if v: details.setdefault('building_direction', v)
            elif 'وضعیت' in t:
                if v: details.setdefault('unit_status', v)
            elif 'سند' in t:
                if v: details.setdefault('document_type', v)
            elif 'نوع کاربری' in t:
                if v: details.setdefault('usage_type', v)
            elif 'نوع ملک' in t:
                if v: details.setdefault('property_type', v)

        try:
            # ── 1. Compact table: each .kt-group-row-item holds title + optional value ──
            for cell in soup.select('.kt-group-row-item'):
                title_el = cell.select_one('.kt-group-row-item__title')
                value_el = cell.select_one('.kt-group-row-item__value')
                if title_el:
                    # Pass empty string when no value (chip-only = feature present)
                    _apply(title_el.get_text(strip=True),
                           value_el.get_text(strip=True) if value_el else '')

            # ── 2. Expandable/base rows ──
            for row in soup.select('.kt-base-row, .kt-unexpandable-row'):
                title_el = row.select_one(
                    '.kt-base-row__title, .kt-unexpandable-row__title, '
                    '[class*="row__title"]'
                )
                value_el = row.select_one(
                    '.kt-unexpandable-row__value, .kt-base-row__end, '
                    '[class*="row__value"], [class*="row__end"]'
                )
                if title_el:
                    _apply(title_el.get_text(strip=True),
                           value_el.get_text(strip=True) if value_el else '')

            # ── 3. Standalone feature chips (e.g. آسانسور / پارکینگ listed without row) ──
            CHIP_MAP = {
                'آسانسور': 'has_elevator',
                'پارکینگ': 'has_parking',
                'انباری': 'has_storage',
                'بالکن': 'has_balcony',
                'تراس': 'has_balcony',
            }
            for elem in soup.select(
                '.kt-icon-row-item, .kt-amenity-feat-cell, '
                '[class*="feat-cell"], [class*="amenity-item"], '
                '[class*="feature-item"]'
            ):
                text = elem.get_text(strip=True)
                for keyword, field in CHIP_MAP.items():
                    if keyword in text and not _is_negated(text):
                        details[field] = True

        except Exception as e:
            logger.warning(f"Failed to extract property details: {e}")

        # ── 3. Fallback: parse area and rooms from title text ──
        if 'area' not in details or 'rooms' not in details:
            try:
                title_el = soup.select_one('h1')
                if title_el:
                    title_txt = title_el.get_text()
                    if 'area' not in details:
                        m = re.search(r'(\d[\d,]*)\s*متر', title_txt)
                        if m:
                            details['area'] = self._parse_persian_number(m.group(1))
                    if 'rooms' not in details:
                        m = re.search(r'(\d)\s*خواب', title_txt)
                        if m:
                            details['rooms'] = int(m.group(1))
            except Exception:
                pass

        return details
    
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
        'جهت', 'جهت ساختمان',
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
        cell values — those are already captured by _extract_property_details.
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
    
    async def _get_phone_number(self) -> Optional[str]:
        """Click contact button and extract phone number"""
        try:
            # Try multiple selectors for contact button
            contact_selectors = [
                '.post-actions__get-contact',  # Most specific first
                'button.kt-button--primary:has-text("اطلاعات تماس")',
                'button:has-text("اطلاعات تماس")',
                'button:has-text("شماره تماس")',
                'button:has-text("تماس")',
                '[data-testid="contact-button"]',
                '.kt-contact-row button',
                'button.kt-button--primary:has-text("تماس")',
            ]
            
            contact_button = None
            for selector in contact_selectors:
                try:
                    contact_button = await self.page.query_selector(selector)
                    if contact_button:
                        is_visible = await contact_button.is_visible()
                        if is_visible:
                            logger.info(f"Found visible contact button with selector: {selector}")
                            break
                        contact_button = None
                except Exception:
                    continue
            
            if contact_button:
                await self._human_like_delay(0.3, 0.8)
                
                # Use force click and scroll into view
                try:
                    # Scroll button into view first
                    await contact_button.scroll_into_view_if_needed()
                    await asyncio.sleep(0.3)
                    
                    # Try regular click with force
                    await contact_button.click(force=True, timeout=5000)
                    logger.info("Contact button clicked successfully with force")
                except Exception as click_err:
                    logger.warning(f"Force click failed, trying dispatchEvent: {click_err}")
                    try:
                        # Try dispatching a click event directly
                        await self.page.evaluate('''(el) => {
                            el.dispatchEvent(new MouseEvent('click', {
                                view: window,
                                bubbles: true,
                                cancelable: true
                            }));
                        }''', contact_button)
                        logger.info("dispatchEvent click executed")
                    except Exception as dispatch_err:
                        logger.warning(f"dispatchEvent also failed: {dispatch_err}")
                
                # Wait for network to settle after click
                try:
                    await self.page.wait_for_load_state('networkidle', timeout=5000)
                except Exception:
                    pass
                
                await asyncio.sleep(2)  # Wait for modal/response to load
                
                # Save screenshot for debugging
                try:
                    debug_screenshot = self.images_dir / "debug_after_click.png"
                    await self.page.screenshot(path=str(debug_screenshot))
                    logger.info(f"Debug screenshot saved to {debug_screenshot}")
                except Exception:
                    pass
                
                # Log current page content for debugging
                try:
                    page_content = await self.page.content()
                    if 'tel:' in page_content:
                        logger.info("Phone number link found in page content")
                    else:
                        logger.info("No tel: link found in page content after click")
                        # Check for modal or overlay
                        if 'kt-new-modal' in page_content or 'kt-modal' in page_content:
                            logger.info("Modal detected on page")
                        # Check for any 09 phone patterns (Persian or English)
                        import re
                        phone_patterns = re.findall(r'[۰-۹0-9]{10,11}', page_content)
                        if phone_patterns:
                            logger.info(f"Found phone-like patterns: {phone_patterns[:5]}")
                except Exception:
                    pass
                
                # Try multiple selectors for phone number - expanded list
                phone_selectors = [
                    'a[href^="tel:"]',
                    '.kt-unexpandable-row__action a[href^="tel:"]',
                    '[data-testid="phone-number"]',
                    '.kt-base-row a[href^="tel:"]',
                    'a.kt-unexpandable-row__action-btn',
                    '.post-actions__phone a',
                    'a[class*="phone"]',
                    # Modal-based selectors
                    '.kt-new-modal a[href^="tel:"]',
                    '.kt-modal a[href^="tel:"]',
                    '.kt-dimmer a[href^="tel:"]',
                    '[role="dialog"] a[href^="tel:"]',
                    # Text-based selectors
                    'span:has-text("09")',
                    'p:has-text("09")',
                    'div:has-text("۰۹")',
                ]
                
                phone_found = False
                for selector in phone_selectors:
                    try:
                        phone_elem = await self.page.wait_for_selector(selector, timeout=2000)
                        if phone_elem:
                            logger.info(f"Found phone element with selector: {selector}")
                            # Get href attribute for cleaner phone extraction
                            href = await phone_elem.get_attribute('href')
                            if href and href.startswith('tel:'):
                                phone_text = href.replace('tel:', '').strip()
                            else:
                                phone_text = await phone_elem.inner_text()
                            
                            logger.info(f"Raw phone text: {phone_text}")
                            
                            # Convert Persian numbers and validate as phone
                            phone = self._parse_persian_number(phone_text)
                            if phone:
                                phone_str = str(phone)
                                if len(phone_str) == 10 and phone_str.startswith('9'):
                                    logger.info(f"Extracted phone number: 0{phone_str}")
                                    return f"0{phone_str}"
                                elif len(phone_str) == 11 and phone_str.startswith('09'):
                                    logger.info(f"Extracted phone number: {phone_str}")
                                    return phone_str
                            phone_found = True
                            break
                    except Exception as e:
                        continue
                
                # Last resort: try to extract phone from page content using regex
                if not phone_found:
                    try:
                        page_content = await self.page.content()
                        import re
                        # Look for Persian phone numbers (۰۹ pattern)
                        persian_pattern = r'[۰۹]{2}[۰-۹]{9}'
                        english_pattern = r'0?9[0-9]{9}'
                        
                        matches = re.findall(persian_pattern, page_content)
                        if matches:
                            phone = self._parse_persian_number(matches[0])
                            if phone:
                                logger.info(f"Extracted phone from regex (Persian): {phone}")
                                return f"0{phone}" if not str(phone).startswith('0') else str(phone)
                        
                        matches = re.findall(english_pattern, page_content)
                        if matches:
                            phone = matches[0]
                            if not phone.startswith('0'):
                                phone = '0' + phone
                            logger.info(f"Extracted phone from regex (English): {phone}")
                            return phone
                    except Exception as regex_err:
                        logger.warning(f"Regex extraction failed: {regex_err}")
                
                if not phone_found:
                    logger.warning("No phone element found after clicking contact button")
            else:
                logger.warning("No contact button found on page - phone cannot be extracted")
            
            return None
            
        except Exception as e:
            logger.warning(f"Failed to get phone number: {e}")
            return None
    
    async def _extract_advertiser_type(self) -> Optional[str]:
        """Detect whether the seller is personal (شخصی) or an agency (مشاور)."""
        try:
            result = await self.page.evaluate("""() => {
                // Check dedicated advertiser-type rows first
                const rows = document.querySelectorAll('.kt-base-row, .kt-unexpandable-row');
                for (const row of rows) {
                    const title = row.querySelector('[class*="__title"]');
                    const value = row.querySelector('[class*="__value"], [class*="__end"]');
                    if (!title) continue;
                    const tt = title.innerText || '';
                    if (tt.includes('آگهی') || tt.includes('فروشنده') || tt.includes('نوع')) {
                        const vt = value ? value.innerText : '';
                        if (vt.includes('مشاور') || vt.includes('آژانس') || vt.includes('بنگاه'))
                            return 'agency';
                        if (vt.includes('شخصی'))
                            return 'personal';
                    }
                }
                // Fallback: look for seller badge / chip near contact section
                const contact = document.querySelector(
                    '[class*="contact"], [class*="seller"], [class*="advertiser"]'
                );
                if (contact) {
                    const ct = contact.innerText || '';
                    if (ct.includes('مشاور') || ct.includes('آژانس') || ct.includes('بنگاه'))
                        return 'agency';
                    if (ct.includes('شخصی'))
                        return 'personal';
                }
                return null;
            }""")
            return result
        except Exception as e:
            logger.debug(f"Could not extract advertiser type: {e}")
            return None

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

    async def download_images(
        self,
        images: List[str],
        divar_id: str
    ) -> List[str]:
        """Download images and return local paths"""
        local_paths = []
        
        try:
            property_dir = self.images_dir / divar_id
            property_dir.mkdir(parents=True, exist_ok=True)
            
            async with httpx.AsyncClient() as client:
                for i, url in enumerate(images):
                    try:
                        response = await client.get(url, timeout=30)
                        if response.status_code == 200:
                            # Generate filename
                            ext = 'webp' if 'webp' in url else 'jpg'
                            filename = f"img_{i+1}.{ext}"
                            filepath = property_dir / filename
                            
                            with open(filepath, 'wb') as f:
                                f.write(response.content)
                            
                            local_paths.append(str(filepath))
                            logger.debug(f"Downloaded image: {filename}")
                            
                            await asyncio.sleep(0.5)  # Rate limit downloads
                    except Exception as e:
                        logger.warning(f"Failed to download image {i+1}: {e}")
            
        except Exception as e:
            logger.error(f"Failed to download images: {e}")
        
        return local_paths
    
    async def property_exists(self, divar_id: str) -> bool:
        """Check if property already exists in database"""
        try:
            result = await self.db_session.execute(
                select(Property).where(Property.divar_id == divar_id)
            )
            return result.scalar_one_or_none() is not None
        except Exception as e:
            logger.error(f"Failed to check property existence: {e}")
            return False
    
    async def save_property(self, property_data: Dict[str, Any]) -> Optional[Property]:
        """Save property to database"""
        try:
            divar_id = property_data.get('divar_id')
            
            # Validate required fields
            if not divar_id:
                logger.warning("Cannot save property: missing divar_id")
                return None
            
            if not property_data.get('title'):
                logger.warning(f"Cannot save property {divar_id}: missing title")
                return None
            
            if not property_data.get('url'):
                logger.warning(f"Cannot save property {divar_id}: missing url")
                return None
            
            # Check if exists
            result = await self.db_session.execute(
                select(Property).where(Property.divar_id == divar_id)
            )
            existing = result.scalar_one_or_none()
            
            if existing:
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
    ) -> ScrapingJob:
        """Start a complete scraping job for a city and category"""
        
        # Get or create job record
        if job_id:
            # Use existing job
            result = await self.db_session.execute(
                select(ScrapingJob).where(ScrapingJob.job_id == job_id)
            )
            job = result.scalar_one_or_none()
            if not job:
                raise ValueError(f"Job {job_id} not found")
            job.status = "running"
            job.started_at = datetime.now()
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
                'max_age_hours': max_age_hours,
            }.items() if v is not None}
            logger.info(f"Starting scraping job for {city}/{category} | filters={active_filters}")

            # ── Collect listings ────────────────────────────────────────────────
            logger.info(f"Target: {max_items} listings")

            all_listings = await self._collect_listings_robust(city, category, max_items)
            seen_ids: set = {lst['divar_id'] for lst in all_listings}

            job.total_items = len(all_listings)
            await self.db_session.commit()

            logger.info(f"Found {len(all_listings)} total listings (target was {max_items})")
            
            # Scrape each property detail
            for i, listing in enumerate(all_listings):
                try:
                    # Check if job was cancelled
                    await self.db_session.refresh(job)
                    if job.status == "cancelled":
                        logger.info(f"Job {job.job_id} was cancelled, stopping scraping")
                        return job
                    
                    # Check if already scraped
                    if await self.property_exists(listing['divar_id']):
                        logger.info(f"Property already exists: {listing['divar_id']}")
                        job.updated_items += 1
                        await self.db_session.commit()
                        continue
                    
                    # Scrape detail page
                    detail = await self.scrape_property_detail(listing['url'], target_category=category)
                    
                    if detail:
                        # Merge with listing data
                        property_data = {**listing, **detail}
                        property_data['city_name'] = CITIES.get(city, {}).get('name', city)
                        property_data['category_name'] = CATEGORIES.get(category, {}).get('name', category)
                        listing_type = CATEGORIES.get(category, {}).get('type', 'unknown')
                        property_data['listing_type'] = listing_type

                        did = listing['divar_id']

                        def _skip(reason: str) -> bool:
                            logger.info(f"Skipping {did}: {reason}")
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
                        if not skip and advertiser_type:
                            actual_type = detail.get('advertiser_type')
                            if actual_type and actual_type != advertiser_type:
                                skip = _skip(f"advertiser_type {actual_type} != {advertiser_type}")

                        # ── Age filter ─────────────────────────────────────────────
                        if not skip and max_age_hours:
                            posted = detail.get('posted_at')
                            if posted and posted < datetime.now() - timedelta(hours=max_age_hours):
                                skip = _skip(f"posted_at {posted} older than {max_age_hours}h")

                        if skip:
                            job.scraped_items = i + 1
                            await self.db_session.commit()
                            continue

                        # Download images if enabled
                        if download_images and property_data.get('images'):
                            local_images = await self.download_images(
                                property_data['images'],
                                property_data['divar_id']
                            )
                            if local_images:
                                property_data['images_downloaded'] = True
                        
                        # Save to database
                        saved = await self.save_property(property_data)
                        if saved:
                            job.new_items += 1
                        else:
                            # save_property rolled back the session; refresh job to avoid lazy-load errors
                            await self.db_session.refresh(job)
                            job.failed_items += 1
                    elif detail is None:
                        # None = real scrape error (network failure, parse error, etc.)
                        job.failed_items += 1
                    # detail is False = off-category skip; don't count as failure

                    job.scraped_items = i + 1
                    await self.db_session.commit()
                    
                    await self._human_like_delay()
                    
                except Exception as e:
                    logger.error(f"Failed to process listing: {e}")
                    job.failed_items += 1
                    try:
                        await self.db_session.rollback()
                        await self.db_session.commit()
                    except Exception:
                        pass
            
            # Complete job
            job.status = "completed"
            job.completed_at = datetime.now()
            await self.db_session.commit()
            
            logger.info(f"Scraping job completed. New: {job.new_items}, Updated: {job.updated_items}, Failed: {job.failed_items}")
            
        except Exception as e:
            job.status = "failed"
            job.error_message = str(e)
            job.completed_at = datetime.now()
            await self.db_session.commit()
            logger.error(f"Scraping job failed: {e}")
        
        return job
    
    async def scrape_all_categories(
        self,
        city: str,
        categories: List[str] = None,
        max_pages: int = 10,
        download_images: bool = True
    ) -> List[ScrapingJob]:
        """Scrape all categories for a city"""
        
        if categories is None:
            categories = list(CATEGORIES.keys())
        
        jobs = []
        
        for category in categories:
            try:
                job = await self.start_scraping_job(
                    city=city,
                    category=category,
                    max_pages=max_pages,
                    download_images=download_images
                )
                jobs.append(job)
                
                # Longer delay between categories
                await asyncio.sleep(random.uniform(10, 20))
                
            except Exception as e:
                logger.error(f"Failed to scrape category {category}: {e}")
        
        return jobs
