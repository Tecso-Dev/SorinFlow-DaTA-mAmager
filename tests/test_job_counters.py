"""
«بررسی / کل» says what this run did, and Divar's own number sits beside it (#29).

Job 44 read «251 / 251»: «کل» was Divar's count for the filters — which
ignores the day, because Divar does not filter by day — and at the end
«بررسی» was set equal to it. The run had collected 24 candidates and 8 of
them were from that day. Job 43 read «77 / 77» with 21 of its candidates
dropped by the date filter, and nothing on the row said so.

Now:
  «بررسی»  the listings this run actually examined
  «کل»     the size of this run's own candidate pool
  divar_count  what Divar said, kept apart for «دیوار می‌گوید»
and the bar of a finished run stays full.

Every run here goes through the real loop on Postgres with the browser
replaced (tests/_scrape_harness.py).
"""
import os
import sys
from datetime import date, datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

import _scrape_harness as h  # noqa: E402

from app.models.scraping_job import ScrapingJob  # noqa: E402

TEHRAN = timezone(timedelta(hours=3, minutes=30))


def noon_utc(day: date) -> datetime:
    """Midday in Tehran on `day`, as the naive UTC the page parser returns."""
    return datetime(day.year, day.month, day.day, 8, 30)


@pytest.fixture
async def db(monkeypatch):
    h.quiet(monkeypatch, divar_says=251)
    eng, maker = await h.open_db()
    yield maker
    await eng.dispose()


class TestTheDayRunThatReadTwoFiftyOne:
    """Job 44's shape: Divar says 251, the day's feed gave 24, 8 were that day."""

    async def _run(self, db, watch=None):
        day = (datetime.now(TEHRAN) - timedelta(days=1)).date()
        on_day = [h.token() for _ in range(8)]
        other_day = [h.token() for _ in range(16)]
        pages = {t: h.page(t, phone=h.PHONE.format(i), posted=noon_utc(day))
                 for i, t in enumerate(on_day)}
        pages.update({t: h.page(t, phone=h.PHONE.format(100 + i), posted=noon_utc(day - timedelta(days=1)))
                      for i, t in enumerate(other_day)})
        feed = [t for pair in zip(other_day[:8], on_day, strict=True) for t in pair] + other_day[8:]
        job_id = await h.new_job(db, posted_date=day.isoformat())

        async def on_open(tok):
            if watch is not None:
                await watch(job_id, tok)
        job, s = await h.run(db, job_id, {"feed": feed, "pages": pages, "on_open": on_open})
        return job, s, on_day

    async def test_kol_is_the_pool_and_barresi_what_was_examined(self, db):
        job, _, _ = await self._run(db)
        assert job.status == "completed"
        assert job.total_items == 24, "«کل» is Divar's count again, not this run's pool"
        assert job.scraped_items == 24
        assert job.new_items == 8

    async def test_divars_number_is_kept_apart(self, db):
        job, _, _ = await self._run(db)
        assert job.divar_count == 251
        assert job.total_items != job.divar_count

    async def test_the_bar_of_the_finished_run_is_full(self, db):
        job, _, _ = await self._run(db)
        assert job.progress == 100.0

    async def test_the_other_days_cost_no_reveal(self, db):
        """Unchanged, but it is why 16 of 24 never became «تازه»."""
        job, s, on_day = await self._run(db)
        assert sorted(s.revealed) == sorted(on_day)

    async def test_halfway_through_the_pool_the_bar_reads_about_half(self, db):
        """Against 251 the bar crept to 9% and then jumped to 100%."""
        seen = []

        async def watch(job_id, _tok):
            row = await h.job_row(db, job_id)
            seen.append((row.scraped_items, row.total_items, row.progress))

        await self._run(db, watch=watch)
        scraped, total, progress = seen[12]          # opening the 13th candidate
        assert total == 24
        assert scraped == 12, "the listings already examined"
        assert progress == 50.0


class TestARunThatMetItsTarget:
    async def test_it_does_not_claim_the_whole_pool(self, db):
        """Asked for 2, got them from the first 2 of 10: «2 / 10», not «10 / 10»."""
        feed = [h.token() for _ in range(10)]
        pages = {t: h.page(t, phone=h.PHONE.format(i)) for i, t in enumerate(feed)}
        job_id = await h.new_job(db, max_items=2)
        job, s = await h.run(db, job_id, {"feed": feed, "pages": pages})
        assert job.status == "completed" and job.new_items == 2
        assert s.opened == feed[:2]
        assert (job.scraped_items, job.total_items) == (2, 10)
        assert job.progress == 100.0, "a finished run reads full"


