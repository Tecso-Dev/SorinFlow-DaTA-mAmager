"""
Contact information extractor for Divar listings.
Handles click-to-reveal phone numbers and captcha solving.
"""
import re
import asyncio
import random
from pathlib import Path
from typing import Optional, List

from loguru import logger

from app.scraper.parsers import parse_persian_number
from app.scraper.captcha_solver import PuzzleCaptchaSolver
from app.config import get_settings

settings = get_settings()


class ContactExtractor:
    """Extracts phone numbers from a Divar listing page."""

    def __init__(self, page, images_dir: Path, otp_key: Optional[str] = None):
        self.page = page
        self.images_dir = images_dir
        self.otp_key = otp_key  # key into otp_store; set by scraper when a job is running

    async def get_phone_number(self) -> Optional[str]:
        """Click the contact button and return the extracted phone number."""
        try:
            login_phone = getattr(settings, 'divar_phone_number', None)
            normalized_login = re.sub(r"[^0-9]", "", str(login_phone)) if login_phone else None

            def _is_login_phone(num: str) -> bool:
                if not normalized_login:
                    return False
                norm = re.sub(r"[^0-9]", "", str(num))
                if not norm:
                    return False
                return norm == normalized_login or (
                    len(normalized_login) >= 10 and norm.endswith(normalized_login[-10:])
                )

            contact_selectors = [
                '.post-actions__get-contact',
                'button:has-text("اطلاعات تماس")',
                'button:has-text("شماره تماس")',
                'button:has-text("تماس")',
                'button:has-text("اطلاعات تماس گیرنده")',
                'text="اطلاعات تماس"',
                'text="شماره تماس"',
                'text="تماس"',
                '[data-testid="contact-button"]',
                '.kt-contact-row button',
                '.post-contact-info button',
                'a.post-actions__get-contact',
                'button[class*="contact"]',
                'a[class*="phone"]',
            ]

            contact_button = None
            for selector in contact_selectors:
                try:
                    btn = await self.page.query_selector(selector)
                    if btn and await btn.is_visible():
                        contact_button = btn
                        logger.info(f"Found visible contact button with selector: {selector}")
                        break
                except Exception:
                    continue

            if not contact_button:
                logger.warning("No contact button found on page - phone cannot be extracted")
                return None

            await asyncio.sleep(random.uniform(0.3, 0.8))

            try:
                await contact_button.scroll_into_view_if_needed()
                await asyncio.sleep(0.5)
                try:
                    await contact_button.click(force=True, timeout=5000)
                    logger.info("Contact button clicked successfully")
                except Exception as force_err:
                    logger.warning(f"force click failed: {force_err}")
                    await self.page.evaluate(
                        '''(el) => { el.dispatchEvent(new MouseEvent('click',
                            {view: window, bubbles: true, cancelable: true})); }''',
                        contact_button,
                    )
                    logger.info("Contact button clicked via dispatchEvent")
            except Exception as click_err:
                logger.warning(f"All click methods failed: {click_err}")

            try:
                await self._handle_captcha_if_present()
            except Exception as captcha_err:
                logger.warning(f"Captcha handler raised exception, continuing: {captcha_err}")

            # Wait for network to settle
            try:
                await self.page.wait_for_selector(
                    "#challenge, #voiceChallenge, [data-arcaptcha-site-key]", timeout=3000
                )
            except Exception:
                pass

            await asyncio.sleep(random.uniform(1.0, 2.0))

            # Handle Divar SMS-OTP for contact-info verification
            await self._handle_sms_otp_if_present()

            # Dismiss PWA info modal if present
            try:
                for btn in await self.page.query_selector_all('.kt-new-modal__footer button.kt-button--primary'):
                    try:
                        text = (await btn.inner_text() or '').strip()
                    except Exception:
                        continue
                    if 'متوجه شدم' in text:
                        logger.info("Dismissing PWA info modal")
                        try:
                            await btn.click(force=True, timeout=3000)
                        except Exception:
                            try:
                                await btn.click()
                            except Exception:
                                pass
                        await asyncio.sleep(1.0)
                        break
            except Exception:
                pass

            # Debug screenshot
            try:
                debug_path = self.images_dir.parent / "debug" / "debug_after_click.png"
                debug_path.parent.mkdir(parents=True, exist_ok=True)
                await self.page.screenshot(path=str(debug_path))
            except Exception:
                pass

            # Log page state for debugging
            try:
                content = await self.page.content()
                if 'tel:' in content:
                    logger.info("Phone number link found in page content")
                else:
                    logger.info("No tel: link found in page content after click")
                    if 'kt-new-modal' in content or 'kt-modal' in content:
                        logger.info("Modal detected on page")
                    phone_patterns = re.findall(r'[۰-۹0-9]{10,11}', content)
                    if phone_patterns:
                        logger.info(f"Found phone-like patterns: {phone_patterns[:5]}")
            except Exception:
                pass

            phone_selectors = [
                'a[href^="tel:"]',
                '.kt-unexpandable-row__action a[href^="tel:"]',
                '.kt-base-row a[href^="tel:"]',
                '.kt-new-modal a[href^="tel:"]',
                '.kt-modal a[href^="tel:"]',
                '.kt-dimmer a[href^="tel:"]',
                '[role="dialog"] a[href^="tel:"]',
                '[data-testid="phone-number"]',
                '[data-phone-action="true"]',
                '.post-contact a',
                '.post-actions__phone a',
                'a[class*="phone"]',
                'button[data-action="call"]',
            ]

            for attempt in range(3):
                for selector in phone_selectors:
                    try:
                        phone_elem = await self.page.wait_for_selector(selector, timeout=800)
                        if not phone_elem:
                            continue
                        try:
                            is_visible = await phone_elem.is_visible()
                        except Exception:
                            is_visible = True
                        if not is_visible:
                            continue

                        logger.info(f"Found phone element with selector: {selector}")
                        href = await phone_elem.get_attribute('href')
                        phone_text = (
                            href.replace('tel:', '').strip()
                            if href and href.startswith('tel:')
                            else await phone_elem.inner_text()
                        )
                        logger.info(f"Raw phone text: {phone_text}")

                        phone = parse_persian_number(phone_text)
                        if phone:
                            phone_str = str(phone)
                            if _is_login_phone(phone_str):
                                logger.info("Skipping — matches login phone")
                                continue
                            if len(phone_str) == 10 and not phone_str.startswith('0'):
                                return f"0{phone_str}"
                            if len(phone_str) >= 10:
                                return phone_str
                    except Exception:
                        continue
                await asyncio.sleep(1.5)

            # Regex fallback: only valid Iranian mobile numbers (09xxxxxxxxx)
            try:
                content = await self.page.content()
                # Normalize Persian digits first, then match 09xxxxxxxxx
                norm = content
                for p, e in zip('۰۱۲۳۴۵۶۷۸۹', '0123456789'):
                    norm = norm.replace(p, e)
                m = re.search(r'(?<![0-9])(09[0-9]{9})(?![0-9])', norm)
                if m:
                    phone = m.group(1)
                    if not _is_login_phone(phone):
                        logger.info(f"Extracted phone via regex fallback: {phone}")
                        return phone
            except Exception:
                pass

            logger.warning("No phone element found after clicking contact button")
            return None

        except Exception as e:
            logger.warning(f"Failed to get phone number: {e}")
            return None

    async def _handle_sms_otp_if_present(self) -> None:
        """Detect Divar's SMS OTP verification for contact info, wait for user code."""
        try:
            # Use instant query_selector — NOT wait_for_selector (avoids N×3s delays)
            OTP_SELECTORS = [
                'input[name="code"]',
                'input[inputmode="numeric"]',
                'input[maxlength="6"]',
                '.kt-new-modal input',
                '[role="dialog"] input',
            ]
            otp_input = None
            for sel in OTP_SELECTORS:
                try:
                    el = await self.page.query_selector(sel)
                    if el and await el.is_visible():
                        placeholder = (await el.get_attribute('placeholder') or '').lower()
                        if 'search' in placeholder or 'جستجو' in placeholder:
                            continue
                        otp_input = el
                        logger.info(f"Divar SMS-OTP input detected: {sel}")
                        break
                except Exception:
                    continue

            if not otp_input:
                logger.info("No SMS-OTP modal detected, continuing normally")
                return

            if not self.otp_key:
                logger.warning("SMS-OTP modal found but otp_key not set — phone extraction skipped")
                return

            from app.scraper import otp_store
            event = otp_store.request(self.otp_key)
            logger.info(f"SMS-OTP required — waiting up to 300s for user input (key={self.otp_key})")

            try:
                await asyncio.wait_for(event.wait(), timeout=300)
            except asyncio.TimeoutError:
                logger.warning("SMS-OTP timeout — user did not submit code in 300s")
                otp_store.clear(self.otp_key)
                return

            code = otp_store.pop_code(self.otp_key)
            if not code:
                logger.warning("OTP event fired but no code found in store")
                return

            logger.info(f"OTP code received, entering into page")
            await otp_input.click()
            await otp_input.fill(code)
            await asyncio.sleep(0.5)

            # Submit: try dedicated confirm button, then Enter key as fallback
            submitted = False
            for btn_sel in [
                '.kt-new-modal button.kt-button--primary',
                '[role="dialog"] button[type="submit"]',
                'button[type="submit"]',
                '.kt-new-modal button',
                '[role="dialog"] button',
            ]:
                try:
                    btn = await self.page.query_selector(btn_sel)
                    if btn and await btn.is_visible():
                        await btn.click()
                        logger.info(f"OTP form submitted via button: {btn_sel}")
                        submitted = True
                        break
                except Exception:
                    continue

            if not submitted:
                await otp_input.press('Enter')
                logger.info("OTP submitted via Enter key")

            await asyncio.sleep(2.0)
            logger.info("SMS-OTP handled, continuing phone extraction")

        except Exception as e:
            logger.warning(f"Error in SMS-OTP handler: {e}")

    async def _handle_captcha_if_present(self) -> None:
        """Detect and attempt to solve an ARCaptcha puzzle if present."""
        try:
            captcha_container = None
            try:
                captcha_container = await self.page.wait_for_selector(
                    "#challenge, #voiceChallenge, [data-arcaptcha-site-key]",
                    timeout=4000,
                )
            except Exception:
                pass

            if not captcha_container:
                return

            logger.info("Captcha container detected, attempting to solve")

            bg_element = None
            for sel in [
                "#challenge .arc-puzzle img.tw-object-contain",
                "#challenge img.tw-object-contain",
                "#challenge img",
                "#voiceChallenge .arc-puzzle img.tw-object-contain",
                "#voiceChallenge img",
            ]:
                try:
                    bg_element = await self.page.query_selector(sel)
                    if bg_element:
                        logger.info(f"Captcha background found: {sel}")
                        break
                except Exception:
                    continue

            gap_element = None
            for sel in [
                "#challenge .arc-puzzle img.puzzle",
                "#challenge img.puzzle",
                ".arc-puzzle img.puzzle",
            ]:
                try:
                    gap_element = await self.page.query_selector(sel)
                    if gap_element:
                        logger.info(f"Captcha gap found: {sel}")
                        break
                except Exception:
                    continue

            if not bg_element or not gap_element:
                logger.warning("Captcha elements not found, skipping solver")
                return

            gap_path    = self.images_dir / "captcha_gap.png"
            bg_path     = self.images_dir / "captcha_bg.png"
            result_path = self.images_dir / "captcha_result.png"

            # Wait for captcha images to fully load before screenshotting
            try:
                await self.page.evaluate("""() => {
                    const imgs = document.querySelectorAll('#challenge img, #voiceChallenge img');
                    return Promise.all(Array.from(imgs).map(img =>
                        img.complete ? Promise.resolve()
                        : new Promise(r => { img.onload = r; img.onerror = r; })
                    ));
                }""")
            except Exception:
                await asyncio.sleep(0.8)

            await gap_element.screenshot(path=str(gap_path))
            await bg_element.screenshot(path=str(bg_path))

            # Get background element's on-screen width for pixel scaling
            bg_box = await bg_element.bounding_box()
            bg_screen_w = bg_box["width"] if bg_box else None

            solver = PuzzleCaptchaSolver(
                gap_image_path=str(gap_path),
                bg_image_path=str(bg_path),
                output_image_path=str(result_path),
            )
            # Run synchronous OpenCV work in a thread pool to avoid blocking the event loop
            loop = asyncio.get_event_loop()
            position = await loop.run_in_executor(None, solver.discern)
            logger.info(f"PuzzleCaptchaSolver returned slide position: {position}")

            # Scale position from screenshot pixels → screen pixels
            if position is not None and bg_screen_w:
                try:
                    import cv2 as _cv2
                    bg_img = _cv2.imread(str(bg_path))
                    if bg_img is not None:
                        bg_img_w = bg_img.shape[1]
                        if bg_img_w > 0:
                            scale = bg_screen_w / bg_img_w
                            position = position * scale
                            logger.info(f"Scaled position: {position:.1f}px (scale={scale:.2f})")
                except Exception:
                    pass

            # Sanity check: puzzle gaps are never in the first 15% of background
            if position is not None and bg_screen_w and position < bg_screen_w * 0.15:
                logger.warning(f"Position {position:.1f} looks too small, using 40% fallback")
                position = bg_screen_w * 0.40

            if position is None:
                # No solver — try a single drag at 40% of background width as fallback
                position = (bg_screen_w * 0.40) if bg_screen_w else 80.0
                logger.warning(f"Captcha solver failed, using fallback position: {position:.1f}px")

            slider = await self.page.query_selector(
                "#challenge .draggable, #challenge [class*='draggable'], #challenge [role='slider']"
            )
            if not slider:
                logger.warning("Captcha slider not found")
                return

            box = await slider.bounding_box()
            if not box:
                logger.warning("Captcha slider bounding box is None")
                return

            start_x = box["x"] + box["width"] / 2
            start_y = box["y"] + box["height"] / 2
            drag_distance = max(20.0, min(float(position), 500.0))

            # Human-like drag with easing
            await self.page.mouse.move(start_x, start_y)
            await asyncio.sleep(0.3)
            await self.page.mouse.down()
            await asyncio.sleep(0.1)
            steps = 30
            for step in range(steps):
                # Ease-in-out curve for natural movement
                t = (step + 1) / steps
                ease = t * t * (3 - 2 * t)
                await self.page.mouse.move(
                    start_x + drag_distance * ease,
                    start_y + random.uniform(-0.5, 0.5),
                )
                await asyncio.sleep(random.uniform(0.01, 0.03))
            await self.page.mouse.up()
            await asyncio.sleep(2.5)
            logger.info(f"Captcha slider dragged {drag_distance:.1f}px")

        except Exception as e:
            logger.warning(f"Error handling captcha: {e}")
