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


class TestASingleScrapeRefusesCleanly:
    SRC = inspect.getsource(sr.scrape_single_property)

    def test_initialize_s_answer_is_checked(self):
        assert "if not await scraper.initialize():" in self.SRC

    def test_a_busy_account_is_a_409_in_words(self):
        assert "status_code=409" in self.SRC
        assert "در یک اسکرپ در حال اجرا مشغول است" in self.SRC

    def test_any_other_boot_failure_is_a_503_with_the_reason(self):
        assert "status_code=503" in self.SRC
        assert "مرورگر اسکرپر بالا نیامد" in self.SRC

    def test_the_failure_message_names_which_half_failed(self):
        assert "_last_detail_error" in self.SRC and "_last_save_error" in self.SRC

    def test_initialize_records_why_it_failed(self):
        src = inspect.getsource(DivarScraper.initialize)
        assert "self._init_error = str(e)" in src


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