class TestAlreadyHeldListingsAreExamined:
    async def test_a_duplicate_counts_as_examined_while_the_run_is_going(self, db):
        """The duplicate branch moved on without touching «بررسی», so a run
        walking stored listings stood still until the next new one."""
        held = [h.token() for _ in range(3)]
        fresh = h.token()
        for i, t in enumerate(held):
            await h.stored(db, t, phone=h.PHONE.format(200 + i))
        feed = held + [fresh]
        seen = {}
        job_id = await h.new_job(db, max_items=5)

        async def watch(tok):
            seen[tok] = (await h.job_row(db, job_id)).scraped_items

        job, s = await h.run(db, job_id, {"feed": feed, "on_open": watch,
                                          "pages": {fresh: h.page(fresh, phone=h.PHONE.format(299))}})
        assert s.opened == [fresh], "a complete stored listing is not opened again"
        assert seen[fresh] == 3, "three already-held listings were examined before it"
        assert (job.scraped_items, job.total_items) == (4, 4)


class TestEveryCandidateAddsUp:
    async def test_a_listing_that_threw_is_counted_failed_and_examined(self, db):
        """The failure was counted before a rollback that threw the count
        away: the log said «1 ناموفق (RuntimeError: 1)», the row said 0."""
        a, bad, c = h.token(), h.token(), h.token()
        job_id = await h.new_job(db, max_items=5)
        job, _ = await h.run(db, job_id, {"feed": [a, bad, c], "pages": {
            a: h.page(a, phone=h.PHONE.format(501)), bad: h.RAISE,
            c: h.page(c, phone=h.PHONE.format(502))}})
        assert (job.new_items, job.failed_items) == (2, 1)
        assert (job.scraped_items, job.total_items) == (3, 3)
        tally = next(m for m in await h.log_lines(db, job_id) if "نامزد —" in m)
        assert "1 ناموفق" in tally and "بی‌حساب" not in tally

    async def test_every_kind_of_candidate_lands_in_one_bucket(self, db):
        """new + بروز + تکراری + failed + filtered + gone = examined."""
        new, held, gap, lost, gone, other = (h.token() for _ in range(6))
        await h.stored(db, held, phone=h.PHONE.format(503))
        await h.stored(db, gap)
        job_id = await h.new_job(db, max_items=10)
        job, _ = await h.run(db, job_id, {"feed": [new, held, gap, lost, gone, other], "pages": {
            new: h.page(new, phone=h.PHONE.format(504)),
            gap: h.page(gap, phone=h.PHONE.format(505)),
            lost: h.page(lost), gone: h.GONE, other: False}})
        assert (job.new_items, job.updated_items, job.failed_items) == (1, 1, 1)
        assert (job.scraped_items, job.total_items) == (6, 6)
        tally = next(m for m in await h.log_lines(db, job_id) if "نامزد —" in m)
        for part in ("1 تازه", "1 بروز", "1 تکراری", "1 ناموفق", "1 خارج از دسته‌بندی",
                     "1 در دیوار حذف شده"):
            assert part in tally, (part, tally)
        assert "بی‌حساب" not in tally and "بررسی‌نشده" not in tally


class TestTheModel:
    @staticmethod
    def job(**kw):
        j = ScrapingJob()
        j.status = kw.pop("status", "running")
        for k, v in kw.items():
            setattr(j, k, v)
        return j

    def test_divars_count_is_not_the_denominator(self):
        j = self.job(total_items=24, scraped_items=6, divar_count=251)
        assert j.progress == 25.0

    def test_a_completed_run_is_full_even_with_an_empty_pool(self):
        """A day with nothing in it: 0 of 0, and the run is over."""
        assert self.job(status="completed", total_items=0, scraped_items=0).progress == 100.0

    def test_a_cancelled_run_shows_where_it_stopped(self):
        assert self.job(status="cancelled", total_items=40, scraped_items=10).progress == 25.0

    def test_the_nearer_end_still_wins_for_a_requested_number(self):
        j = self.job(total_items=424, scraped_items=182, new_items=176, config={"max_items": 200})
        assert j.progress == 88.0

    def test_it_never_reads_past_full(self):
        assert self.job(total_items=10, scraped_items=12).progress == 100.0
