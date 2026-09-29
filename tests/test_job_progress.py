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
import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_prog.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRAPER = open(os.path.join(ROOT, "app/scraper/divar_scraper.py"),
               encoding="utf-8-sig").read()

import _scrape_harness as h  # noqa: E402


@pytest.fixture
async def db(monkeypatch):
    h.quiet(monkeypatch, divar_says=113)
    eng, maker = await h.open_db()
    yield maker
    await eng.dispose()


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
    """Read off real runs (tests/_scrape_harness.py): the loop, the counters
    and the row as it is committed, with the browser replaced."""

    async def test_the_candidate_pool_is_the_denominator(self, db):
        """Divar's own count was the denominator for a while; #29 put the pool
        back, because Divar's count ignores the day. It is kept beside it."""
        feed = [h.token() for _ in range(5)]
        job_id = await h.new_job(db, max_items=10)
        job, _ = await h.run(db, job_id, {"feed": feed, "pages": {
            t: h.page(t, phone=h.PHONE.format(700 + i)) for i, t in enumerate(feed)}})
        assert (job.total_items, job.divar_count) == (5, 113)

    def test_the_target_is_no_longer_the_denominator(self):
        assert "job.total_items = len(all_listings) if" not in SCRAPER, \
            "the target-based branch is what filled the bar early"

    async def test_progress_counts_candidates_examined(self, db):
        """Filtered or saved, a listing the run is done with moves the bar —
        one at a time, never both at once and never neither."""
        old = datetime.utcnow() - timedelta(days=3)
        feed = [h.token() for _ in range(4)]
        pages = {feed[0]: h.page(feed[0], posted=old),
                 feed[1]: h.page(feed[1], phone=h.PHONE.format(710)),
                 feed[2]: h.page(feed[2], posted=old),
                 feed[3]: h.page(feed[3], phone=h.PHONE.format(711))}
        job_id = await h.new_job(db, max_items=10)
        seen = []

        async def watch(_tok):
            seen.append((await h.job_row(db, job_id)).scraped_items)
        job, _ = await h.run(db, job_id, {"feed": feed, "pages": pages, "on_open": watch},
                             max_age_hours=24)
        assert seen == [0, 1, 2, 3], "the filtered branch and the saved branch both advance it"
        assert job.scraped_items == 4 and job.new_items == 2

    def test_the_capped_counter_is_gone(self):
        assert "min(i + 1, max_items)" not in SCRAPER

    def test_the_two_modes_no_longer_need_telling_apart(self):
        """pool_progress existed only to pick a denominator."""
        assert "pool_progress" not in SCRAPER


class TestARunThatStopsAtItsTargetStillReadsFull:
    async def test_completion_fills_the_bar_as_committed(self, db):
        """Asked for one, it stops after the first of three. The bar reads
        full on the row as committed — and «بررسی» still says one, not three."""
        feed = [h.token() for _ in range(3)]
        job_id = await h.new_job(db, max_items=1)
        await h.run(db, job_id, {"feed": feed, "pages": {
            t: h.page(t, phone=h.PHONE.format(720 + i)) for i, t in enumerate(feed)}})
        row = await h.job_row(db, job_id)
        assert row.status == "completed" and row.progress == 100.0
        assert (row.scraped_items, row.total_items) == (1, 3)


class TestTheBarCannotOverfill:
    def test_an_empty_pool_does_not_divide_by_zero(self):
        assert job(0, 0).progress == 0

    def test_walking_the_whole_pool_is_exactly_full(self):
        assert job(42, 42).progress == 100
