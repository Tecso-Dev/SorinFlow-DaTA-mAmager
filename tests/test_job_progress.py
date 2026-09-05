"""
«درصد پیشرفت درست کار نکرد و بعد ۱۰۰ درصد شدن باز اسکرپ کرد.»

From that run's own log:

    {"new":71,"pages":0,"failed":0,"skipped":5,"updated":11,
     "requested":100,"candidates":119}

The bar was filled by candidates examined but divided by max_items — a
target of *saved* listings. Those are different quantities, and they only
agree if every candidate is saved, which never happens. So candidate 100 of
119 read 100% and the scraper carried on for another nineteen.

The loop ends when the pool runs out or the target is met, whichever comes
first, so the pool is the honest denominator.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_prog.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRAPER = open(os.path.join(ROOT, "app/scraper/divar_scraper.py"),
               encoding="utf-8-sig").read()


def job(total, scraped):
    from app.models.scraping_job import ScrapingJob
    j = ScrapingJob()
    j.total_items = total
    j.scraped_items = scraped
    return j


class TestTheRunThatWasReported:
    """119 candidates, 100 requested — the numbers from the log above."""

    def test_the_hundredth_candidate_is_not_the_end(self):
        assert job(119, 100).progress < 100

    def test_it_reads_as_the_share_of_the_pool_it_has_walked(self):
        assert job(119, 100).progress == 84.03

    def test_the_last_candidate_fills_it(self):
        assert job(119, 119).progress == 100


class TestTheDenominatorIsThePool:
    def test_total_items_is_the_candidate_count(self):
        assert "job.total_items = len(all_listings)" in SCRAPER

    def test_the_target_is_no_longer_the_denominator(self):
        assert "job.total_items = len(all_listings) if" not in SCRAPER, \
            "the target-based branch is what filled the bar early"

    def test_progress_counts_candidates_examined(self):
        assert "job.scraped_items = i + 1" in SCRAPER

    def test_the_capped_counter_is_gone(self):
        assert "min(i + 1, max_items)" not in SCRAPER

    def test_both_write_sites_were_changed(self):
        """One is the filtered-out branch, one the processed branch. Leaving
        either behind would make the bar jump between them."""
        assert SCRAPER.count("job.scraped_items = i + 1") == 2

    def test_the_two_modes_no_longer_need_telling_apart(self):
        """pool_progress existed only to pick a denominator."""
        assert "pool_progress" not in SCRAPER


class TestARunThatStopsAtItsTargetStillReadsFull:
    def test_completion_fills_the_bar(self):
        src = SCRAPER[SCRAPER.index('job.status = "completed"'):]
        assert "job.scraped_items = job.total_items" in src[:600]

    def test_it_is_set_before_the_row_is_committed(self):
        i = SCRAPER.index('job.status = "completed"')
        window = SCRAPER[i:i + 600]
        assert window.index("job.scraped_items = job.total_items") < \
            window.index("await self.db_session.commit()")


class TestTheBarCannotOverfill:
    def test_an_empty_pool_does_not_divide_by_zero(self):
        assert job(0, 0).progress == 0

    def test_walking_the_whole_pool_is_exactly_full(self):
        assert job(42, 42).progress == 100
