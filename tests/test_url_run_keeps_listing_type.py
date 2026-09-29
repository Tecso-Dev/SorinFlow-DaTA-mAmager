"""
A list-of-URLs run (single scrape, re-scrape) labels its listings «اسکرپ تکی»,
which is not a category. The page's own breadcrumb says buy or rent, and that is
the truth: overwriting it with the label's «unknown» turned a re-scraped sale
into a row the panel shows as a rental.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_ulk.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

import _divar_pages as dp  # noqa: E402

pytestmark = pytest.mark.filterwarnings("ignore:The 'strip_cdata' option")


@pytest.mark.parametrize("name,kind", [
    ("buy-apartment-owner.html", "buy"),
    ("rent-apartment-owner.html", "rent"),
])
async def test_a_url_run_keeps_what_the_page_says(monkeypatch, name, kind):
    run = dp.make_runner(monkeypatch)
    r = await run([name], "اسکرپ تکی")
    assert r.stored[0]["listing_type"] == kind
