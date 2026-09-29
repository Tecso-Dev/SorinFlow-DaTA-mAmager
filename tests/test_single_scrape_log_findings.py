"""
Three defects, one container log.

    09:24:41  Contact button clicked successfully
    09:24:46  ERROR Failed to initialize scraper: profile … is already open
              in this process — two jobs cannot share one account's profile
    09:24:46  Scraping property detail: https://divar.ir/v/gag-D-FT
    09:24:46  Rate limit reached. Waiting 59.1 seconds...
    09:24:51  No tel: link found in page content after click
    09:24:51  Modal detected on page
    09:25:28  No phone element found after clicking contact button
    09:25:45  ERROR Failed to scrape property detail: 'NoneType' object has
              no attribute 'goto'

1. initialize() returned False and the single scrape went on without a
   page. 2. A brand-new scraper judged its first request against a rate
   projected from a tenth of a second, and slept a minute. 3. A notice
   modal with no input stood between the click and the number, and the
   only wording ever dismissed was «متوجه شدم».
"""
import inspect
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_ssl.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

import pytest  # noqa: E402
from app.api.routes import scraper as sr  # noqa: E402
from app.scraper import divar_scraper as ds  # noqa: E402
from app.scraper.divar_scraper import DivarScraper  # noqa: E402
from app.scraper.contact_extractor import ContactExtractor  # noqa: E402


# Fixtures and stand-ins only — no Test* names, so nothing is collected twice.
from test_run_start_reasons import (  # noqa: E402,F401
    _NotStarting, _pending, _run, _row_and_log, finish, world)


class TestARunWhoseBrowserDidNotComeUpFailsInWords:
    """The single scrape is a job of one now, so this lives in the job path —
    which had the same bug: «Browser initialization incomplete, continuing
    anyway...» continued with no page. Every other way a run fails to start
    is in test_run_start_reasons.py."""

    async def test_initialize_s_answer_stops_the_run(self, world, finish, monkeypatch):  # noqa: F811
        routes, _swept = finish
        went_on = []

        async def _went_on(self, **_kw):
            went_on.append(True)
        monkeypatch.setattr(_NotStarting, "start_scraping_job", _went_on)
        monkeypatch.setattr(_NotStarting, "error", "Browser closed.")
        eng, maker = world.engine()
        try:
            await world.tables(eng)
            job_id = await _pending(world, maker)
            await _run(routes, job_id)
            assert went_on == [], "the run went on with no page"
            row, _lines = await _row_and_log(maker, job_id)
            assert row.status == "failed"
        finally:
            await world.cleanup(maker)
            await eng.dispose()

    async def test_a_busy_account_is_named_as_such(self):
        s = DivarScraper.__new__(DivarScraper)
        s.db_session = None                      # nobody to ask which run has it
        said = await s._explain_open_failure(
            RuntimeError("profile /app/data/profiles/09990000001 is already open in this process"),
            "09990000001")
        assert "09990000001" in said and "در اسکرپ دیگری" in said and "«ادامه» را بزنید" in said
        assert "مرورگر اسکرپر بالا نیامد" not in said, "a busy number is not a broken browser"

    def test_any_other_boot_failure_carries_the_reason(self):
        for err in (RuntimeError("BrowserType.launch_persistent_context: Browser closed."),
                    TimeoutError(), ""):
            said = ds.browser_failure_reason(err)
            assert said.startswith("مرورگر اسکرپر بالا نیامد") and "نامشخص" not in said
        assert "Browser closed" in ds.browser_failure_reason(RuntimeError("Browser closed."))

    async def test_it_is_written_to_the_run_log_and_the_finish_line(self, world, finish,  # noqa: F811
                                                                      monkeypatch):
        routes, _swept = finish
        monkeypatch.setattr(_NotStarting, "error", "Browser closed.")
        eng, maker = world.engine()
        try:
            await world.tables(eng)
            job_id = await _pending(world, maker)
            await _run(routes, job_id)
            row, lines = await _row_and_log(maker, job_id)
            assert row.finish_reason and row.finish_reason.startswith("مرورگر اسکرپر بالا نیامد")
            assert ("error", "error", row.finish_reason) in lines
        finally:
            await world.cleanup(maker)
            await eng.dispose()

    async def test_initialize_records_why_it_failed(self, monkeypatch):
        class _NoChromium:
            async def start(self):
                raise RuntimeError("Executable doesn't exist at /ms-playwright/chromium/chrome")
        monkeypatch.setattr(ds, "async_playwright", lambda: _NoChromium())
        s = DivarScraper(db_session=None, proxy_enabled=False, headless=True)
        assert await s.initialize(phone_number=None) is False
        assert "Executable doesn't exist" in s._init_error
        assert "پیدا نشد" in s._init_reason


