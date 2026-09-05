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

    def __init__(self, page, images_dir: Path, otp_key: Optional[str] = None,
                 on_pause=None, on_resume=None, should_cancel=None,
                 account_phone: Optional[str] = None, on_challenge=None,
                 on_verified=None):
        self.page = page
        self.images_dir = images_dir
        self.otp_key = otp_key  # key into otp_store; set by scraper when a job is running
        # How many Divar sessions the scraper can rotate through. Decides how
        # many unanswered prompts to absorb before concluding that every
        # account is challenged rather than just this one.
        self.account_count = 1
        # Which saved Divar account is logged in right now. The SMS goes to this
        # number, and with rotation on it is not necessarily the one the user
        # started the job with — so the prompt has to name it.
        self.account_phone = account_phone
        # async callbacks fired when the scraper pauses (OTP requested) and
        # resumes (code entered / wait ended) — used to flip the job status
        self.on_pause = on_pause
        self.on_resume = on_resume
        # async predicate: returns True if the job was cancelled → stop waiting
        self.should_cancel = should_cancel
        # Fired the moment Divar demands a code, before we settle in to wait for
        # a human. The scraper uses it to rotate to a fresh account instead —
        # the challenge is this number telling us it is spent.
        self.on_challenge = on_challenge
        # Fired once a code has been accepted. Divar has just granted this
        # session the trust the code existed to establish, and it lives in the
        # jar the browser now holds. Without this the scraper never saved it,
        # so the next use of the account restored the pre-verification jar and
        # was challenged all over again — which is why five accounts sat at
        # nought to four reveals between them.
        self.on_verified = on_verified

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

            # Solve the captcha and look for the phone, retrying with a fresh
            # puzzle on failure. ARCaptcha serves a new puzzle each attempt, so
            # multiple tries multiply the odds of a correct slide.
            MAX_ATTEMPTS = 3
            for attempt_num in range(1, MAX_ATTEMPTS + 1):
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

                phone = await self._scan_for_phone(_is_login_phone)
                if phone:
                    return phone

                # No phone yet. If the captcha challenge is gone, retrying the
                # puzzle won't help; stop. Otherwise refresh and try again.
                challenge = None
                try:
                    challenge = await self.page.query_selector(
                        "#challenge, #voiceChallenge, [data-arcaptcha-site-key]"
                    )
                except Exception:
                    pass
                if not challenge:
                    logger.info("No captcha challenge remaining; stopping phone retries")
                    break
                if attempt_num < MAX_ATTEMPTS:
                    logger.info(
                        f"Captcha attempt {attempt_num}/{MAX_ATTEMPTS} did not reveal "
                        f"phone — retrying with a fresh puzzle"
                    )
                    await self._refresh_captcha()
                    await asyncio.sleep(random.uniform(1.0, 1.8))

            logger.warning("No phone element found after clicking contact button")
            return None

        except Exception as e:
            logger.warning(f"Failed to get phone number: {e}")
            return None

    async def _scan_for_phone(self, is_login_phone) -> Optional[str]:
        """Search the current page/modal for a revealed phone number."""
        try:
            content = await self.page.content()
            if 'tel:' in content:
                logger.info("Phone number link found in page content")
            else:
                logger.info("No tel: link found in page content after click")
                if 'kt-new-modal' in content or 'kt-modal' in content:
                    logger.info("Modal detected on page")
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
                        if is_login_phone(phone_str):
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
            norm = content
            for p, e in zip('۰۱۲۳۴۵۶۷۸۹', '0123456789'):
                norm = norm.replace(p, e)
            m = re.search(r'(?<![0-9])(09[0-9]{9})(?![0-9])', norm)
            if m:
                phone = m.group(1)
                if not is_login_phone(phone):
                    logger.info(f"Extracted phone via regex fallback: {phone}")
                    return phone
        except Exception:
            pass

        return None

    async def _refresh_captcha(self) -> None:
        """Ask ARCaptcha for a fresh puzzle after a failed slide.

        Most ARCaptcha widgets auto-load a new puzzle after a wrong attempt, so
        a refresh button isn't always present — in that case the next
        `_handle_captcha_if_present` simply picks up the new images.
        """
        refresh_selectors = [
            "#challenge [class*='refresh']",
            "#challenge button[aria-label*='refresh']",
            "#challenge [aria-label*='تازه']",
            "#voiceChallenge [class*='refresh']",
            "[class*='captcha'] [class*='refresh']",
        ]
        for sel in refresh_selectors:
            try:
                el = await self.page.query_selector(sel)
                if el and await el.is_visible():
                    await el.click(force=True, timeout=2000)
                    logger.info(f"Clicked captcha refresh button: {sel}")
                    return
            except Exception:
                continue
        logger.info("No captcha refresh button found; relying on auto-refresh")

    async def _request_otp_resend(self) -> bool:
        """Click Divar's resend control if it is offering one.

        Matched on visible text, not class names: the wording is stable and the
        markup is not. A disabled button means Divar is still counting down from
        a send that did happen, so it is left alone.
        """
        WORDS = ("ارسال مجدد", "ارسال دوباره", "دریافت مجدد", "ارسال کد",
                 "دریافت کد", "کد را دوباره")
        try:
            for el in await self.page.query_selector_all(
                    'button, a, [role="button"]'):
                try:
                    if not await el.is_visible():
                        continue
                    text = ((await el.inner_text()) or "").strip()
                    if not text or not any(w in text for w in WORDS):
                        continue
                    if not await el.is_enabled():
                        logger.info(f"OTP resend still counting down ({text!r}) — a code was sent")
                        return False
                    await el.click(force=True, timeout=3000)
                    logger.info(f"Asked Divar to send the OTP again via {text!r}")
                    await asyncio.sleep(1.0)
                    return True
                except Exception:
                    continue
        except Exception as e:
            logger.debug(f"OTP resend lookup failed: {e}")
        logger.info("No OTP resend control on the page — relying on Divar's own send")
        return False

    async def _notify_code_needed(self, waited: float) -> None:
        """Email whoever can answer, once per prompt. Never raises.

        A run parked waiting for a code is silent — it just stops advancing —
        so without this the "wait for a human" mode only works for a human who
        happens to be looking at the scraper page.
        """
        try:
            from app.services import email_service, email_templates
            from app.database import async_session_maker
            from app.models.user import User
            from sqlalchemy import select

            async with async_session_maker() as db:
                to = (await db.execute(
                    select(User.email).where(
                        User.email.isnot(None),
                        User.role.in_(("root", "super_admin")),
                        User.is_active == True,          # noqa: E712
                    ).limit(1)
                )).scalar_one_or_none()

                if not to:
                    # Fall back to the account we send FROM.
                    #
                    # No admin had an address on file, and an alert nobody
                    # receives is the same as no alert — the run parks silently
                    # for six hours and the person who could have freed it in
                    # ten seconds never hears. The agency's own SMTP mailbox is
                    # read by the people who would answer.
                    cfg = await email_service.resolve_config(db)
                    to = (cfg.get("from_email") or cfg.get("user") or "").strip()
                    if to:
                        logger.info(
                            "[otp] no admin address on file — notifying the "
                            f"sending account instead ({to})")

                if not to:
                    logger.warning(
                        "[otp] nobody to notify: no admin email and no SMTP "
                        "account configured. The run is parked and silent — set "
                        "an email on an admin user, or configure the email panel.")
                    return

                subject = "دیوار کد تأیید می‌خواهد — اسکرپ متوقف است"
                body = (
                    f"اسکرپر برای گرفتن شمارهٔ تماس به کد تأیید دیوار نیاز دارد "
                    f"و {int(waited)} ثانیه است منتظر مانده.\n\n"
                    f"شمارهٔ حساب: {self.account_phone or '—'}\n\n"
                    "برای ادامه، وارد پنل شوید و در بخش «اسکرپر» کد پیامک‌شده را "
                    "وارد کنید. تا آن زمان اسکرپ متوقف می‌ماند و آگهی‌ها بدون "
                    "شمارهٔ تماس ذخیره نمی‌شوند."
                )
                # notification() returns (subject, html, text) — the same shape
                # every template in that module uses.
                subj, html, text = email_templates.notification(
                    subject, body,
                    cta_label="ورود به پنل", cta_url="https://sorinflow.com/dashboard/")
                await email_service.send(to, subj, html, text, db=db)
                logger.warning(f"[otp] emailed {to}: a Divar code is needed")
        except Exception as e:
            # A notification that fails must never take the scrape with it.
            logger.warning(f"[otp] could not send the code-needed email: {e}")

    # Everything here is markup, not data — safe to put in a log in full.
    _INPUT_ATTRS = ("name", "id", "type", "inputmode", "maxlength",
                    "placeholder", "autocomplete")

    async def _modal_text(self) -> str:
        """The visible text of whichever dialog is on screen, or ''."""
        for sel in ('.kt-new-modal', '[role="dialog"]', '.kt-modal'):
            try:
                el = await self.page.query_selector(sel)
                if el and await el.is_visible():
                    return ((await el.inner_text()) or "").strip()
            except Exception:
                continue
        return ""

    async def _input_attrs(self, el) -> dict:
        """The attributes that say what a field is for. Never raises."""
        out = {}
        for a in self._INPUT_ATTRS:
            try:
                v = await el.get_attribute(a)
            except Exception:
                v = None
            if v:
                out[a] = v
        return out

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

            # What Divar actually put on the screen.
            #
            # This modal has been guessed at for days from the outside. It is
            # reached headless, inside a container, so nobody can look at it —
            # and every selector above matches «an input in a dialog», which is
            # true of more than one Divar screen. The words are what tell them
            # apart, so the words go in the job log where they can be read.
            modal_text = await self._modal_text()
            logger.info(
                f"[otp] field {await self._input_attrs(otp_input)} | "
                f"modal says: {modal_text[:300]!r}"
            )

            if not self.otp_key:
                logger.warning("SMS-OTP modal found but otp_key not set — phone extraction skipped")
                return

            # Tell the scraper before anything else. Whether a human types the
            # code or nobody is watching, this account has been challenged and
            # should be swapped out at the next safe point — that is true even
            # if the wait below is cancelled or times out.
            if self.on_challenge:
                try:
                    self.on_challenge()
                except Exception as e:
                    logger.warning(f"on_challenge callback failed: {e}")

            from app.scraper import otp_store
            # If the user already dismissed an OTP prompt this run, don't block
            # every subsequent phone for the full timeout — skip straight away.
            if otp_store.is_cancelled(self.otp_key):
                logger.info("SMS-OTP suppressed for this job (dismissed earlier) — skipping phone")
                return

            # A modal can be on screen without Divar having sent anything — the
            # previous code was consumed, or the first send was rate-limited and
            # the form is sitting there with a «ارسال مجدد» control. Waiting on
            # that is waiting for an SMS nobody asked for.
            await self._request_otp_resend()

            event = otp_store.request(self.otp_key, self.account_phone or "")
            timeout = getattr(settings, "otp_wait_timeout", 300)

            # Wait the full time for the FIRST prompt of a job, and briefly for
            # the rest.
            #
            # The first unanswered prompt already told us nobody is at the
            # keyboard. Every account still gets tried — one of them may not be
            # challenged at all, and that costs nothing — but waiting the full
            # five minutes on each is how a run with five accounts spent
            # twenty-five minutes discovering the same fact five times, while
            # Divar's challenges kept arriving faster than the listings.
            #
            # Somebody who IS watching answers the first prompt, and the full
            # timeout is theirs.
            _prior = otp_store.strikes(otp_store.job_of(self.otp_key))

            # Wait for a human, or get on with it? A real choice, not a guess.
            #
            # The old behaviour assumed an unanswered prompt meant nobody was
            # watching, so it suppressed reveals and let the run finish without
            # phone numbers. For a business whose product IS the phone number
            # that is the wrong trade: «the phone number is very important —
            # this is the feature that separates us from others».
            #
            # With OTP_WAIT_FOR_HUMAN on, the run parks here with the prompt
            # live in the panel until somebody enters the code, capped by
            # OTP_WAIT_MAX_SECONDS so a job nobody returns to does not hold a
            # browser and a Divar session for ever. The cancel button still
            # works — the wait loop checks it every two seconds.
            _wait_for_human = bool(getattr(settings, "otp_wait_for_human", False))
            if _wait_for_human:
                timeout = max(timeout, int(getattr(settings, "otp_wait_max_seconds", 21600)))
                logger.info(
                    "SMS-OTP required — PAUSING and waiting for a human "
                    f"(up to {timeout // 3600}h). Enter the code in the scraper "
                    f"panel to continue. key={self.otp_key}")
            elif _prior:
                timeout = min(timeout, 30)
                logger.info(
                    f"{_prior} prompt(s) already went unanswered this job — "
                    f"waiting {timeout}s on this account rather than the full "
                    "window")
            else:
                logger.info(
                    f"SMS-OTP required — PAUSING scrape, waiting up to "
                    f"{timeout}s for code (key={self.otp_key})")

            # ── pause the job while we wait for the code ──
            paused_ok = False
            if self.on_pause:
                try:
                    await self.on_pause()
                    paused_ok = True
                except Exception as e:
                    logger.warning(f"on_pause callback failed: {e}")

            got_code = False
            try:
                # Wait in short slices so a user "close"/cancel is honored promptly
                waited = 0.0
                slice_s = 2.0
                _notified = False
                _notify_after = int(getattr(settings, "otp_notify_after_seconds", 120))
                while waited < timeout:
                    if otp_store.is_cancelled(self.otp_key):
                        logger.info("SMS-OTP wait cancelled by user")
                        otp_store.clear(self.otp_key)
                        break
                    if self.should_cancel:
                        try:
                            if await self.should_cancel():
                                logger.info("SMS-OTP wait aborted — job cancelled")
                                otp_store.clear(self.otp_key)
                                break
                        except Exception:
                            pass
                    try:
                        await asyncio.wait_for(event.wait(), timeout=slice_s)
                        got_code = True
                        break
                    except asyncio.TimeoutError:
                        waited += slice_s
                        # Tell somebody, once, that the run is parked.
                        #
                        # Waiting for a human is only useful if the human finds
                        # out. Nobody watches the scraper page at 3am, and the
                        # run is otherwise silent — it simply stops advancing.
                        if (not _notified
                                and waited >= _notify_after
                                and _wait_for_human):
                            _notified = True
                            await self._notify_code_needed(waited)
                if not got_code and not otp_store.is_cancelled(self.otp_key):
                    logger.warning(f"SMS-OTP timeout — no code in {timeout}s")
                    otp_store.clear(self.otp_key)

                    # A challenge belongs to ONE account, so try the others
                    # before giving up on phone numbers entirely.
                    #
                    # This used to cancel_all() on the first unanswered prompt.
                    # The reasoning was sound as far as it went — nobody
                    # answered, nobody will answer the next one, and every
                    # further listing would block for another timeout — but it
                    # threw away the other Divar accounts too, which is the
                    # whole point of rotation. A run with three good sessions
                    # revealed five numbers on the first account and then saved
                    # two hundred listings with «شماره تماس ---» on every one.
                    #
                    # on_challenge (already called above) forces a rotation at
                    # the next listing. So: count the unanswered prompts, and
                    # only suppress the job once every account has had its turn
                    # and been challenged too. A successful reveal resets the
                    # count, because it proves the pool is not exhausted.
                    job = otp_store.job_of(self.otp_key)
                    strikes = otp_store.note_timeout(job)
                    budget = max(1, int(self.account_count or 1))
                    if strikes >= budget:
                        otp_store.cancel_all(job)
                        logger.warning(
                            f"No one answered on {strikes} account(s) — every "
                            "session has been challenged, so OTP requests are "
                            "paused for the rest of this job. Listings still "
                            "save; phone numbers will be missing until a code "
                            "is entered.")
                    else:
                        logger.warning(
                            f"No one answered on this account ({strikes}/{budget}) "
                            "— rotating and trying the next session before "
                            "giving up on phone numbers.")
            finally:
                # ── resume the job (code entered, timed out, or cancelled) ──
                if paused_ok and self.on_resume:
                    try:
                        await self.on_resume()
                    except Exception as e:
                        logger.warning(f"on_resume callback failed: {e}")

            if not got_code:
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

            # Keep what the code bought.
            #
            # Divar issues the challenge to establish trust in this session;
            # once it is answered, that trust is in the cookies the browser is
            # holding right now. Persisting them is the whole point — the
            # stored jar is what the next rotation and the next job restore,
            # and leaving it at its pre-verification state meant every account
            # was challenged on its first reveal, every single time.
            #
            # Unconditional: if the code was wrong the jar is unchanged and
            # saving it is a no-op, which is cheaper than deciding whether the
            # modal really went away.
            if self.on_verified:
                try:
                    await self.on_verified()
                except Exception as e:
                    logger.warning(f"could not persist the verified session: {e}")

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
