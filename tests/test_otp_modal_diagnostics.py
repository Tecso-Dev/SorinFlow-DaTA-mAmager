"""
The OTP modal is reached headless, inside a container. Nobody can look at it.

Every attempt to fix it so far has been a guess about markup nobody has seen,
and the guesses have been wrong. These tests pin the one thing that ends that:
whatever Divar puts on the screen gets written to the job log, before the run
parks for five minutes waiting on it.
"""
import inspect
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_od.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.scraper.contact_extractor import ContactExtractor  # noqa: E402


class FakeEl:
    def __init__(self, attrs=None, text="", visible=True):
        self._attrs = attrs or {}
        self._text = text
        self._visible = visible

    async def get_attribute(self, name):
        return self._attrs.get(name)

    async def inner_text(self):
        return self._text

    async def is_visible(self):
        return self._visible


class FakePage:
    """Answers query_selector from a dict of selector -> element."""

    def __init__(self, elements=None):
        self.elements = elements or {}

    async def query_selector(self, sel):
        return self.elements.get(sel)


def extractor(page):
    e = ContactExtractor.__new__(ContactExtractor)
    e.page = page
    return e


class TestModalText:
    @pytest.mark.asyncio
    async def test_it_reads_the_dialog_on_screen(self):
        page = FakePage({".kt-new-modal": FakeEl(text="  کد تایید را وارد کنید  ")})
        assert await extractor(page)._modal_text() == "کد تایید را وارد کنید"

    @pytest.mark.asyncio
    async def test_it_falls_through_to_the_next_dialog_shape(self):
        """Divar's markup is not stable; the fallbacks are the point."""
        page = FakePage({'[role="dialog"]': FakeEl(text="شماره موبایل")})
        assert await extractor(page)._modal_text() == "شماره موبایل"

    @pytest.mark.asyncio
    async def test_an_invisible_dialog_is_not_the_one_on_screen(self):
        page = FakePage({".kt-new-modal": FakeEl(text="قدیمی", visible=False)})
        assert await extractor(page)._modal_text() == ""

    @pytest.mark.asyncio
    async def test_no_dialog_is_empty_not_an_error(self):
        assert await extractor(FakePage())._modal_text() == ""

    @pytest.mark.asyncio
    async def test_a_raising_page_still_returns_a_string(self):
        class Exploding:
            async def query_selector(self, sel):
                raise RuntimeError("page closed")
        assert await extractor(Exploding())._modal_text() == ""


class TestInputAttrs:
    @pytest.mark.asyncio
    async def test_it_reports_what_the_field_is_for(self):
        el = FakeEl({"name": "code", "maxlength": "6", "inputmode": "numeric"})
        got = await extractor(FakePage())._input_attrs(el)
        assert got == {"name": "code", "maxlength": "6", "inputmode": "numeric"}

    @pytest.mark.asyncio
    async def test_absent_attributes_are_left_out_rather_than_logged_as_none(self):
        """A log full of name=None says less than a short one."""
        assert await extractor(FakePage())._input_attrs(FakeEl()) == {}

    @pytest.mark.asyncio
    async def test_one_unreadable_attribute_does_not_lose_the_others(self):
        class Partial(FakeEl):
            async def get_attribute(self, name):
                if name == "placeholder":
                    raise RuntimeError("detached")
                return self._attrs.get(name)
        got = await extractor(FakePage())._input_attrs(Partial({"name": "code"}))
        assert got == {"name": "code"}


class TestItIsLoggedBeforeTheRunParks:
    @pytest.fixture
    def src(self):
        return inspect.getsource(ContactExtractor._handle_sms_otp_if_present)

    def test_the_modal_is_described_at_all(self, src):
        assert "_modal_text()" in src and "_input_attrs(" in src

    def test_it_is_described_before_the_wait(self, src):
        """A description that arrives after a five-minute park is a description
        of a screen the reader has already given up on."""
        assert src.index("_modal_text()") < src.index("otp_store.request(")

    def test_the_words_themselves_are_logged_not_a_summary(self, src):
        i = src.index("_modal_text()")
        assert "modal says" in src[i:i + 400]


class TestTheCodeAlertReachesEveryAdmin:
    """It reached one. `.limit(1)` with no ORDER BY picked whichever admin
    Postgres returned first — a developer — and the owner, who enters the
    codes, sat unaware while a run stayed paused for fifty minutes."""

    def test_no_limit_one_on_the_recipient_query(self):
        import inspect, re
        from app.scraper.contact_extractor import ContactExtractor
        src = inspect.getsource(ContactExtractor._notify_code_needed)
        # Code only: the comment explaining the fix names the old call too.
        code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
        assert ".limit(1)" not in code
        assert ".scalars().all()" in code

    def test_every_recipient_is_sent_to(self):
        import inspect
        from app.scraper.contact_extractor import ContactExtractor
        src = inspect.getsource(ContactExtractor._notify_code_needed)
        assert "for addr in targets:" in src
        assert "email_service.send(addr," in src


