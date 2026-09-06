"""
«۳۲ خارج از دسته‌بندی» does not say whether that is right.

Two runs in a row reported exactly 32. That is either the guard working as
designed — Divar injects promoted and related ads into a result page, and
the run collected 148 candidates where Divar's own count said 120 — or it is
thirty-two real apartments being thrown away. The count cannot tell them
apart. A name can.

So the run keeps a handful of what it dropped, in words, and writes them to
the job log where they can be read without a shell on the server.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_cds.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRAPER = open(os.path.join(ROOT, "app/scraper/divar_scraper.py"),
               encoding="utf-8-sig").read()

from app.scraper.divar_scraper import DivarScraper  # noqa: E402

DETAIL = inspect.getsource(DivarScraper.scrape_property_detail)


class TestTheDropIsNamed:
    def test_the_detail_scrape_records_what_it_dropped(self):
        assert "self._last_category_drop" in DETAIL

    def test_it_names_what_divar_called_the_ad(self):
        """The breadcrumb leaf is Divar's own word for the category, which is
        the thing the reader needs in order to judge the drop."""
        i = DETAIL.index("self._last_category_drop")
        assert "leaf" in DETAIL[i:i + 200]
        assert "source_title or decoded_url" in DETAIL[i:i + 200]

    def test_it_is_recorded_before_the_listing_is_abandoned(self):
        i = DETAIL.index("self._last_category_drop")
        assert i < DETAIL.index("return False  # sentinel: category skip")

    def test_it_is_bounded(self):
        """A log line is not the place for a whole page title."""
        i = DETAIL.index("self._last_category_drop")
        assert "[:80]" in DETAIL[i:i + 200]


class TestTheRunKeepsAFew:
    def test_there_is_a_list(self):
        assert "category_drops: List[str] = []" in SCRAPER

    def test_it_is_capped(self):
        """Thirty-two names is not a log line, it is a wall."""
        assert "len(category_drops) < 6" in SCRAPER

    def test_it_is_filled_where_the_drop_is_counted(self):
        i = SCRAPER.index('skip_tally["category"]')
        assert "category_drops.append" in SCRAPER[i:i + 400]

    def test_a_missing_name_does_not_append_nothing(self):
        i = SCRAPER.index('skip_tally["category"]')
        assert "if _what and" in SCRAPER[i:i + 400]


class TestItReachesTheLog:
    def test_the_samples_are_recorded(self):
        assert "نمونه‌ای از آگهی‌هایی که خارج از دسته‌بندی شمرده شدند" in SCRAPER

    def test_only_when_there_are_any(self):
        assert "if category_drops:" in SCRAPER

    def test_it_goes_to_the_job_log_not_only_the_container_log(self):
        i = SCRAPER.index("نمونه‌ای از آگهی‌هایی")
        assert "job_log.record" in SCRAPER[i - 300:i]

    def test_it_is_separate_from_the_reconciliation_line(self):
        """One line counts, the other shows. Merging them would bury both."""
        assert SCRAPER.index("نامزد — ") < SCRAPER.index("نمونه‌ای از آگهی‌هایی")
