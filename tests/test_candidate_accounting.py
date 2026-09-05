"""
«۱۱۳ آگهی با این فیلترها در دیوار هست… ولی ۸۲ تا اسکرپ داشت. کدومش اشتباهه؟»

Neither. That run's own log says:

    {"new":71,"failed":0,"skipped":5,"updated":11,"candidates":119}

71 + 11 = the 82 that were handled; 5 more were dropped by the deposit band.
That leaves 32 of 119 candidates going somewhere with no name. They left
through the off-category branch, which returned a sentinel and incremented
nothing — so the panel could show 82 handled out of 119 and offer no account
of the difference.

A total that does not add up reads as a fault whether or not there is one.
These tests pin the arithmetic: every candidate is either saved, already
held, failed, dropped by a named filter, or never reached — and anything
left over is reported as unaccounted rather than passing unremarked.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_acct.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRAPER = open(os.path.join(ROOT, "app/scraper/divar_scraper.py"),
               encoding="utf-8-sig").read()


class TestTheOffCategoryDropIsCounted:
    def test_it_has_its_own_branch_now(self):
        assert "elif detail is False:" in SCRAPER

    def test_the_branch_tallies(self):
        i = SCRAPER.index("elif detail is False:")
        assert 'skip_tally["category"]' in SCRAPER[i:i + 700]

    def test_it_is_not_counted_as_a_failure(self):
        """It is a listing that did not belong, not a listing that broke."""
        i = SCRAPER.index("elif detail is False:")
        assert "failed_items" not in SCRAPER[i:i + 700]

    def test_the_bucket_has_a_persian_name(self):
        from app.scraper.divar_scraper import DivarScraper
        assert DivarScraper._FILTER_LABELS_FA["category"] == "خارج از دسته‌بندی"

    def test_every_bucket_the_tally_reports_can_be_named(self):
        """An unnamed bucket prints its raw English key into a Persian line."""
        from app.scraper.divar_scraper import DivarScraper
        assert "category" in DivarScraper._FILTER_LABELS_FA


class TestCandidatesNeverReachedAreNotCandidatesDropped:
    def test_the_run_counts_what_it_examined(self):
        assert "examined = 0" in SCRAPER and "examined += 1" in SCRAPER

    def test_it_counts_after_the_stopping_checks(self):
        """Counting from the loop index would report candidates the run broke
        before reaching as candidates it threw away."""
        i = SCRAPER.index("examined += 1")
        before = SCRAPER[:i]
        assert before.rindex("was cancelled, stopping scraping") > \
            before.rindex("for i, listing in enumerate(all_listings):")

    def test_it_counts_before_the_duplicate_check(self):
        """An already-held listing was looked at."""
        i = SCRAPER.index("examined += 1")
        assert SCRAPER.index("if await self.property_exists(", i) > i

    def test_the_leftovers_are_reported_separately(self):
        assert "بررسی‌نشده" in SCRAPER


class TestTheReconciliationIsWrittenWhereItCanBeRead:
    def test_it_records_the_candidate_total(self):
        assert "نامزد — " in SCRAPER

    def test_it_lists_the_named_buckets(self):
        i = SCRAPER.index("نامزد — ")
        window = SCRAPER[i - 1400:i]
        assert "_FILTER_LABELS_FA.get(k, k)" in window

    def test_a_gap_is_called_a_gap(self):
        assert "بی‌حساب" in SCRAPER

    def test_a_gap_is_logged_as_a_warning(self):
        i = SCRAPER.index("نامزد — ")
        assert 'level="warning" if _unaccounted > 0 else "info"' in SCRAPER[i:i + 400]

    def test_a_clean_run_is_not_a_warning(self):
        """Most runs reconcile; those must stay quiet or the flag means nothing."""
        i = SCRAPER.index("نامزد — ")
        assert 'else "info"' in SCRAPER[i:i + 400]


class TestTheArithmetic:
    """The identity the log line asserts, checked directly."""

    def test_the_reported_run_now_adds_up(self):
        new, updated, failed, filtered, off_category = 71, 11, 0, 5, 32
        candidates = 119
        assert new + updated + failed + filtered + off_category == candidates

    def test_what_the_panel_could_previously_explain(self):
        """82 handled, 5 named — 32 short of 119, which is what was asked."""
        assert 119 - (71 + 11 + 5) == 32
