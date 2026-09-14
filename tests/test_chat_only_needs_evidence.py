"""
«فقط چت» is a permanent verdict and needs Divar to say it.

Sobhan checked a listing by hand that the panel had written off as chat-only.
Divar showed ۰۹۰۳۲۰۲۳۱۰۰ on the page. Twenty listings were marked that way in
one run, and property_exists refuses to re-scrape them — so they were lost
until somebody noticed.

The cause: the detector accepted the PRESENCE OF A CHAT CONTROL as proof.
Every Divar page has one — «چت و تماس» is in the site header on every listing
— so the moment «اطلاعات تماس» was missing for any reason, the page matched.
What was actually missing was the account's identity verification: Divar had
restricted it and hidden the contact button.
"""
import inspect
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_co.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.scraper.contact_extractor import ContactExtractor  # noqa: E402


def _ex():
    e = ContactExtractor.__new__(ContactExtractor)
    e.contact_channel = None
    e.needs_identity = False
    return e


async def _chat(text):
    return await _ex()._chat_only(text)


async def _ident(text):
    return await _ex()._needs_identity(text)


class TestPresenceOfChatProvesNothing:

    def test_no_selector_based_detection_remains(self):
        """The whole mechanism that produced the false positives.

        Code only: the comment that explains the removal names the selectors
        it removed, and a bare substring check cannot tell a rule from its
        own obituary."""
        src = inspect.getsource(ContactExtractor)
        code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
        assert "_CHAT_SELECTORS" not in code
        assert 'a[href*="/chat/"]' not in code
        assert "query_selector" not in code.split("_HIDDEN_WORDS")[1].split("async def _request_otp_resend")[0]

    @pytest.mark.asyncio
    async def test_a_page_with_a_chat_button_is_not_chat_only(self):
        """Every listing page has «چت و تماس» in the header."""
        assert not await _chat("چت و تماس\nاطلاعات تماس\nمغازه ۳۰ متری")

    @pytest.mark.asyncio
    async def test_a_restricted_account_page_is_not_chat_only(self):
        """The real failure: contact button gone, chat link still in the
        header, number sitting on the page."""
        page = "دیوار من\nچت و تماس\nتایید هویت\nهویت خود را تایید کنید\nکد ملی"
        assert not await _chat(page)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("said", [
        "شماره مخفی است", "فقط از طریق چت", "امکان تماس تلفنی وجود ندارد",
        "تنها از طریق چت با آگهی‌دهنده",
    ])
    async def test_divar_saying_it_is_believed(self, said):
        assert await _chat(f"مغازه ۳۰ متری\n{said}\nچت")

    @pytest.mark.asyncio
    async def test_an_empty_page_is_not_a_verdict(self):
        """A page that failed to render must not condemn a listing forever."""
        assert not await _chat("")


class TestTheIdentityDemandIsRecognised:

    @pytest.mark.asyncio
    @pytest.mark.parametrize("page", [
        "هویت خود را تایید کنید\nکد ملی خود را وارد کنید",
        "احراز هویت\nبرای دیدن اطلاعات تماس",
        "تأیید هویت\nکد ملی",
    ])
    async def test_a_demand_is_detected(self, page):
        assert await _ident(page)

    @pytest.mark.asyncio
    async def test_the_menu_item_alone_is_not_a_demand(self):
        """«تایید هویت» is a menu entry on every logged-in page — including
        one where everything is working."""
        page = "دیوار من\nدیوار حرفه‌ای\nتایید هویت\nآگهی‌های من\nاطلاعات تماس"
        assert not await _ident(page)

    @pytest.mark.asyncio
    async def test_it_is_checked_before_chat_only(self):
        """Order matters: a restricted page could otherwise be written off as
        the poster's choice, which is permanent."""
        src = inspect.getsource(ContactExtractor.get_phone_number)
        assert src.index("_needs_identity") < src.index("_chat_only")


class TestItIsTreatedAsOursAndTemporary:

    def test_it_is_not_recorded_as_chat_only(self):
        from app.scraper.divar_scraper import DivarScraper
        src = inspect.getsource(DivarScraper.start_scraping_job)
        blk = src[src.index('_ch == "needs_identity"'):]
        blk = blk[:blk.index('elif _ch == "chat_only"')]
        assert 'reason="needs_identity"' in blk
        assert "job.failed_items += 1" in blk, "it is ours, so it counts as a failure"

    def test_such_a_row_is_still_retried(self):
        """property_exists only refuses to re-scrape chat_only."""
        from app.scraper.divar_scraper import DivarScraper
        src = inspect.getsource(DivarScraper.property_exists)
        code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
        assert '== "chat_only"' in code
        assert "needs_identity" not in code, "identity blocks would become permanent"

    def test_the_owner_is_told_how_to_fix_it(self):
        from app.scraper.divar_scraper import DivarScraper
        src = inspect.getsource(DivarScraper._report_identity_block)
        assert "identity-confirmation" in src
        assert "کد ملی" in src

    def test_it_is_said_once_per_run(self):
        """Every listing hits the same wall; twenty identical emails is the
        same as none."""
        from app.scraper.divar_scraper import DivarScraper
        src = inspect.getsource(DivarScraper._report_identity_block)
        assert "_identity_reported" in src

    def test_the_panel_has_a_label(self):
        from app.scraper.divar_scraper import DivarScraper
        assert DivarScraper._FILTER_LABELS_FA["needs_identity"] == "نیاز به تأیید هویت دیوار"