class TestTheRateLimiterInItsFirstMinute:
    def _scraper(self, count, seconds_ago):
        s = DivarScraper.__new__(DivarScraper)
        s.request_count = count
        s.session_start = datetime.now() - timedelta(seconds=seconds_ago)
        s._cooldown_until = 0.0
        s.stealth_config = type("C", (), {"max_requests_per_minute": 20,
                                          "max_requests_per_session": 1000})()
        s._memory_fraction = lambda: 0.0
        return s

    @pytest.mark.asyncio
    async def test_the_first_request_of_a_fresh_instance_does_not_sleep(self, monkeypatch):
        slept = []
        async def _s(d):
            slept.append(d)
        monkeypatch.setattr(ds.asyncio, "sleep", _s)
        s = self._scraper(count=1, seconds_ago=0.1)
        await s._check_rate_limit()
        assert slept == [], "a tenth of a second is not a minute's rate"

    @pytest.mark.asyncio
    async def test_the_count_still_caps_the_first_minute(self, monkeypatch):
        slept = []
        async def _s(d):
            slept.append(d)
        monkeypatch.setattr(ds.asyncio, "sleep", _s)
        s = self._scraper(count=21, seconds_ago=30)
        await s._check_rate_limit()
        assert slept and slept[0] > 0

    @pytest.mark.asyncio
    async def test_the_rate_applies_once_there_is_a_minute_to_measure(self, monkeypatch):
        slept = []
        async def _s(d):
            slept.append(d)
        monkeypatch.setattr(ds.asyncio, "sleep", _s)
        s = self._scraper(count=50, seconds_ago=90)      # 33 rpm
        await s._check_rate_limit()
        assert slept

    def test_the_reason_is_in_the_code(self):
        src = inspect.getsource(DivarScraper._check_rate_limit)
        assert "600 rpm" in src


class FakeEl:
    def __init__(self, text="", visible=True, has_input=False, buttons=()):
        self._t, self._v, self._i, self._b = text, visible, has_input, list(buttons)
        self.clicked = 0
    async def is_visible(self):
        return self._v
    async def inner_text(self):
        return self._t
    async def query_selector(self, sel):
        return FakeEl() if (sel == 'input' and self._i) else None
    async def query_selector_all(self, sel):
        return self._b
    async def click(self, **kw):
        self.clicked += 1


class FakePage:
    def __init__(self, modal):
        self._m = modal
    async def query_selector(self, sel):
        return self._m if sel == '.kt-new-modal' else None

    async def query_selector_all(self, sel):     # how dialogs are looked up now
        return [self._m] if (sel == '.kt-new-modal' and self._m) else []


def extractor(modal):
    e = ContactExtractor.__new__(ContactExtractor)
    e.page = FakePage(modal)
    return e


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    async def _s(d):
        return None
    monkeypatch.setattr("app.scraper.contact_extractor.asyncio.sleep", _s)


class TestTheNoticeIsAcknowledged:
    @pytest.mark.asyncio
    async def test_a_wording_other_than_متوجه_شدم_is_pressed(self):
        ok = FakeEl("تأیید")
        modal = FakeEl("زنگ خطرهای قبل از معامله …", buttons=[FakeEl("انصراف"), ok])
        assert await extractor(modal)._acknowledge_notice() is True
        assert ok.clicked == 1

    @pytest.mark.asyncio
    async def test_the_old_wording_still_works(self):
        ok = FakeEl("متوجه شدم")
        modal = FakeEl("نسخهٔ وب دیوار", buttons=[ok])
        assert await extractor(modal)._acknowledge_notice() is True
        assert ok.clicked == 1

    @pytest.mark.asyncio
    async def test_a_lone_button_is_the_button_whatever_it_says(self):
        ok = FakeEl("برو بریم")
        modal = FakeEl("نکته", buttons=[ok])
        assert await extractor(modal)._acknowledge_notice() is True
        assert ok.clicked == 1

    @pytest.mark.asyncio
    async def test_the_code_prompt_is_never_clicked_through(self):
        """A modal with an input belongs to the OTP handler."""
        ok = FakeEl("تأیید")
        modal = FakeEl("کد تأیید", has_input=True, buttons=[ok])
        assert await extractor(modal)._acknowledge_notice() is False
        assert ok.clicked == 0

    @pytest.mark.asyncio
    async def test_no_modal_is_a_no_op(self):
        assert await extractor(None)._acknowledge_notice() is False

    @pytest.mark.asyncio
    async def test_two_unknown_buttons_are_left_alone_and_logged(self):
        """Guessing between «حذف» and «گزارش» is how a listing gets reported."""
        a, b = FakeEl("حذف"), FakeEl("گزارش")
        modal = FakeEl("چه کار کنیم؟", buttons=[a, b])
        assert await extractor(modal)._acknowledge_notice() is False
        assert a.clicked == 0 and b.clicked == 0

    def test_the_words_are_logged_before_the_click(self):
        src = inspect.getsource(ContactExtractor._acknowledge_notice)
        assert src.index("modal after contact click — says") < src.index("acknowledged via")

    def test_it_replaces_the_one_wording_dismissal(self):
        src = inspect.getsource(ContactExtractor.get_phone_number)
        assert "await self._acknowledge_notice()" in src
        assert "Dismissing PWA info modal" not in src
