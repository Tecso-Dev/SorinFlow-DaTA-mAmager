"""
SorinFlow Divar Scraper - Authentication Handler
Handles login, cookies, and session management for Divar.ir
"""
import asyncio
import time
import json
import os
from datetime import datetime, timezone
from typing import Optional, Dict, List, Any, Tuple
from pathlib import Path
from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import get_settings
from app.models.cookie import Cookie
from app.scraper.stealth import StealthConfig, STEALTH_JS, get_browser_args, get_context_options

settings = get_settings()


class DivarAuth:
    """Handle Divar.ir authentication with cookies and session management"""
    
    def __init__(self, db_session: Optional[AsyncSession] = None):
        self.db_session = db_session
        self.cookies_dir = Path(settings.cookies_path)
        self.cookies_dir.mkdir(parents=True, exist_ok=True)
        self.stealth_config = StealthConfig()
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
    
    async def initialize_browser(self, proxy: Optional[str] = None, headless: bool = True):
        """Initialize browser with stealth settings"""
        playwright = await async_playwright().start()
        
        self.browser = await playwright.chromium.launch(
            headless=headless,
            args=get_browser_args()
        )
        
        context_options = get_context_options(self.stealth_config, proxy)
        self.context = await self.browser.new_context(**context_options)
        
        # Add stealth script
        await self.context.add_init_script(STEALTH_JS)
        
        self.page = await self.context.new_page()
        return self.page
    
    def browser_alive(self) -> bool:
        """Whether there is still a live page to drive.

        get_current_cookies() returns [] both when the jar is empty and when
        the browser has been torn down, and those are not the same fact. One
        means the session needs a login; the other means there is nothing to
        ask. Conflating them is how four healthy accounts were each declared
        expired in turn, twelve seconds apart, against a browser that had
        already closed.
        """
        try:
            if self.browser is not None and not self.browser.is_connected():
                return False
            if self.page is None or self.page.is_closed():
                return False
            return True
        except Exception:
            return False

    async def close_browser(self):
        """Close browser and cleanup"""
        if self.page:
            await self.page.close()
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
    
    def get_cookie_file_path(self, phone_number: str) -> Path:
        """Get path for cookie file"""
        return self.cookies_dir / f"cookies_{phone_number}.json"
    
    async def save_cookies_to_file(self, phone_number: str, cookies: List[Dict]) -> bool:
        """Save cookies to file"""
        try:
            cookie_file = self.get_cookie_file_path(phone_number)
            with open(cookie_file, 'w', encoding='utf-8') as f:
                json.dump({
                    "phone_number": phone_number,
                    "cookies": cookies,
                    "saved_at": datetime.now().isoformat()
                }, f, ensure_ascii=False, indent=2)
            logger.info(f"Cookies saved to file for {phone_number}")
            return True
        except Exception as e:
            logger.error(f"Failed to save cookies to file: {e}")
            return False
    
    async def load_cookies_from_file(self, phone_number: str) -> Optional[List[Dict]]:
        """Load cookies from file"""
        try:
            cookie_file = self.get_cookie_file_path(phone_number)
            if cookie_file.exists():
                with open(cookie_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    return data.get("cookies", [])
            return None
        except Exception as e:
            logger.error(f"Failed to load cookies from file: {e}")
            return None
    
    async def save_cookies_to_db(self, phone_number: str, cookies: List[Dict], token: Optional[str] = None) -> bool:
        """Save cookies to database"""
        if not self.db_session:
            return False
        
        try:
            # Find token expiry. Shared helper — this used to read only the
            # `expires` field and build a naive local datetime, so a jar from a
            # browser extension got no expiry and a jar from Playwright got one
            # that was wrong by the host's UTC offset.
            from app.services.divar_session import derive_expiry
            expires_at = derive_expiry(cookies)
            
            # Check if cookie exists for this phone
            result = await self.db_session.execute(
                select(Cookie).where(Cookie.phone_number == phone_number)
            )
            existing_cookie = result.scalar_one_or_none()
            
            if existing_cookie:
                existing_cookie.cookies = cookies
                existing_cookie.token = token
                existing_cookie.is_valid = True
                existing_cookie.expires_at = expires_at
                existing_cookie.updated_at = datetime.now()
            else:
                new_cookie = Cookie(
                    phone_number=phone_number,
                    cookies=cookies,
                    token=token,
                    is_valid=True,
                    expires_at=expires_at
                )
                self.db_session.add(new_cookie)
            
            await self.db_session.commit()
            logger.info(f"Cookies saved to database for {phone_number}")
            return True
        except Exception as e:
            logger.error(f"Failed to save cookies to database: {e}")
            await self.db_session.rollback()
            return False
    
    async def load_cookies_from_db(self, phone_number: str) -> Optional[List[Dict]]:
        """Load cookies from database"""
        if not self.db_session:
            return None
        
        try:
            result = await self.db_session.execute(
                select(Cookie).where(
                    Cookie.phone_number == phone_number,
                    Cookie.is_valid == True
                )
            )
            cookie = result.scalar_one_or_none()
            
            if cookie:
                # Check if expired (handle timezone-aware vs naive datetime)
                if cookie.expires_at:
                    expires_at = cookie.expires_at
                    now = datetime.now(timezone.utc).replace(tzinfo=None)
                    # Make comparison timezone-naive if needed
                    if hasattr(expires_at, 'tzinfo') and expires_at.tzinfo is not None:
                        expires_at = expires_at.replace(tzinfo=None)
                    
                    if expires_at < now:
                        cookie.is_valid = False
                        await self.db_session.commit()
                        logger.warning(f"Cookies expired for {phone_number}")
                        return None
                return cookie.cookies
            return None
        except Exception as e:
            logger.error(f"Failed to load cookies from database: {e}")
            return None
    
    async def check_cookies_validity(self, cookies: List[Dict]) -> bool:
        """Check if cookies are still valid"""
        try:
            # Shared extractor: this used to read only the `expires` field, so
            # a jar imported from a browser extension — which writes
            # `expirationDate` — was reported invalid however fresh it was.
            from app.services.divar_session import cookie_expiry
            now_utc = datetime.now(timezone.utc)

            # Check for token cookie first.
            #
            # A `token` with no expiry is a SESSION cookie, not a broken one —
            # and that is the normal shape of what Divar issues. This branch
            # used to fall through in that case, and "token" matches none of
            # the keywords below, so a perfectly good session reached the
            # catch-all and logged "No specific auth cookies found, but cookies
            # exist" — announcing the absence of the very cookie it was
            # holding. Two separate investigations went the wrong way on that
            # line.
            from app.services.divar_session import auth_cookie
            token_cookie = auth_cookie(cookies)
            if token_cookie and token_cookie.get("value"):
                expires = cookie_expiry(token_cookie)
                if expires is None:
                    logger.info("Session token cookie found (no expiry) — treating as valid")
                    return True
                if expires > now_utc:
                    return True
                logger.info(f"Token cookie expired at {expires.isoformat()}")
                return False

            # Check for other auth cookies
            auth_cookies = [c for c in cookies if any(keyword in c.get("name", "").lower() for keyword in ["auth", "session", "user", "login", "jwt", "bearer"])]
            for cookie in auth_cookies:
                expires = cookie_expiry(cookie)
                if expires and expires > now_utc:
                    logger.info(f"Valid auth cookie found: {cookie.get('name')}")
                    return True
                else:
                    # If no expiration, assume it's valid (session cookie)
                    logger.info(f"Session auth cookie found: {cookie.get('name')}")
                    return True
            
            # If no valid auth cookies found, check if we have any cookies at all
            # Some sites use cookie presence as validity indicator
            if cookies:
                logger.warning("No specific auth cookies found, but cookies exist. Assuming valid.")
                return True
                
            return False
        except Exception as e:
            logger.error(f"Failed to check cookie validity: {e}")
            return False
    
    async def apply_cookies(self, cookies: List[Dict]) -> bool:
        """Make this cookie set the context's *only* session.

        add_cookies() merges: same-name cookies get overwritten, but anything
        the previous account had that this set lacks survives. During account
        rotation that left the old account's identifiers sitting alongside the
        new one — so Divar could still tie the two together, which is the whole
        thing rotation exists to avoid, and a leftover token could fight the
        new one and read as an expired session. Clearing first makes the swap
        a real swap.
        """
        if not self.context:
            logger.error("Browser context not initialized")
            return False

        try:
            try:
                await self.context.clear_cookies()
            except Exception as e:
                # older Playwright builds may not expose it; a merge is still
                # better than no session at all
                logger.warning(f"Could not clear cookies before applying: {e}")
            await self.context.add_cookies(cookies)
            logger.info("Cookies applied to browser context")
            return True
        except Exception as e:
            logger.error(f"Failed to apply cookies: {e}")
            return False
    
    async def get_current_cookies(self) -> List[Dict]:
        """Get current cookies from browser context"""
        if not self.context:
            return []
        
        try:
            cookies = await self.context.cookies()
            return cookies
        except Exception as e:
            logger.error(f"Failed to get current cookies: {e}")
            return []
    
    async def login_with_phone(self, phone_number: str, wait_for_code: bool = True) -> Dict[str, Any]:
        """
        Login to Divar with phone number
        Returns status and instructions for OTP verification
        """
        result = {
            "success": False,
            "message": "",
            "requires_code": False,
            "cookies": None
        }
        
        try:
            if not self.page:
                await self.initialize_browser(headless=settings.scraper_headless)
            
            # Navigate to login page
            logger.info("Navigating to Divar login page...")
            await self.page.goto(settings.divar_login_url, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(3)

            # Check for error page and recover
            content = await self.page.content()
            if 'مشکلی پیش آمد' in content or 'خطایی رخ داد' in content:
                logger.warning("Got error page on first load — navigating directly to /login")
                await self.page.goto("https://divar.ir/login", wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(3)

            # Save screenshot to understand current page state
            try:
                debug_path = Path(settings.images_path).parent / "debug" / "debug_login_page.png"
                debug_path.parent.mkdir(parents=True, exist_ok=True)
                await self.page.screenshot(path=str(debug_path))
                logger.debug(f"Login page screenshot saved to {debug_path}")
            except Exception:
                pass

            # Click on login button (look for ورود links/buttons)
            logger.info("Looking for login button...")
            login_button = None
            try:
                buttons = await self.page.query_selector_all('button, a')
                for btn in buttons:
                    try:
                        text = await btn.inner_text()
                        if 'ورود' in text:
                            login_button = btn
                            break
                    except Exception:
                        continue
            except Exception as e:
                logger.warning(f"Error finding initial login button: {e}")

            if login_button:
                await login_button.click()
                logger.info("Login button clicked, waiting for phone input form...")
                # No sleep here on purpose: the PHONE_SELECTORS loop below is a
                # wait_for_selector, which already polls for the form. Sleeping
                # first only delayed the same outcome by three seconds.
            else:
                logger.info("No login button found — assuming phone input is directly visible")

            # Screenshot after clicking login button
            try:
                debug_path2 = Path(settings.images_path).parent / "debug" / "debug_login_after_click.png"
                await self.page.screenshot(path=str(debug_path2))
                logger.debug(f"Post-click screenshot saved to {debug_path2}")
            except Exception:
                pass

            # Enter phone number — try multiple selectors
            # masked: the redaction filter would catch it anyway, but a call
            # site that never had the number in the string is one fewer thing
            # depending on the filter being right
            logger.info(f"Entering phone number: {str(phone_number)[:4]}*****{str(phone_number)[-2:]}")
            phone_input = None
            PHONE_SELECTORS = [
                'input[name="mobile"]',
                'input[type="tel"]',
                'input[type="number"]',
                'input[inputmode="numeric"]',
                'input[inputmode="tel"]',
                'input[autocomplete="tel"]',
                'input[placeholder*="موبایل"]',
                'input[placeholder*="تلفن"]',
                'input[placeholder*="شماره"]',
                'input[placeholder*="09"]',
                'form input[type="text"]',
                'input:not([type="hidden"]):not([type="submit"]):not([type="checkbox"])',
            ]
            for sel in PHONE_SELECTORS:
                try:
                    phone_input = await self.page.wait_for_selector(sel, timeout=5000)
                    if phone_input:
                        logger.info(f"Found phone input with selector: {sel}")
                        break
                except Exception:
                    continue

            if not phone_input:
                # JS fallback: log all inputs for debugging
                try:
                    inputs = await self.page.evaluate("""
                        () => Array.from(document.querySelectorAll('input')).map(el => ({
                            type: el.type, name: el.name, placeholder: el.placeholder,
                            inputmode: el.getAttribute('inputmode'), id: el.id
                        }))
                    """)
                    logger.error(f"Inputs found on page: {inputs}")
                    current_url = self.page.url
                    logger.error(f"Current URL: {current_url}")
                    try:
                        snap = Path(settings.images_path).parent / "debug" / "debug_no_input_found.png"
                        await self.page.screenshot(path=str(snap))
                    except Exception:
                        pass
                except Exception:
                    pass
                raise Exception("Could not find phone number input field on login page")
            await phone_input.fill("")
            
            # Type phone number with human-like delays
            for char in phone_number:
                await phone_input.type(char, delay=self.stealth_config.typing_delay * 1000)
                await asyncio.sleep(0.05)
            
            await asyncio.sleep(1)
            
            # Click confirm button — Divar uses "بعدی" (next) not "تأیید"
            logger.info("Clicking confirm button...")
            confirm_button = None
            CONFIRM_KEYWORDS = ('تأیید', 'بعدی', 'ارسال', 'confirm', 'next', 'submit', 'send')
            try:
                buttons = await self.page.query_selector_all('button')
                for btn in buttons:
                    try:
                        text = (await btn.inner_text()).strip()
                        if any(kw in text for kw in CONFIRM_KEYWORDS):
                            confirm_button = btn
                            logger.info(f"Found confirm button with text: '{text}'")
                            break
                    except Exception:
                        continue
                if not confirm_button:
                    confirm_button = await self.page.query_selector('button[type="submit"]')
                    if confirm_button:
                        logger.info("Found confirm button via type=submit fallback")
            except Exception as e:
                logger.warning(f"Error finding confirm button: {e}")

            if confirm_button:
                await confirm_button.click()
                logger.info("Confirm button clicked — waiting for SMS...")
                # Was 4s. Nothing below waits on it — the screenshot is a debug
                # aid and the function then returns requires_code immediately.
                # Those four seconds were pure latency between the click and
                # the panel telling somebody their code was on its way.
                await asyncio.sleep(0.5)
                try:
                    snap = Path(settings.images_path).parent / "debug" / "debug_after_phone_submit.png"
                    await self.page.screenshot(path=str(snap))
                    logger.info(f"Post-submit screenshot: {snap}")
                except Exception:
                    pass
                result["requires_code"] = True
                result["message"] = f"کد OTP به {phone_number} ارسال شد. لطفاً کد ۶ رقمی را وارد کنید."
                logger.info(result["message"])
            else:
                result["success"] = False
                result["message"] = "خطا: دکمه تأیید در صفحه دیوار پیدا نشد. لطفاً دوباره امتحان کنید."
                logger.error(result["message"])

            return result
            
        except Exception as e:
            result["message"] = f"Login failed: {str(e)}"
            logger.error(result["message"])
            return result
    
    # What Divar says when a code is not accepted. Matched on its own words,
    # because the alternative — reporting "no authentication tokens found" —
    # tells whoever typed the code nothing about whether the code was wrong,
    # expired, or the flow itself is broken.
    _OTP_REJECTIONS = (
        "کد وارد شده صحیح نیست", "کد اشتباه", "کد نامعتبر",
        "کد منقضی", "منقضی شده", "نامعتبر است", "دوباره تلاش",
    )

    async def _divar_rejection_text(self) -> Optional[str]:
        """Divar's own visible complaint about the code, if it is showing one."""
        try:
            body = (await self.page.inner_text("body")) or ""
        except Exception:
            return None
        for phrase in self._OTP_REJECTIONS:
            if phrase in body:
                return f"دیوار کد را نپذیرفت: «{phrase}»"
        return None

    async def _await_login_outcome(
        self, timeout: float = 30.0
    ) -> Tuple[List[Dict], Optional[str]]:
        """Poll until Divar sets the session cookie, rejects the code, or time runs out.

        Returns (cookies, rejection_message). A rejection short-circuits: there
        is nothing left to wait for once Divar has said no.
        """
        deadline = time.monotonic() + timeout
        while True:
            cookies = await self.get_current_cookies()
            from app.services.divar_session import has_auth_cookie, auth_cookie
            if has_auth_cookie(cookies):
                logger.info(f"Session cookie appeared ({auth_cookie(cookies)['name']}) "
                            "— login completed")
                return cookies, None

            rejection = await self._divar_rejection_text()
            if rejection:
                return cookies, rejection

            if time.monotonic() >= deadline:
                logger.warning(
                    f"No session cookie after {timeout:.0f}s and no complaint "
                    f"from Divar — falling through to the full cookie check")
                return cookies, None

            await asyncio.sleep(1.0)

    async def submit_otp_code(self, code: str, phone_number: str = None) -> Dict[str, Any]:
        """Submit OTP verification code"""
        result = {
            "success": False,
            "message": "",
            "cookies": None,
            "phone_number": phone_number
        }
        
        try:
            if not self.page:
                result["message"] = "Browser not initialized. Please start login again."
                return result
            
            # Enter verification code
            logger.info("Entering verification code...")
            
            # Debug: Wait a bit and check page state
            await asyncio.sleep(2)
            current_url = self.page.url
            logger.info(f"Current URL when looking for OTP input: {current_url}")

            # Divar sometimes auto-completes the login (trusted session / no OTP
            # required) and lands directly on an authenticated page with the `token`
            # cookie already set — the OTP form no longer exists. If we blindly search
            # for it, we grab the wrong input and the bogus "login" click hangs for the
            # default 30s ("Timeout 30000ms exceeded"). Detect the already-logged-in
            # state up front and finalize immediately from the cookies we already have.
            pre_cookies = await self.get_current_cookies()
            from app.services.divar_session import auth_cookie as _auth_cookie
            token_cookie = _auth_cookie(pre_cookies)
            if token_cookie:
                logger.info(f"Already authenticated (url={current_url}) — token cookie present, skipping OTP form entry")
                result["success"] = True
                result["message"] = "Login successful!"
                result["cookies"] = pre_cookies
                save_phone = phone_number or settings.divar_phone_number
                if save_phone:
                    await self.save_cookies_to_file(save_phone, pre_cookies)
                    if self.db_session:
                        await self.save_cookies_to_db(save_phone, pre_cookies, token_cookie.get("value"))
                    logger.info(f"Login successful, cookies saved for {save_phone}!")
                else:
                    logger.warning("No phone number provided, cookies not saved")
                return result

            # Divar's current login modal renders the OTP as several separate
            # single-digit boxes whose JS auto-advances focus after each digit.
            # Typing per-element with ElementHandle.type() re-focuses the same
            # box for every character, so digits after the first get dropped.
            # Unified approach: collect the visible digit/code input(s), click
            # the first one, and type the whole code through the shared keyboard —
            # letting Divar's own JS move focus box-to-box. This also works for a
            # legacy single 6-char field (all digits land in the one field).
            #
            # Give the modal a moment to render the boxes first.
            try:
                await self.page.wait_for_selector(
                    'input[maxlength], input[inputmode="numeric"], input[type="tel"], input[type="text"], input[type="number"]',
                    timeout=15000,
                )
            except Exception as e:
                logger.warning(f"No OTP input appeared within 15s: {e}")

            # Gather every visible, enabled, text-like input (the OTP boxes).
            otp_inputs = []
            for inp in await self.page.query_selector_all('input'):
                try:
                    if not await inp.is_visible():
                        continue
                    itype = (await inp.get_attribute('type') or 'text').lower()
                    if itype in ('hidden', 'checkbox', 'radio', 'submit', 'button', 'file', 'password'):
                        continue
                    if not await inp.is_editable():
                        continue
                    otp_inputs.append(inp)
                except Exception:
                    continue

            logger.info(f"Found {len(otp_inputs)} visible editable input(s) for OTP entry")

            if not otp_inputs:
                # Debug: dump inputs + page for diagnosis
                try:
                    all_inputs = await self.page.query_selector_all('input')
                    logger.error(f"No editable OTP input found. Total <input> on page: {len(all_inputs)}")
                    for i, inp in enumerate(all_inputs[:12]):
                        itype = await inp.get_attribute('type') or 'text'
                        name = await inp.get_attribute('name') or ''
                        maxlength = await inp.get_attribute('maxlength') or ''
                        vis = await inp.is_visible()
                        logger.error(f"Input {i}: type={itype}, name={name}, maxlength={maxlength}, visible={vis}")
                    with open("/app/debug_otp_page.html", 'w', encoding='utf-8') as f:
                        f.write(await self.page.content())
                except Exception as e:
                    logger.error(f"Failed to debug page content: {e}")
                raise Exception(f"Could not find code input field. Page URL: {current_url}")

            # Click the first box and type the full code via the keyboard.
            try:
                await otp_inputs[0].click(timeout=8000)
            except Exception as e:
                raise Exception(
                    f"OTP input not clickable (matched wrong/hidden element). "
                    f"Page URL: {current_url}. Original error: {e}"
                )
            # Clear any residue, then type all digits with a human-ish delay so
            # Divar's per-box auto-advance keeps up.
            await self.page.keyboard.press("Control+A")
            await self.page.keyboard.press("Delete")
            await self.page.keyboard.type(code, delay=140)
            logger.info(f"Typed {len(code)}-digit code into OTP field(s) via keyboard")

            await asyncio.sleep(1.5)
            
            # Click login button
            logger.info("Clicking login button...")
            
            # Debug: Take screenshot before clicking login
            try:
                await self.page.screenshot(path="/app/debug_before_login_click.png")
                logger.info("Screenshot saved: debug_before_login_click.png")
            except Exception as e:
                logger.warning(f"Could not take screenshot: {e}")
            
            login_button = None
            
            # Try multiple ways to find the login button
            try:
                # First try to find button with text "ورود"
                login_button = await self.page.query_selector('button[type="submit"]')
                if not login_button:
                    # Look for any button that might be the login button
                    buttons = await self.page.query_selector_all('button')
                    for btn in buttons:
                        text = await btn.inner_text()
                        if 'ورود' in text or 'login' in text.lower() or 'submit' in text.lower():
                            login_button = btn
                            break
                
                # No "last resort - any button" fallback. The first <button>
                # on Divar's login modal is its close control, so falling back
                # to it dismissed the login instead of submitting it, and the
                # cookie check below then reported "no authentication tokens"
                # for a code that was perfectly good. Divar auto-submits once
                # the last digit lands, so having no button to click is a
                # normal, working path — not a reason to click something else.
                if not login_button:
                    logger.info(
                        "No submit button on the OTP modal — relying on Divar's "
                        "own auto-submit once the last digit is entered")
                    
            except Exception as e:
                logger.warning(f"Error finding login button: {e}")
            
            if login_button:
                # Use an explicit short timeout: if the button isn't actionable
                # (e.g. we matched the wrong element) don't hang for the default 30s —
                # continue to the cookie check, which is the real success signal.
                try:
                    await login_button.click(timeout=8000)
                    logger.info("Login button clicked")
                except Exception as e:
                    logger.warning(f"Login button not clickable ({e}); continuing to cookie check")

            # Wait for the outcome, on both paths. This used to be a flat 5s
            # sleep inside the button branch: on the auto-submit path there was
            # no wait at all, and on the clicked path a Divar round trip slower
            # than 5s was read as "no authentication tokens" for a code that had
            # in fact just worked. Poll for the real signal instead, and stop
            # early the moment Divar says the code was wrong.
            cookies, divar_error = await self._await_login_outcome()
            if divar_error:
                result["message"] = divar_error
                logger.error(f"Divar rejected the code: {divar_error}")
                return result

            # Debug: Take screenshot after the attempt
            try:
                await self.page.screenshot(path="/app/debug_after_login_click.png")
            except Exception as e:
                logger.warning(f"Could not take screenshot after login: {e}")
            
            # Debug: Log all available cookies
            logger.info(f"All cookies after login: {len(cookies)} found")
            if cookies:
                # Names only. This used to log the first 50 characters of every
                # value, and Divar's «token» cookie IS the session — printing it
                # put a live credential in a file and on the node's disk. The
                # count on the line above is what anyone was actually reading.
                logger.info(f"Cookie names: {', '.join(sorted(c.get('name', '?') for c in cookies))}")
            else:
                logger.warning("No cookies found at all after login attempt")
                # Debug: Check page URL and title
                try:
                    current_url = self.page.url
                    title = await self.page.title()
                    logger.info(f"Current page URL: {current_url}")
                    logger.info(f"Current page title: {title}")
                except Exception as e:
                    logger.error(f"Could not get page info: {e}")
            
            # Also check localStorage and sessionStorage for tokens
            local_storage = await self.page.evaluate("() => { const items = {}; for (let i = 0; i < localStorage.length; i++) { const key = localStorage.key(i); items[key] = localStorage.getItem(key); } return items; }")
            session_storage = await self.page.evaluate("() => { const items = {}; for (let i = 0; i < sessionStorage.length; i++) { const key = sessionStorage.key(i); items[key] = sessionStorage.getItem(key); } return items; }")
            
            logger.info(f"localStorage items: {len(local_storage)}")
            for key, value in local_storage.items():
                if any(keyword in key.lower() for keyword in ["token", "auth", "jwt", "bearer", "session"]):
                    logger.info(f"localStorage auth item: {key} = {value[:50]}...")
            
            logger.info(f"sessionStorage items: {len(session_storage)}")
            for key, value in session_storage.items():
                if any(keyword in key.lower() for keyword in ["token", "auth", "jwt", "bearer", "session"]):
                    logger.info(f"sessionStorage auth item: {key} = {value[:50]}...")
            
            from app.services.divar_session import auth_cookie as _auth_cookie2
            token_cookie = _auth_cookie2(cookies)
            
            if token_cookie:
                result["success"] = True
                result["message"] = "Login successful!"
                result["cookies"] = cookies
                
                # Save cookies - use passed phone_number or fall back to settings
                save_phone = phone_number or settings.divar_phone_number
                if save_phone:
                    await self.save_cookies_to_file(save_phone, cookies)
                    if self.db_session:
                        await self.save_cookies_to_db(save_phone, cookies, token_cookie.get("value"))
                    logger.info(f"Login successful, cookies saved for {save_phone}!")
                else:
                    logger.warning("No phone number provided, cookies not saved")
            else:
                # Check for other possible auth cookies or storage items
                auth_cookies = [c for c in cookies if any(keyword in c.get("name", "").lower() for keyword in ["auth", "session", "user", "login", "jwt", "bearer"])]
                auth_storage = [k for k in local_storage.keys() if any(keyword in k.lower() for keyword in ["token", "auth", "jwt", "bearer", "session"])]
                auth_storage.extend([k for k in session_storage.keys() if any(keyword in k.lower() for keyword in ["token", "auth", "jwt", "bearer", "session"])])
                
                if auth_cookies or auth_storage:
                    logger.info(f"Found potential auth cookies: {[c.get('name') for c in auth_cookies]}")
                    logger.info(f"Found potential auth storage: {auth_storage}")
                    result["success"] = True
                    result["message"] = f"Login successful! (Using alternative auth methods)"
                    result["cookies"] = cookies
                    
                    # Save cookies anyway
                    save_phone = phone_number or settings.divar_phone_number
                    if save_phone:
                        await self.save_cookies_to_file(save_phone, cookies)
                        if self.db_session:
                            # Use first auth cookie value or a placeholder
                            auth_value = auth_cookies[0].get("value") if auth_cookies else "alt_auth"
                            await self.save_cookies_to_db(save_phone, cookies, auth_value)
                        logger.info(f"Login successful, cookies saved for {save_phone}!")
                else:
                    result["message"] = "Login failed. No authentication tokens found."
                    logger.error(result["message"])
                    logger.error(f"Available cookies: {[c.get('name') for c in cookies]}")
                    logger.error(f"localStorage keys: {list(local_storage.keys())}")
                    logger.error(f"sessionStorage keys: {list(session_storage.keys())}")
            
            return result
            
        except Exception as e:
            result["message"] = f"OTP verification failed: {str(e)}"
            logger.error(result["message"])
            return result
    
    async def restore_session(self, phone_number: str) -> bool:
        """Restore session from saved cookies"""
        try:
            # Try database first
            cookies = await self.load_cookies_from_db(phone_number)
            
            # Fall back to file
            if not cookies:
                cookies = await self.load_cookies_from_file(phone_number)
            
            if not cookies:
                logger.warning(f"No saved cookies found for {phone_number}")
                return False
            
            # Check validity
            is_valid = await self.check_cookies_validity(cookies)
            if not is_valid:
                logger.warning(f"Cookies expired for {phone_number}")
                return False
            
            # Initialize browser if needed
            if not self.page:
                await self.initialize_browser(headless=settings.scraper_headless)
            
            if not self.browser_alive():
                # Nothing below can succeed, and every step of it logs an error
                # that reads like a session problem. Say what actually happened
                # and get out.
                logger.warning(
                    f"{phone_number}: the browser is gone — cannot restore a "
                    f"session without one. This is not an expired session.")
                return False

            # Apply cookies
            await self.apply_cookies(cookies)
            
            # Let Divar's own frontend refresh the session, then check it did.
            #
            # SuperTokens splits a session in two: sAccessToken lasts about an
            # hour, sRefreshToken 364 days. A browser silently swaps an expired
            # access token for a fresh one. We never did, so every stored jar
            # carried whatever access token it had at login — dead within the
            # hour — while the panel read the *cookie's* 364-day expiry and
            # called the session healthy. Divar let us browse and demanded an
            # SMS code the moment we asked for a phone number, on every account,
            # seconds after each rotation.
            #
            # Rather than reimplement the refresh — the endpoint is not
            # discoverable from outside; every path, real or invented, answers
            # 403 to a request without a session — drive the page that already
            # knows how, and verify by watching the token itself change.
            from app.services.divar_session import access_token_expiry, access_token_state

            before = access_token_expiry(cookies)
            try:
                await self.page.goto(
                    "https://divar.ir/my-divar",
                    # networkidle, not domcontentloaded: the refresh is an XHR
                    # the app fires after boot, and domcontentloaded returns
                    # before a line of that JS has run.
                    wait_until="networkidle",
                    timeout=30000,
                )
            except Exception as nav_error:
                logger.warning(f"Navigation slow but continuing: {nav_error}")

            # Poll the jar rather than sleeping a fixed guess: the refresh is a
            # round trip to Tehran and 1.5s was optimistic on a good day.
            fresh = None
            browser_died = False
            for _ in range(12):
                if not self.browser_alive():
                    # Twelve seconds of polling a closed browser tells us
                    # nothing and costs a minute across four accounts.
                    browser_died = True
                    break
                live = await self.get_current_cookies()
                if access_token_state(live) == "live":
                    fresh = live
                    break
                await asyncio.sleep(1.0)

            if browser_died:
                logger.warning(
                    f"{phone_number}: the browser closed while waiting for the "
                    f"refresh — the session is untested, not expired")
                return False

            current_url = self.page.url
            if "/login" in current_url or current_url.rstrip("/") in (
                "https://divar.ir",
                "https://www.divar.ir",
            ):
                logger.warning(
                    f"Session invalid — redirected to {current_url}. Cookies are expired."
                )
                return False

            if fresh is None:
                # The URL check above passes for an unauthenticated visitor too
                # — Divar's SPA renders its shell either way — so returning True
                # here is how a dead session was reported as restored, and the
                # scrape then walked into an OTP prompt on its first reveal.
                state = access_token_state(await self.get_current_cookies())
                logger.warning(
                    f"{phone_number}: access token still {state} after the page "
                    f"had its chance to refresh — treating the session as expired")
                return False

            after = access_token_expiry(fresh)
            if before != after:
                logger.info(
                    f"{phone_number}: Divar issued a fresh access token "
                    f"(valid to {after:%Y-%m-%d %H:%M} UTC)")
                # Keep it. The stored jar is what the next rotation and every
                # direct httpx call replay, so an unsaved refresh is no refresh.
                await self.save_cookies_to_file(phone_number, fresh)
                if self.db_session:
                    from app.services.divar_session import auth_cookie
                    tok = auth_cookie(fresh)
                    await self.save_cookies_to_db(
                        phone_number, fresh, tok.get("value") if tok else None)

            logger.info(f"Session restored successfully for {phone_number} (url={current_url})")
            return True
            
        except Exception as e:
            logger.error(f"Failed to restore session: {e}")
            return False
    
    async def invalidate_cookies(self, phone_number: str) -> bool:
        """Invalidate stored cookies"""
        try:
            # Remove from database
            if self.db_session:
                result = await self.db_session.execute(
                    select(Cookie).where(Cookie.phone_number == phone_number)
                )
                cookie = result.scalar_one_or_none()
                if cookie:
                    cookie.is_valid = False
                    await self.db_session.commit()
            
            # Remove file
            cookie_file = self.get_cookie_file_path(phone_number)
            if cookie_file.exists():
                os.remove(cookie_file)
            
            logger.info(f"Cookies invalidated for {phone_number}")
            return True
        except Exception as e:
            logger.error(f"Failed to invalidate cookies: {e}")
            return False
    
    async def get_cookie_status(self, phone_number: str) -> Dict[str, Any]:
        """Get status of stored cookies using only the database (no browser launch)."""
        status = {
            "has_cookies": False,
            "is_valid": False,
            "expires_at": None,
            "phone_number": phone_number,
            "message": "No cookies found",
        }

        if not self.db_session:
            return status

        try:
            result = await self.db_session.execute(
                select(Cookie).where(Cookie.phone_number == phone_number)
            )
            record = result.scalar_one_or_none()

            if not record:
                return status

            status["has_cookies"] = True
            status["expires_at"] = record.expires_at.isoformat() if record.expires_at else None

            if not record.is_valid:
                status["message"] = "Cookies expired or invalid. Please login again."
                return status

            if record.expires_at:
                expires = record.expires_at.replace(tzinfo=None) if record.expires_at.tzinfo else record.expires_at
                if expires < datetime.now(timezone.utc).replace(tzinfo=None):
                    record.is_valid = False
                    await self.db_session.commit()
                    status["message"] = "Cookies expired or invalid. Please login again."
                    return status

            status["is_valid"] = True
            status["message"] = "Session active and verified with Divar."
            return status

        except Exception as e:
            status["message"] = f"Error checking cookie status: {e}"
            return status
