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
