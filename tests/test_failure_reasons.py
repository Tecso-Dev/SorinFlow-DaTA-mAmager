"""
«۳ ناموفق» is a number nobody can act on.

The reconciliation now balances — 148 candidates = 14 saved + 97 already
held + 3 failed + 32 off-category + 2 filtered — but «failed» was still a
bare count. A page Divar bounced us off is a different problem from a page
that threw an exception, and only one of them is worth chasing.

Every failure now carries its reason into the same kind of tally the filters
already have.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_fail.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRAPER = open(os.path.join(ROOT, "app/scraper/divar_scraper.py"),
               encoding="utf-8-sig").read()

from app.scraper.divar_scraper import DivarScraper  # noqa: E402

DETAIL = inspect.getsource(DivarScraper.scrape_property_detail)


class TestTheDetailScrapeSaysWhyItGaveUp:
    def test_a_bounced_page_is_named(self):
        assert 'self._last_detail_error = "صفحه باز نشد"' in DETAIL

    def test_a_non_property_page_is_named(self):
        assert 'self._last_detail_error = "ملک نبود"' in DETAIL

    def test_an_exception_carries_its_type(self):
        assert 'self._last_detail_error = f"{type(e).__name__}"' in DETAIL

    def test_it_is_cleared_before_each_attempt(self):
        """Otherwise the previous listing's reason is attributed to this one."""
        assert DETAIL.index("self._last_detail_error = None") < DETAIL.index("try:")

    def test_every_none_return_now_has_a_reason(self):
        """A return with no reason set would be reported as «نامعلوم» forever."""
        reasons = DETAIL.count("self._last_detail_error =")
        assert reasons == 4, "one clear + three reasons; a path was added or lost"


class TestTheRunTalliesThem:
    def test_there_is_a_tally(self):
        assert "fail_tally: Dict[str, int] = {}" in SCRAPER

    def _none_branch(self):
        """Bounded by the next branch, not by a character count — a fixed
        window measures whichever comment happens to fall inside it."""
        i = SCRAPER.index("elif detail is None:")
        return SCRAPER[i:SCRAPER.index("elif detail is False:", i)]

    def test_a_scrape_error_goes_in_it(self):
        assert "fail_tally[_reason]" in self._none_branch()

    def test_a_missing_reason_is_called_unknown_rather_than_dropped(self):
        assert '"نامعلوم"' in self._none_branch()

    def test_a_failed_save_is_its_own_reason(self):
        """Grouped by what the database said, so five of one problem read as
        five of one problem."""
        assert "fail_tally[_save_why]" in SCRAPER
        assert '"ذخیره نشد"' in SCRAPER

    def test_a_raised_listing_is_recorded_by_exception_type(self):
        assert "fail_tally[type(e).__name__]" in SCRAPER

    def test_every_place_that_increments_failed_items_also_tallies(self):
        """A count and a tally that disagree are worse than the count alone."""
        assert SCRAPER.count("job.failed_items += 1") == 3
        assert SCRAPER.count("fail_tally[") == 3, \
            "a failure site was added without a reason, or one lost its tally"


class TestItReachesTheReconciliation:
    def _report(self):
        i = SCRAPER.index("if job.failed_items:\n                _named")
        return SCRAPER[i:i + 500]

    def test_the_reasons_are_named_in_the_line(self):
        assert "fail_tally.items()" in self._report()
        assert '{job.failed_items} ناموفق' in self._report()

    def test_the_biggest_reason_comes_first(self):
        assert "key=lambda kv: -kv[1]" in self._report()

    def test_an_empty_tally_leaves_the_bare_count(self):
        """Belt and braces: a failure whose reason went missing must not print
        an empty pair of brackets."""
        assert 'if _named else ""' in self._report()

    def test_a_run_with_no_failures_adds_nothing(self):
        assert 'if job.failed_items:' in SCRAPER
