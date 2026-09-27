"""
A code is typed into the box that is on the page NOW, not the one found
before the wait.

Seen live on 1405/07/04: karmand typed the code into the panel two minutes
after the prompt opened, and the handler filled the element it had found at
the start — «Error in SMS-OTP handler: Element is not attached to the DOM».
Divar had re-rendered the dialog meanwhile, and the code was thrown away.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_refound.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from _fake_redis import fake_server, redis_factory  # noqa: E402

from app.scraper import contact_extractor as ce  # noqa: E402
from app.scraper import otp_store  # noqa: E402
from app.scraper.contact_extractor import ContactExtractor  # noqa: E402

PHONE = "09053833026"


class Box:
    def __init__(self):
        self.typed = ""

    async def get_attribute(self, name):
        return {"name": "otp", "type": "tel", "maxlength": "6"}.get(name)

    async def is_visible(self):
        return True

    async def click(self, **kw):
        pass

    async def fill(self, v):
        self.typed = v

    async def press(self, key):
        pass


class Dialog:
    async def is_visible(self):
        return True

    async def inner_text(self):
        return "کد تایید به شمارهٔ ۰۹۰۵ پیامک شد"


class RerenderingPage:
    """The code box is replaced after the first lookup, as Divar's re-render
    does while the prompt waits."""

    def __init__(self):
        self.before, self.after = Box(), Box()
        self.lookups = 0

    async def query_selector_all(self, sel):
        if sel in ContactExtractor._MODAL_SELECTORS:
            return [Dialog()]
        if sel == 'input[maxlength="6"]':
            self.lookups += 1
            return [self.before if self.lookups == 1 else self.after]
        return []


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(otp_store, "get_redis", redis_factory(fake_server()))

    async def instant(_):
        return None
    monkeypatch.setattr(ce.asyncio, "sleep", instant)


@pytest.mark.asyncio
async def test_the_code_goes_into_the_box_on_screen_now():
    page = RerenderingPage()
    e = ContactExtractor(page, images_dir=None, otp_key="job1:gaXYZ", account_phone=PHONE)
    # the forwarder beat the prompt: the code is already parked
    assert await otp_store.park_early_code(PHONE, "123456")

    await e._handle_sms_otp_if_present()

    assert page.after.typed == "123456"
    assert page.before.typed == ""
