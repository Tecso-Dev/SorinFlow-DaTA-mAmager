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
        page_num: int = 1
    ) -> List[Dict[str, Any]]:
        """Scrape a listing page to get property cards"""
        listings = []
        
        try:
            url = f"{self.BASE_URL}/s/{city}/{category}"
            if page_num > 1:
                url += f"?page={page_num}"
            
            logger.info(f"Scraping listing page: {url}")
            
            await self._check_rate_limit()
            
            # Use domcontentloaded for faster loading, then wait for content
            await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2)  # Wait for JS to render
            await self._simulate_scroll()
            await asyncio.sleep(1.0)  # Wait after scroll

            # Wait for listings to load
            try:
                await self.page.wait_for_selector('a[href*="/v/"]', timeout=20000)
            except Exception:
                logger.warning("Primary selector not found, waiting more...")
                await asyncio.sleep(5)
            
            # Get page content
            content = await self.page.content()
            soup = BeautifulSoup(content, 'lxml')
            
            # Find all property cards - try multiple selectors
            cards = soup.select('a.kt-post-card__action')
            if not cards:
                cards = soup.select('div.post-card-item a')
            if not cards:
                cards = soup.select('article a[href*="/v/"]')
            if not cards:
                # Try finding any links to property pages
                cards = soup.select('a[href*="/v/"]')
            
            for card in cards:
                try:
                    listing = self._parse_listing_card(card)
                    if listing:
                        listings.append(listing)
                except Exception as e:
                    logger.warning(f"Failed to parse listing card: {e}")
            
            logger.info(f"Found {len(listings)} listings on page {page_num}")
            
        except Exception as e:
            logger.error(f"Failed to scrape listing page: {e}")
        
        return listings
    
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
    
    async def scrape_property_detail(self, url: str) -> Optional[Dict[str, Any]]:
        """Scrape detailed information from a property page"""
        try:
            logger.info(f"Scraping property detail: {url}")
            
            await self._check_rate_limit()
            await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
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
        max_pages: int = 10,
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
            
            # Scrape listing pages
            all_listings = []
            for page_num in range(1, max_pages + 1):
                # Check if job was cancelled
                await self.db_session.refresh(job)
                if job.status == "cancelled":
                    logger.info(f"Job {job.job_id} was cancelled, stopping scraping")
                    return job
                
                listings = await self.scrape_listing_page(city, category, page_num)
                
                if not listings:
                    logger.info(f"No more listings found at page {page_num}")
                    break
                
                all_listings.extend(listings)
                job.scraped_pages = page_num
                job.total_items = len(all_listings)
                await self.db_session.commit()
                
                await self._human_like_delay()
            
            logger.info(f"Found {len(all_listings)} total listings")
            
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
                        continue
                    
                    # Scrape detail page
                    detail = await self.scrape_property_detail(listing['url'])
                    
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
                    else:
                        job.failed_items += 1

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
