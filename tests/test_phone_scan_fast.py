"""
The phone scan after «اطلاعات تماس» is one lookup for every selector, a few
times a second apart. It was a wait_for_selector per selector — 13 × 800 ms
× 3 rounds plus pauses, 36 s on every listing that shows no number, the
chat-only ones included (measured on job 37, 1405/07/04).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_scan.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

import pytest  # noqa: E402

from app.scraper import contact_extractor as ce  # noqa: E402
from app.scraper.contact_extractor import ContactExtractor  # noqa: E402


class Link:
    def __init__(self, href="", text="", visible=True):
        self.href, self.text, self.visible = href, text, visible

    async def is_visible(self):
        return self.visible

    async def get_attribute(self, name):
        return self.href if name == "href" else None

    async def inner_text(self):
        return self.text


class Page:
    def __init__(self, elems=(), html=""):
        self.elems, self.html, self.asked = list(elems), html, []

    async def content(self):
        return self.html

    async def query_selector_all(self, sel):
        self.asked.append(sel)
        return self.elems

    async def wait_for_selector(self, *a, **kw):
        raise AssertionError("the scan must not wait selector by selector")


@pytest.fixture
def slept(monkeypatch):
    got = []

    async def fake(s):
        got.append(s)
    monkeypatch.setattr(ce.asyncio, "sleep", fake)
    return got


def extractor(page):
    e = ContactExtractor.__new__(ContactExtractor)
    e.page = page
    return e


@pytest.mark.asyncio
async def test_a_revealed_number_is_read_at_once(slept):
    page = Page([Link(visible=False), Link("tel:09141234567")])
    assert await extractor(page)._scan_for_phone(lambda p: False) == "09141234567"
    assert slept == [] and len(page.asked) == 1


@pytest.mark.asyncio
async def test_the_accounts_own_number_is_not_the_listings(slept):
    page = Page([Link("tel:09058432452"), Link("tel:09141234567")])
    got = await extractor(page)._scan_for_phone(lambda p: p.endswith("9058432452"))
    assert got == "09141234567"


@pytest.mark.asyncio
async def test_no_number_costs_seconds_not_half_a_minute(slept):
    page = Page([], html="<div>فقط چت دیوار</div>")
    assert await extractor(page)._scan_for_phone(lambda p: False) is None
    assert sum(slept) <= 4 and len(page.asked) == 4
