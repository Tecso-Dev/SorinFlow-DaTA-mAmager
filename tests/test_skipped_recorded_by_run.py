"""
Every candidate the run does not save gets written down with its link.

Three ways a listing goes unsaved, and all three have to be covered or the
list in the panel is a half-list — which is worse than none, because it looks
complete:

    a filter said no        →  reason = the filter's own bucket
    the category did not match →  reason = "category"
    the page would not open / would not save / threw  →  reason = "failed"

A duplicate is deliberately NOT in here: it was saved, on an earlier run.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_skrun.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRAPER = open(os.path.join(ROOT, "app/scraper/divar_scraper.py"),
               encoding="utf-8-sig").read()


def between(start, end):
    """The code from one landmark to the next.

    Not a fixed character window: those measure whatever comment happens to
    sit in them, and have twice now failed on prose rather than behaviour.
    """
    i = SCRAPER.index(start)
    return SCRAPER[i:SCRAPER.index(end, i + len(start))]


class TestAllThreeRoutesAreCovered:
    def test_a_filtered_listing_is_recorded(self):
        assert "skipped_listings.record(" in between("if skip:", "maybe_rotate_account")

    def test_an_off_category_listing_is_recorded(self):
        branch = between('skip_tally["category"]', "# Progress is listings PROCESSED")
        assert "skipped_listings.record(" in branch
        assert 'reason="category"' in branch

    def test_a_failed_detail_scrape_is_recorded(self):
        branch = between("elif detail is None:", "elif detail is False:")
        assert "skipped_listings.record(" in branch
        assert 'reason="failed"' in branch

    def test_a_failed_save_is_recorded(self):
        assert "skipped_listings.record(" in \
            between('fail_tally["ذخیره نشد"]', "elif detail is None:")

    def test_a_listing_that_threw_is_recorded(self):
        assert "skipped_listings.record(" in \
            between("Failed to process listing", "# Complete job")

    def test_there_is_one_record_call_per_unsaved_route(self):
        assert SCRAPER.count("skipped_listings.record(") == 5

    def test_a_duplicate_is_not_recorded_as_unsaved(self):
        """It was saved — on an earlier run."""
        assert "skipped_listings.record(" not in \
            between("job.updated_items += 1", "Close the read transaction")


class TestTheRowCanBeRetried:
    def test_every_call_carries_the_divar_id(self):
        for chunk in SCRAPER.split("skipped_listings.record(")[1:]:
            assert "divar_id=" in chunk[:400]

    def test_every_call_carries_the_url(self):
        for chunk in SCRAPER.split("skipped_listings.record(")[1:]:
            assert "url=listing" in chunk[:400]

    def test_every_call_carries_a_reason(self):
        for chunk in SCRAPER.split("skipped_listings.record(")[1:]:
            assert "reason=" in chunk[:400]


class TestTheFilterReasonSurvivesTheSyncBoundary:
    def test_skip_stays_synchronous(self):
        """It is called from a dozen branches; making it async would put an
        await on every one and lose the first one anybody forgets."""
        assert "async def _skip" not in SCRAPER
        assert "return True" in between("def _skip(reason: str) -> bool:",
                                        "skip = False")

    def test_it_remembers_what_it_decided(self):
        assert '_why["bucket"], _why["detail"] = bucket, reason' in \
            between("def _skip(reason: str) -> bool:", "skip = False")

    def test_the_row_uses_that_bucket(self):
        assert '_why.get("bucket"' in between("if skip:", "maybe_rotate_account")

    def test_it_is_reset_for_each_listing(self):
        """Declared inside the loop body, so one listing's reason cannot be
        pinned on the next."""
        decl = SCRAPER.index('_why: Dict[str, str] = {}')
        loop = SCRAPER.index("for i, listing in enumerate(all_listings):")
        assert decl > loop


class TestItIsTidiedUpLikeTheJobLog:
    def test_old_rows_are_pruned_at_the_start_of_a_run(self):
        assert "await skipped_listings.prune()" in SCRAPER

    def test_it_prunes_beside_the_job_log(self):
        i = SCRAPER.index("await job_log.prune()")
        assert "skipped_listings.prune()" in SCRAPER[i:i + 200]
