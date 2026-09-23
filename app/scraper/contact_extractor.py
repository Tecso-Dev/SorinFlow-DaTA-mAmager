"""
Contact information extractor for Divar listings.
Handles click-to-reveal phone numbers and captcha solving.
"""
import re
import asyncio
import json
import shutil
import time
import random
from datetime import datetime
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
                 on_verified=None, on_identity_required=None):
        self.page = page
        # Divar asked this account to prove who it is — national ID, birth
        # date. Nothing here can answer that; the scraper marks the account
        # and tells the panel. See _identity_wall().
        self.on_identity_required = on_identity_required
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
        # "phone" | "chat_only" | "unavailable" | None — how the last reveal
        # ended, for the row to record. See _chat_only for why it matters.
        self.contact_channel = None
        # Set when Divar demands identity verification. Account-level:
        # the run stops trying to reveal numbers rather than writing
        # each listing off in turn.
        self.needs_identity = False
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
                # Two very different reasons for no button, and only one of
                # them is worth a second visit.
                #
                # A poster can hide their number and take contact through
                # Divar's chat only. That is a decision about the ad, not a
                # failure of ours: no retry, no rotation, no fresh account will
                # ever produce a phone. Left indistinguishable from «could not
                # get it», every such listing was re-opened on every run to
                # fill a gap that is not a gap — and the panel called it a row
                # with a missing number instead of a row whose number is not
                # on offer.
                _t = await self._page_text()
                if await self._needs_identity(_t):
                    # Account-level. Writing this listing off would be wrong and
                    # permanent, and the next listing would fail identically.
                    self.contact_channel = "needs_identity"
                    self.needs_identity = True
                    logger.error(
                        "Divar is asking this account to verify its identity — "
                        "no phone number can be revealed until that is done at "
                        "divar.ir/my-divar/identity-confirmation")
                    return None
                if await self._chat_only(_t):
                    self.contact_channel = "chat_only"
                    logger.info("Poster takes contact through chat only — no phone to reveal")
                    return None
                self.contact_channel = "unavailable"
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

                # Divar's identity wall can be a whole page, not a dialog.
                try:
                    _body = ((await self.page.inner_text("body")) or "")[:20000]
                except Exception:
                    _body = ""
                if self._identity_wall(_body):
                    await self._report_identity_wall(_body)
                    return None

                # A notice standing between the click and the number.
                #
                # From a real run: «Contact button clicked» → «No tel: link
                # found» → «Modal detected on page» → «No phone element found»,
                # and the listing was saved without a number that Divar was
                # showing in the same session by hand. The modal had no input
                # — so it was not the code prompt — and its button did not say
                # «متوجه شدم», which was the only wording this dismissed. A
                # safety notice, a terms nudge, a first-reveal tip: Divar has
                # several, the wording moves, and every one of them sits
                # exactly where the number is about to appear.
                #
                # Any modal with no input that shows up after «اطلاعات تماس»
                # is a notice, and acknowledging it is what a person does.
                # Its words go in the log first, so the next one that does
                # not match is a line to read rather than a listing lost.
                await self._acknowledge_notice()

                # Debug screenshot
                try:
                    debug_path = self.images_dir.parent / "debug" / "debug_after_click.png"
                    debug_path.parent.mkdir(parents=True, exist_ok=True)
                    await self.page.screenshot(path=str(debug_path))
                except Exception:
                    pass

                phone = await self._scan_for_phone(_is_login_phone)
                if phone:
                    self.contact_channel = "phone"
                    return phone

                # The modal opened and says so in words: the poster hid it.
                _t2 = await self._page_text()
                if await self._needs_identity(_t2):
                    self.contact_channel = "needs_identity"
                    self.needs_identity = True
                    logger.error("Divar is asking this account to verify its identity")
                    return None
                if await self._chat_only(_t2):
                    self.contact_channel = "chat_only"
                    logger.info("Contact modal offers chat only — the poster hid the number")
                    return None

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
                    self._keep_failed_captcha()
                    await self._refresh_captcha()
                    await asyncio.sleep(random.uniform(1.0, 1.8))

            self._keep_failed_captcha()
            logger.warning("No phone element found after clicking contact button")
            if self.contact_channel is None:
                self.contact_channel = "unavailable"
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

    # What Divar shows when the poster has hidden their number.
    # Divar SAYING the number is not on offer. Nothing else counts.
    #
    # This used to also accept the presence of a chat control — «چت» buttons,
    # a[href*="/chat/"], [class*="chat"] button. Every Divar page has one:
    # «چت و تماس» sits in the site header on every listing. So the moment the
    # contact button was missing for ANY reason the page still matched, and
    # the listing was recorded as «the poster only takes chat» — permanently,
    # because property_exists refuses to re-scrape those.
    #
    # It happened for real: Divar restricted the account and demanded identity
    # verification, «اطلاعات تماس» disappeared, the header chat link did not,
    # and twenty listings were written off as unreachable while their numbers
    # were sitting on the page. One was checked by hand — ۰۹۰۳۲۰۲۳۱۰۰.
    #
    # A claim this permanent needs the site to state it, not to merely fail to
    # contradict it.
    _HIDDEN_WORDS = ("شماره مخفی", "شماره تماس مخفی", "فقط از طریق چت",
                     "تنها از طریق چت", "امکان تماس تلفنی وجود ندارد",
                     "شماره‌ای ثبت نشده", "بدون شماره تماس")

    # Divar asking the ACCOUNT to prove who it is. Account-level, not about
    # this listing: every reveal fails until somebody completes it at
    # divar.ir/my-divar/identity-confirmation.
    _IDENTITY_WORDS = ("تایید هویت", "تأیید هویت", "احراز هویت",
                       "کد ملی", "هویت خود را تایید", "هویت خود را تأیید")

    async def _page_text(self) -> str:
        try:
            return ((await self.page.inner_text("body")) or "")[:20000]
        except Exception:
            return ""

    async def _chat_only(self, text: str = None) -> bool:
        """True only when Divar SAYS the number is not on offer.

        The absence of a contact button is not evidence: it is also what a
        restricted account, a slow render and a changed class name look like.
        This marks a listing unreachable forever, so it takes a sentence.
        """
        t = text if text is not None else await self._page_text()
        return any(w in t for w in self._HIDDEN_WORDS)

    async def _needs_identity(self, text: str = None) -> bool:
        """True when Divar is asking this ACCOUNT to verify itself.

        Not about the listing: every reveal on every listing fails until
        somebody completes it, so a run that hits this is not collecting
        phone numbers at all and should say so rather than writing each
        listing off in turn.
        """
        t = text if text is not None else await self._page_text()
        if not any(w in t for w in self._IDENTITY_WORDS):
            return False
        # «تایید هویت» is also a menu item on every logged-in page. It only
        # means us when it is being demanded, not merely offered.
        return any(w in t for w in ("هویت خود را", "احراز هویت", "کد ملی"))

    # Buttons that acknowledge a notice, in the order to prefer them. The
    # first is the one this used to require; the rest are Divar's other
    # wordings for the same gesture.
    _ACK_WORDS = ("متوجه شدم", "فهمیدم", "باشه", "تأیید", "تایید", "ادامه",
                  "قبول", "بستن", "OK")

    async def _acknowledge_notice(self) -> bool:
        """Dismiss a no-input modal, logging what it said. True if one was."""
        try:
            modal = None
            for sel in ('.kt-new-modal', '[role="dialog"]', '.kt-modal'):
                el = await self.page.query_selector(sel)
                if el and await el.is_visible():
                    modal = el
                    break
            if modal is None:
                return False
            # A modal WITH an input is the code prompt (or a phone step), and
            # belongs to the OTP handler — never click through that.
            if await modal.query_selector('input'):
                return False
            text = ((await modal.inner_text()) or "").strip()
            buttons = await modal.query_selector_all('button, [role="button"], a.kt-button')
            labelled = []
            for b in buttons:
                try:
                    if await b.is_visible():
                        labelled.append((((await b.inner_text()) or "").strip(), b))
                except Exception:
                    continue
            logger.info(f"[notice] modal after contact click — says: {text[:200]!r}; "
                        f"buttons: {[t for t, _ in labelled][:6]}")
            target = None
            for word in self._ACK_WORDS:
                for t, b in labelled:
                    if word in t:
                        target = (t, b)
                        break
                if target:
                    break
            if target is None and len(labelled) == 1:
                target = labelled[0]          # one button on a notice: that is the one
            if target is None:
                logger.warning("[notice] no button on it matched — leaving it; the number stays hidden")
                return False
            t, b = target
            try:
                await b.click(force=True, timeout=3000)
            except Exception:
                await b.click()
            logger.info(f"[notice] acknowledged via {t!r}")
            await asyncio.sleep(1.0)
            return True
        except Exception as e:
            logger.debug(f"[notice] lookup failed: {e}")
            return False

    # What Divar's identity verification says about itself. Both halves are
    # required: «کد ملی» alone appears in a poster's own text now and then
    # («کد ملی نمی‌دهم»), and «احراز هویت» alone is the panel's own word.
    _IDENTITY_ID_WORDS = ("کد ملی", "کدملی", "شماره ملی", "شمارهٔ ملی", "شناسه ملی")
    _IDENTITY_ASK_WORDS = ("احراز هویت", "تاریخ تولد", "تایید هویت", "تأیید هویت",
                           "هویت خود را", "هویت شما")

    @classmethod
    def _identity_wall(cls, text: str) -> bool:
        t = (text or "").replace("‌", " ")
        return any(w in t for w in cls._IDENTITY_ID_WORDS) and \
            any(w in t for w in cls._IDENTITY_ASK_WORDS)

    async def _report_identity_wall(self, text: str) -> None:
        """Record the wall and hand the account back. Never raises."""
        self.contact_channel = "identity_required"
        logger.warning(
            f"[identity] Divar is asking {self.account_phone or '?'} to verify its identity "
            f"(national ID) — the page says: {(text or '')[:240]!r}")
        if self.on_identity_required:
            try:
                await self.on_identity_required((text or "")[:400])
            except Exception as e:
                logger.warning(f"[identity] on_identity_required failed: {e}")

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
                # Whose phone SHOULD have answered this, and what is wrong with
                # it. The alert used to say only «a code is needed», which is
                # the one thing the reader can already see; what they cannot
                # see is that their handset went offline forty minutes ago.
                fw_line = ""
                try:
                    from app.services import forwarder as _fw
                    from app.models.forwarder import ForwarderDevice as _FD
                    devs = (await db.execute(select(_FD).where(
                        _FD.is_active == True))).scalars().all()   # noqa: E712
                    mine = [d for d in devs
                            if _fw.same_phone(d.sim_phone, self.account_phone)]
                    if not mine:
                        fw_line = ("\n\nهیچ گوشی‌ای برای این شماره ثبت نشده — "
                                   "با ثبت آن در پنل، کدها خودکار وارد می‌شوند.")
                    else:
                        d = mine[0]
                        h = _fw.health(d)
                        if h["state"] != "ok":
                            fw_line = (f"\n\nفرستندهٔ پیامک «{d.label or d.device_id}» "
                                       f"مشکل دارد: {h['message_fa']}")
                        else:
                            fw_line = ("\n\nفرستندهٔ پیامک سالم است ولی این کد نرسید — "
                                       "شاید پیامک دیر رسیده. «ارسال دوباره کد» را بزنید.")
                except Exception as _fe:
                    logger.debug(f"[otp] could not describe the forwarder: {_fe}")

                # The OWNER of this Divar account first.
                #
                # Rotation moves between accounts mid-run, and the code goes to
                # whichever SIM is now active — so the person who can answer is
                # that account's owner, not whoever happens to be an admin. With
                # ten accounts across three people, mailing every admin every
                # time is how an alert becomes noise nobody opens.
                owner_to = []
                try:
                    from app.models.cookie import Cookie as _Ck
                    from app.services import forwarder as _fwsvc
                    cks = (await db.execute(select(_Ck))).scalars().all()
                    owner_ids = {c.owner_user_id for c in cks
                                 if getattr(c, "owner_user_id", None)
                                 and _fwsvc.same_phone(c.phone_number, self.account_phone)}
                    if owner_ids:
                        rows = (await db.execute(
                            select(User.email).where(
                                User.id.in_(owner_ids),
                                User.email.isnot(None),
                                User.is_active == True))).scalars().all()  # noqa: E712
                        owner_to = [a.strip() for a in rows if (a or "").strip()]
                except Exception as _oe:
                    logger.debug(f"[otp] could not resolve the account owner: {_oe}")

                # EVERY admin with an address, not one of them.
                #
                # This was .limit(1) with no ORDER BY: one admin, chosen by
                # whatever row Postgres returned first. On the live system that
                # was a developer's inbox, and the owner — the person who
                # actually enters the codes — never heard. A run sat paused
                # for fifty minutes while the one person who could free it in
                # ten seconds was told nothing.
                rows = (await db.execute(
                    select(User.email).where(
                        User.email.isnot(None),
                        User.role.in_(("root", "super_admin")),
                        User.is_active == True,          # noqa: E712
                    )
                )).scalars().all()
                recipients = []
                for addr in rows:
                    addr = (addr or "").strip()
                    if addr and addr not in recipients:
                        recipients.append(addr)
                # The owner is the one who can act; admins are the fallback
                # for an account nobody owns yet.
                if owner_to:
                    recipients = owner_to
                to = recipients[0] if recipients else None

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
                    + fw_line +
                    "\n\nبرای ادامه، وارد پنل شوید و در بخش «اسکرپر» کد پیامک‌شده را "
                    "وارد کنید. تا آن زمان اسکرپ متوقف می‌ماند و آگهی‌ها بدون "
                    "شمارهٔ تماس ذخیره نمی‌شوند."
                )
                # notification() returns (subject, html, text) — the same shape
                # every template in that module uses.
                subj, html, text = email_templates.notification(
                    subject, body,
                    cta_label="ورود به پنل", cta_url="https://sorinflow.com/dashboard/")
                targets = recipients if recipients else [to]
                sent = []
                for addr in targets:
                    try:
                        await email_service.send(addr, subj, html, text, db=db)
                        sent.append(addr)
                    except Exception as se:
                        logger.warning(f"[otp] could not email {addr}: {se}")
                logger.warning(f"[otp] emailed {', '.join(sent) or 'nobody'}: a Divar code is needed")
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

    # Deliberately broad: Divar's markup moves, and a challenge that goes
    # undetected costs a phone number. Which field it is, is decided after.
    _MODAL_INPUT_SELECTORS = (
        'input[name="code"]',
        'input[inputmode="numeric"]',
        'input[maxlength="6"]',
        '.kt-new-modal input',
        '[role="dialog"] input',
    )

    # The sentence above the field, when the field itself says nothing useful.
    _CODE_WORDS = ("کد تایید", "کد تأیید", "کد ورود", "کد پیامک", "رقمی",
                   "کدی که", "verification code", "one-time")
    _PHONE_WORDS = ("شماره موبایل", "شمارهٔ موبایل", "شماره همراه",
                    "شمارهٔ همراه", "شماره تلفن", "شماره خود")

    # login_with_phone's list, which is known to move Divar past this screen.
    _CONFIRM_WORDS = ("تأیید", "تایید", "بعدی", "ادامه", "ورود", "ارسال",
                      "confirm", "next", "submit", "send")

    async def _find_modal_input(self):
        """The visible field of whatever modal is up, or None."""
        # Instant query_selector — NOT wait_for_selector (avoids N×3s delays)
        for sel in self._MODAL_INPUT_SELECTORS:
            try:
                el = await self.page.query_selector(sel)
                if el and await el.is_visible():
                    placeholder = (await el.get_attribute('placeholder') or '').lower()
                    if 'search' in placeholder or 'جستجو' in placeholder:
                        continue
                    logger.info(f"Divar SMS-OTP input detected: {sel}")
                    return el
            except Exception:
                continue
        return None

    async def _modal_step(self, el, modal_text: str) -> str:
        """«phone» while Divar is still asking who is calling, else «code».

        Conservative by design. «code» is the behaviour this handler has always
        had, so an unfamiliar modal keeps it; only a positive phone signal
        switches. Being wrong the other way would break a working path.
        """
        attrs = await self._input_attrs(el)
        soup = " ".join(f"{k}={v}" for k, v in attrs.items()).lower()
        raw_max = attrs.get("maxlength", "")
        maxlen = int(raw_max) if raw_max.isdigit() else None

        # A field named for what it holds settles it outright.
        if any(w in soup for w in ("code", "otp", "کد", "one-time")):
            return "code"
        if any(w in soup for w in ("موبایل", "شماره", "همراه", "mobile", "phone")):
            return "phone"
        # Failing a name: a box that cannot hold eleven digits is not holding
        # an Iranian mobile number.
        if maxlen is not None:
            return "code" if maxlen <= 8 else "phone"

        # Failing that, the sentence above the box. Code words are tested
        # first because the code screen names the number it just texted, so it
        # contains «شماره» too and testing for that first would misread it.
        head = modal_text[:400]
        if any(w in head for w in self._CODE_WORDS):
            return "code"
        if any(w in head for w in self._PHONE_WORDS):
            return "phone"
        return "code"

    async def _click_confirm(self) -> bool:
        """Press whatever this modal calls «next». True if something was clicked."""
        try:
            for el in await self.page.query_selector_all(
                    'button, [role="button"]'):
                try:
                    if not await el.is_visible() or not await el.is_enabled():
                        continue
                    text = ((await el.inner_text()) or "").strip()
                    if not text or not any(w in text for w in self._CONFIRM_WORDS):
                        continue
                    await el.click(force=True, timeout=3000)
                    logger.info(f"[otp] pressed {text!r}")
                    return True
                except Exception:
                    continue
        except Exception as e:
            logger.debug(f"[otp] confirm-button lookup failed: {e}")
        try:
            btn = await self.page.query_selector('button[type="submit"]')
            if btn and await btn.is_visible():
                await btn.click(force=True, timeout=3000)
                logger.info("[otp] pressed the modal's submit button")
                return True
        except Exception:
            pass
        return False

    async def _submit_phone_step(self, el):
        """Answer «which number?» so Divar will send a code. Returns the code
        field it then shows, or None.

        Divar's contact-reveal challenge starts one screen earlier than this
        handler assumed. Before there is a code there is a question about who
        is asking, and nothing is sent until it is answered — so parking here
        is waiting for an SMS nobody requested. That is precisely what the
        scraper did: it found an input in a dialog, called it the code box, and
        waited five minutes for a message Divar had never been asked to send.

        The two moves are login_with_phone's, which are known to work.
        """
        phone = (self.account_phone or "").strip()
        if not phone:
            logger.warning(
                "[otp] Divar is asking which number to text and this extractor "
                "was not told which account it is driving — cannot answer")
            return None

        logger.info(
            f"[otp] Divar wants the account number before it will send anything "
            f"— entering {phone[:4]}*****{phone[-2:]}")
        try:
            await el.click()
            await el.fill("")
            for ch in phone:
                await el.type(ch, delay=random.uniform(60, 140))
            await asyncio.sleep(0.6)
        except Exception as e:
            logger.warning(f"[otp] could not enter the account number: {e}")
            return None

        if not await self._click_confirm():
            logger.warning(
                "[otp] the number is typed but no button on this modal would "
                "send it — the code cannot arrive")
            return None

        # Divar swaps the field rather than the page, so wait for the box to
        # become a code box.
        for _ in range(20):
            await asyncio.sleep(0.5)
            nxt = await self._find_modal_input()
            if nxt is None:
                continue
            if await self._modal_step(nxt, await self._modal_text()) == "code":
                logger.info("[otp] Divar moved to the code screen — the SMS is on its way")
                return nxt

        logger.warning(
            f"[otp] the number went in but no code field followed; the modal "
            f"now says: {(await self._modal_text())[:200]!r}")
        return None

    async def _handle_sms_otp_if_present(self) -> None:
        """Detect Divar's SMS OTP verification for contact info, wait for user code."""
        try:
            otp_input = await self._find_modal_input()

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

            # Not a code prompt at all: Divar asking who this account IS.
            #
            # An operator hit it by hand — «دیوار ازش احراز هویت با کدملی
            # خواست». It looks like every other challenge from a selector's
            # point of view (a dialog with an input) and it is nothing like
            # one: no SMS is coming, no code will satisfy it, and a run that
            # parks here waits five minutes for nothing while the account
            # stays unusable. Recognise it by its words, record it, hand the
            # account back, and put it in front of a person.
            if self._identity_wall(modal_text):
                await self._report_identity_wall(modal_text)
                return

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

            if await self._modal_step(otp_input, modal_text) == "phone":
                otp_input = await self._submit_phone_step(otp_input)
                if otp_input is None:
                    # No code is coming. Parking for five minutes would only
                    # stall the job; on_challenge above has already arranged
                    # for this account to be swapped out.
                    return
            else:
                # A modal can be on screen without Divar having sent anything —
                # the previous code was consumed, or the first send was
                # rate-limited and the form is sitting there with a «ارسال
                # مجدد» control. Waiting on that is waiting for an SMS nobody
                # asked for. (Skipped after a phone step: Divar has just sent
                # one, and asking again would only trip its rate limit.)
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
                _resend_after = int(getattr(settings, "otp_resend_after_seconds", 90) or 0)
                _auto_resends = 0
                _next_auto = float(_resend_after) if _resend_after > 0 else float("inf")
                while waited < timeout:
                    # No code after otp_resend_after_seconds: press Divar's
                    # «ارسال مجدد» ourselves, at most twice per challenge.
                    # Divar accepts a contact code for ~120s, so a first SMS the
                    # carrier lost is worth asking again for well inside that
                    # window. The request's clock restarts so the inbound
                    # endpoint's staleness check measures against THIS send —
                    # a late first code must not land on a fresh resend. The
                    # total wait is not extended: `waited` keeps counting.
                    if waited >= _next_auto and _auto_resends < 2:
                        _auto_resends += 1
                        _next_auto = waited + _resend_after
                        sent = await self._request_otp_resend()
                        otp_store.restart_clock(self.otp_key)
                        logger.info(
                            f"no code after {int(waited)}s — automatic resend "
                            f"{_auto_resends}/2 "
                            + ("(Divar's control clicked)" if sent else "(no control on the page)"))
                    # Did the operator press «ارسال دوباره»? Only this loop can
                    # act on it: Divar's resend control lives on the page the
                    # browser is parked on, and nothing outside can reach it.
                    if otp_store.take_resend(self.otp_key):
                        sent = await self._request_otp_resend()
                        otp_store.restart_clock(self.otp_key)
                        waited = 0.0
                        _notified = False
                        logger.info(
                            "OTP resend requested from the panel — "
                            + ("Divar's control was clicked" if sent
                               else "no control on the page; Divar may still resend on its own"))
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
            # SMS sent -> typed here, when a forwarder told us the send time.
            # The one number a forwarder is judged by; carrier delivery is
            # inside it, and only the phone->server hop is ours to fix.
            _sent_at = otp_store.pop_sent_stamp(self.otp_key)
            if _sent_at:
                try:
                    from app import metrics as _mx
                    _delivery = max(time.time() - _sent_at, 0.0)
                    _mx.otp_delivery_seconds.observe(_delivery)
                    logger.info(f"[otp] forwarded code typed {_delivery:.1f}s after Divar sent it")
                except Exception:
                    pass

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

    CAPTCHA_KEEP = 40      # puzzles kept on disk; a pod is not a photo album

    def _keep_failed_captcha(self) -> None:
        """Keep the pictures of a puzzle the solver got wrong.

        The three files carry fixed names, so the next attempt overwrites them
        and a failure leaves nothing to tune against — the only reason the last
        run could be read at all is that nothing ran after it. A failed attempt
        is copied aside with the numbers it produced: the hole it picked, the
        piece's box, and how far it actually slid.
        """
        info = getattr(self, "_last_captcha", None)
        if not info:
            return
        self._last_captcha = None
        try:
            box = self.images_dir.parent / "debug" / "captcha"
            out = box / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            out.mkdir(parents=True, exist_ok=True)
            for key in ("bg", "gap", "result"):
                src = Path(info[key])
                if src.exists():
                    shutil.copyfile(src, out / f"{key}.png")
            meta = {k: v for k, v in info.items() if k not in ("bg", "gap", "result")}
            meta["account"] = self.account_phone
            (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1),
                                           encoding="utf-8")
            for old_dir in sorted(box.iterdir(), reverse=True)[self.CAPTCHA_KEEP:]:
                shutil.rmtree(old_dir, ignore_errors=True)
            logger.info(f"Kept the failed puzzle in {out.name}")
        except Exception as e:
            logger.debug(f"could not keep the failed captcha: {e}")

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

            # Both boxes, in screen pixels. The background's tells us how the
            # screenshot scales; the piece's tells the solver which row the
            # hole is on and where the journey starts.
            bg_box = await bg_element.bounding_box()
            gap_box = await gap_element.bounding_box()
            bg_screen_w = bg_box["width"] if bg_box else None

            scale = 1.0
            try:
                import cv2 as _cv2
                _bg_img = _cv2.imread(str(bg_path))
                if _bg_img is not None and bg_screen_w and _bg_img.shape[1] > 0:
                    scale = bg_screen_w / _bg_img.shape[1]
            except Exception:
                pass

            piece_box = None
            if bg_box and gap_box and scale:
                piece_box = ((gap_box["x"] - bg_box["x"]) / scale,
                             (gap_box["y"] - bg_box["y"]) / scale,
                             gap_box["width"] / scale,
                             gap_box["height"] / scale)

            solver = PuzzleCaptchaSolver(
                gap_image_path=str(gap_path),
                bg_image_path=str(bg_path),
                output_image_path=str(result_path),
                piece_box=piece_box,
            )
            # Run synchronous OpenCV work in a thread pool to avoid blocking the event loop
            loop = asyncio.get_event_loop()
            hole_x = await loop.run_in_executor(None, solver.discern)
            travel = solver.slide_distance()
            logger.info(f"PuzzleCaptchaSolver hole x={hole_x} travel={travel} scale={scale:.2f}")

            if travel is not None:
                position = travel * scale
            elif hole_x is not None:
                # No piece box to measure against; the hole's own x is the best
                # guess left, as it was before the piece's row was known.
                position = hole_x * scale
            else:
                position = (bg_screen_w * 0.40) if bg_screen_w else 80.0
                logger.warning(f"Captcha solver failed, using fallback position: {position:.1f}px")

            # A hole sitting on top of the piece is a misread, not a puzzle.
            if bg_screen_w and position < bg_screen_w * 0.08:
                logger.warning(f"Position {position:.1f} looks too small, using 40% fallback")
                position = bg_screen_w * 0.40

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
            piece_x0 = gap_box["x"] if gap_box else None

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

            # The handle and the piece need not move one pixel for one: the
            # track and the picture can be different widths, and that ratio is
            # not written anywhere. So before letting go, read how far the
            # piece actually went and finish the journey.
            moved_extra = 0.0
            if piece_x0 is not None and travel is not None:
                try:
                    now = await gap_element.bounding_box()
                    moved = (now["x"] - piece_x0) if now else 0.0
                    want = travel * scale
                    if moved > want * 0.3:          # the piece tracks the handle
                        residual = want - moved
                        if 1.0 <= abs(residual) <= 120.0:
                            for i in range(6):
                                await self.page.mouse.move(
                                    start_x + drag_distance + residual * (i + 1) / 6.0,
                                    start_y + random.uniform(-0.5, 0.5),
                                )
                                await asyncio.sleep(random.uniform(0.02, 0.05))
                            moved_extra = residual
                            logger.info(
                                f"Piece moved {moved:.1f}px of {want:.1f}px — corrected by {residual:+.1f}px")
                except Exception as e:
                    logger.debug(f"could not measure the piece mid-drag: {e}")

            await self.page.mouse.up()
            await asyncio.sleep(2.5)
            logger.info(f"Captcha slider dragged {drag_distance + moved_extra:.1f}px")

            # What the solver saw and what it decided, kept for the copy a
            # failed attempt leaves behind.
            self._last_captcha = {
                "bg": str(bg_path), "gap": str(gap_path), "result": str(result_path),
                "hole": solver.hole, "piece_box": piece_box, "scale": round(scale, 3),
                "travel": travel, "dragged": round(drag_distance + moved_extra, 1),
            }

        except Exception as e:
            logger.warning(f"Error handling captcha: {e}")