class TestThePanelsDeadlineIsTheBrowsersDeadline:
    """The panel said «مهلت این کد تمام شد — اسکرپر بدون این شماره ادامه داد»
    five minutes in, while the browser was parked for another five hours and
    fifty-five. The entry was still in _store and a code would still have been
    accepted; the operator was told it was too late and handed a disabled
    button, so the run stayed paused because of the message rather than
    because of Divar."""

    def test_the_window_follows_wait_for_human(self, monkeypatch):
        from app.scraper import otp_store
        from app.config import get_settings
        cfg = get_settings()
        monkeypatch.setattr(cfg, "otp_wait_timeout", 300, raising=False)
        monkeypatch.setattr(cfg, "otp_wait_for_human", True, raising=False)
        monkeypatch.setattr(cfg, "otp_wait_max_seconds", 21600, raising=False)
        assert otp_store.wait_window() == 21600

    def test_without_wait_for_human_it_is_the_plain_timeout(self, monkeypatch):
        from app.scraper import otp_store
        from app.config import get_settings
        cfg = get_settings()
        monkeypatch.setattr(cfg, "otp_wait_timeout", 300, raising=False)
        monkeypatch.setattr(cfg, "otp_wait_for_human", False, raising=False)
        assert otp_store.wait_window() == 300

    def test_it_is_the_same_expression_the_extractor_uses(self):
        """Two places deriving one deadline is what broke it."""
        import inspect
        from app.scraper import otp_store
        from app.scraper.contact_extractor import ContactExtractor
        ext = inspect.getsource(ContactExtractor._handle_sms_otp_if_present)
        win = inspect.getsource(otp_store.wait_window)
        for token in ("otp_wait_for_human", "otp_wait_max_seconds", "max("):
            assert token in ext and token in win, f"{token} is in only one of the two"


class TestAskingDivarForAnotherCode:
    """The only way to get a second code was to let the prompt fail and wait
    for the scraper to reach the next listing."""

    def setup_method(self):
        from app.scraper import otp_store
        otp_store._store.clear()

    def test_a_resend_is_flagged_then_consumed_once(self):
        from app.scraper import otp_store
        otp_store.request("j:1", "0912")
        assert otp_store.ask_resend("j:1")["ok"] is True
        assert otp_store.take_resend("j:1") is True
        assert otp_store.take_resend("j:1") is False, "one press, one resend"

    def test_it_is_capped(self):
        from app.scraper import otp_store
        otp_store.request("j:1", "0912")
        for _ in range(otp_store.MAX_RESENDS):
            assert otp_store.ask_resend("j:1")["ok"] is True
            otp_store.take_resend("j:1")
        out = otp_store.ask_resend("j:1")
        assert out["ok"] is False and out["reason"] == "limit"

    def test_a_closed_request_cannot_be_resent(self):
        from app.scraper import otp_store
        otp_store.request("j:1", "0912")
        otp_store.submit("j:1", "123456")
        assert otp_store.ask_resend("j:1")["ok"] is False
        assert otp_store.ask_resend("nope:1")["ok"] is False

    def test_the_clock_restarts_so_the_countdown_matches_the_new_code(self):
        import time
        from app.scraper import otp_store
        otp_store.request("j:1", "0912")
        otp_store._store["j:1"]["ts"] = time.time() - 120
        otp_store.restart_clock("j:1")
        assert time.time() - otp_store._store["j:1"]["ts"] < 2

    def test_only_the_parked_browser_can_press_it(self):
        """Divar's resend control is on the page the browser is sitting on."""
        import inspect
        from app.scraper.contact_extractor import ContactExtractor
        src = inspect.getsource(ContactExtractor._handle_sms_otp_if_present)
        assert "take_resend" in src and "_request_otp_resend" in src
        assert "restart_clock" in src

    def test_the_panel_has_the_button_and_the_route(self):
        html = open("frontend/index.html", encoding="utf-8").read()
        js = open("frontend/js/app.js", encoding="utf-8").read()
        api = open("app/api/routes/scraper.py", encoding="utf-8").read()
        assert 'id="otp2-resend"' in html and "ارسال دوباره کد" in html
        assert "function resendDivarOtp" in js and "/resend" in js
        assert '@router.post("/otp/{key}/resend")' in api
