"""
Divar's contact-reveal challenge has two screens, not one.

    ┌──────────────────────────┐      ┌──────────────────────────┐
    │  شماره موبایل خود را     │ ---> │  کد ۶ رقمی ارسال‌شده     │
    │  وارد کنید               │ بعدی │  به ۰۹۰۵… را وارد کنید   │
    └──────────────────────────┘      └──────────────────────────┘
       nothing has been sent yet         now an SMS is on its way

Both are «an input inside a dialog», which is all the handler used to check.
Landing on the first one, it called the phone box a code box and parked for
five minutes waiting for a message Divar had never been asked to send. The
job log said only «No OTP resend control on the page» — true, and useless:
on that screen the button says «بعدی».

The user's own account of it settles which screen it was: "من فقط اسکرپ رو
زدم، دستی نرفتم دیوار لاگین بشم" — nobody answered the first screen, so
nobody could have been sent a code.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_ops.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.scraper import contact_extractor as ce  # noqa: E402
from app.scraper.contact_extractor import ContactExtractor  # noqa: E402

# Real wording, as far as it can be reconstructed from Divar's login flow.
PHONE_SCREEN = "ورود به دیوار\nشماره موبایل خود را وارد کنید\nبعدی"
CODE_SCREEN = ("کد تایید\nکد ۶ رقمی ارسال‌شده به شماره ۰۹۰۵۳۸۳۳۰۲۶ را وارد "
               "کنید\nارسال مجدد (۰۲:۰۰)\nتأیید")


class FakeEl:
    def __init__(self, attrs=None, text="", visible=True, enabled=True):
        self._attrs = attrs or {}
        self._text = text
        self._visible = visible
        self._enabled = enabled
        self.typed = ""
        self.clicked = 0

    async def get_attribute(self, name):
        return self._attrs.get(name)

    async def inner_text(self):
        return self._text

    async def is_visible(self):
        return self._visible

    async def is_enabled(self):
        return self._enabled

    async def click(self, **kw):
        self.clicked += 1

    async def fill(self, v):
        self.typed = v

    async def type(self, ch, delay=None):
        self.typed += ch


class TwoScreenPage:
    """A dialog that becomes the code screen once its button is pressed."""

    def __init__(self, phone_attrs=None, code_attrs=None, advances=True):
        self.step = "phone"
        self.advances = advances
        self.phone_input = FakeEl(phone_attrs if phone_attrs is not None
                                  else {"type": "tel", "maxlength": "11"})
        self.code_input = FakeEl(code_attrs if code_attrs is not None
                                 else {"name": "code", "maxlength": "6"})
        self.button = FakeEl(text="بعدی")

    @property
    def _input(self):
        return self.phone_input if self.step == "phone" else self.code_input

    async def query_selector(self, sel):
        if sel in (".kt-new-modal", '[role="dialog"]', ".kt-modal"):
            return FakeEl(text=PHONE_SCREEN if self.step == "phone" else CODE_SCREEN)
        if sel in ContactExtractor._MODAL_INPUT_SELECTORS:
            return self._input if sel == '.kt-new-modal input' else None
        return None

    async def query_selector_all(self, sel):
        if self.step == "phone" and self.advances:
            # pressing it is what moves Divar on
            class Pressable(FakeEl):
                async def click(inner, **kw):
                    inner.clicked += 1
                    self.step = "code"
            b = Pressable(text="بعدی")
            self.button = b
            return [b]
        return [self.button]


def extractor(page, phone="09053833026"):
    e = ContactExtractor.__new__(ContactExtractor)
    e.page = page
    e.account_phone = phone
    return e


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    async def instant(_):
        return None
    monkeypatch.setattr(ce.asyncio, "sleep", instant)


class TestTellingTheScreensApart:
    async def step(self, attrs, text):
        return await extractor(TwoScreenPage())._modal_step(FakeEl(attrs), text)

    @pytest.mark.asyncio
    async def test_a_named_code_field_is_the_code_screen(self):
        assert await self.step({"name": "code"}, "") == "code"

    @pytest.mark.asyncio
    async def test_a_six_digit_box_is_the_code_screen(self):
        assert await self.step({"maxlength": "6"}, "") == "code"

    @pytest.mark.asyncio
    async def test_an_eleven_digit_box_is_a_phone_number(self):
        assert await self.step({"type": "tel", "maxlength": "11"}, "") == "phone"

    @pytest.mark.asyncio
    async def test_a_field_that_says_موبایل_is_a_phone_number(self):
        assert await self.step({"placeholder": "شماره موبایل"}, "") == "phone"

    @pytest.mark.asyncio
    async def test_one_time_code_autocomplete_is_the_code_screen(self):
        assert await self.step({"autocomplete": "one-time-code"}, "") == "code"

    @pytest.mark.asyncio
    async def test_an_unlabelled_box_is_read_from_the_sentence_above_it(self):
        assert await self.step({}, PHONE_SCREEN) == "phone"
        assert await self.step({}, CODE_SCREEN) == "code"

    @pytest.mark.asyncio
    async def test_the_code_screen_is_not_misread_for_naming_the_number(self):
        """«کد ۶ رقمی ارسال‌شده به شماره ۰۹۰۵…» contains «شماره». Testing for
        that before the code words would send the scraper back a screen."""
        assert await self.step({}, CODE_SCREEN) == "code"

    @pytest.mark.asyncio
    async def test_an_unrecognised_modal_keeps_the_old_behaviour(self):
        """Being wrong towards «phone» would break a path that works."""
        assert await self.step({}, "چیزی که ندیده‌ایم") == "code"


class TestAnsweringThePhoneScreen:
    @pytest.mark.asyncio
    async def test_it_enters_the_account_number(self):
        page = TwoScreenPage()
        got = await extractor(page)._submit_phone_step(page.phone_input)
        assert page.phone_input.typed == "09053833026"
        assert got is page.code_input, "it should hand back the code field"

    @pytest.mark.asyncio
    async def test_it_presses_the_button_divar_calls_بعدی(self):
        """The word the old resend list did not have, which is the whole bug."""
        page = TwoScreenPage()
        await extractor(page)._submit_phone_step(page.phone_input)
        assert page.button.clicked == 1

    @pytest.mark.asyncio
    async def test_it_gives_up_rather_than_park_when_no_button_sends_it(self):
        page = TwoScreenPage(advances=False)
        page.button = FakeEl(text="انصراف")
        assert await extractor(page)._submit_phone_step(page.phone_input) is None

    @pytest.mark.asyncio
    async def test_it_gives_up_when_no_code_field_follows(self):
        page = TwoScreenPage(advances=False)
        assert await extractor(page)._submit_phone_step(page.phone_input) is None

    @pytest.mark.asyncio
    async def test_it_cannot_answer_without_knowing_the_account(self):
        page = TwoScreenPage()
        assert await extractor(page, phone=None)._submit_phone_step(
            page.phone_input) is None
        assert page.phone_input.typed == "", "nothing should have been typed"

    @pytest.mark.asyncio
    async def test_a_field_that_will_not_take_input_is_not_submitted(self):
        class Stuck(FakeEl):
            async def fill(self, v):
                raise RuntimeError("detached")
        page = TwoScreenPage()
        assert await extractor(page)._submit_phone_step(Stuck()) is None
        assert page.step == "phone", "nothing should have been pressed"


class TestTheHandlerUsesIt:
    @pytest.fixture
    def src(self):
        import inspect
        return inspect.getsource(ContactExtractor._handle_sms_otp_if_present)

    def test_the_step_is_decided_before_the_run_parks(self, src):
        assert src.index("_modal_step(") < src.index("otp_store.request(")

    def test_the_phone_screen_is_answered(self, src):
        assert "_submit_phone_step(" in src

    def test_it_does_not_park_when_the_phone_screen_could_not_be_answered(self, src):
        i = src.index("_submit_phone_step(")
        assert "return" in src[i:i + 400]
        assert src.index("_submit_phone_step(") < src.index("otp_store.request(")

    def test_resend_is_skipped_right_after_a_send(self, src):
        """Asking Divar to resend a code it just sent is how a rate limit is
        earned."""
        i = src.index("_submit_phone_step(")
        j = src.index("_request_otp_resend()")
        assert i < j, "the resend must sit in the other branch"
        assert "else:" in src[i:j]
