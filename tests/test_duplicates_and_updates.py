"""
One meaning for «تکراری» and one for «بروز», everywhere (#32).

Job 43's log said «۳ آگهی از قبل در پایگاه داده بود و دوباره ذخیره نشد», its
«بروز» column said 3, and its finish line said «0 تکراری». One counter,
updated_items, was carrying two different things — a stored listing the run
skipped because it was already complete, and a stored listing without a
number that the run opened again and saved over — and each line named it
differently.

Now:
  «تکراری»  already stored with its number (or chat-only): not opened, nothing written
  «بروز»    already stored, opened again by this run and saved over with a number
and the log, the finish line and the table all use those two words for
those two numbers.

And «ذخیره شد ولی شمارهٔ تماس گرفته نشد — در اجرای بعدی دوباره تلاش می‌شود»
was a promise nothing kept: the next run only reached such a listing if
Divar's feed happened to hand it over again, and a daily run for another
day never did. The next run of the same city and category, by the same
person, now opens it first.

Real loop, real Postgres, browser replaced (tests/_scrape_harness.py).
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

import _scrape_harness as h  # noqa: E402

TEHRAN = timezone(timedelta(hours=3, minutes=30))


@pytest.fixture
async def db(monkeypatch):
    h.quiet(monkeypatch)
    eng, maker = await h.open_db()
    yield maker
    await eng.dispose()


async def _view(maker, job_id):
    """The job as GET /api/scraper/jobs/{id} returns it."""
    from app.api.routes.scraper import get_scraping_job
    async with maker() as s:
        return await get_scraping_job(job_id, db=s)


class TestOneMeaningEach:
    async def _run(self, db):
        held, gap, fresh = h.token(), h.token(), h.token()
        await h.stored(db, held, phone=h.PHONE.format(301))     # complete: a duplicate
        await h.stored(db, gap)                                  # no number: worth a visit
        job_id = await h.new_job(db, max_items=5)
        job, s = await h.run(db, job_id, {
            "feed": [held, gap, fresh],
            "pages": {gap: h.page(gap, phone=h.PHONE.format(302)),
                      fresh: h.page(fresh, phone=h.PHONE.format(303))}})
        return job_id, job, s, (held, gap, fresh)

    async def test_the_complete_one_is_a_duplicate_and_is_not_opened(self, db):
        job_id, job, s, (held, gap, fresh) = await self._run(db)
        assert held not in s.opened
        assert s.opened == [gap, fresh]

    async def test_the_column_counts_only_what_was_saved_over(self, db):
        """«بروز» is the listing the run opened and re-saved — not the one it
        skipped."""
        job_id, job, _, _ = await self._run(db)
        assert (job.new_items, job.updated_items) == (1, 1)

    async def test_the_gap_got_its_number(self, db):
        _, _, _, (_, gap, _) = await self._run(db)
        assert (await h.property_row(db, gap)).phone_number == h.PHONE.format(302)

    async def test_every_line_of_the_log_uses_the_same_words_for_the_same_numbers(self, db):
        job_id, job, _, _ = await self._run(db)
        lines = await h.log_lines(db, job_id)
        finish = next(m for m in lines if m.startswith("اسکرپ تمام شد"))
        assert "1 تازه" in finish and "1 بروز" in finish and "1 تکراری" in finish
        tally = next(m for m in lines if "نامزد —" in m)
        assert "1 تازه" in tally and "1 بروز" in tally and "1 تکراری" in tally
        assert "بی‌حساب" not in tally, "the three buckets must add up to the three candidates"
        # the sentence that used to be about updated_items is about duplicates
        not_again = [m for m in lines if "دوباره باز نشد" in m or "دوباره ذخیره نشد" in m]
        assert not_again and all(m.startswith("1 ") for m in not_again)
        assert not any("2 آگهی" in m for m in lines), "the two were summed somewhere"

    async def test_the_table_reason_line_uses_them_too(self, db):
        job_id, _, _, _ = await self._run(db)
        view = await _view(db, job_id)
        assert view.new_items == 1 and view.updated_items == 1
        assert "1 تکراری" in view.reason_line and "1 بروز" in view.reason_line


class TestANumberlessListingIsRetriedByTheNextRun:
    """First run: saved without a number. Second run of the same city and
    category: Divar's feed no longer has it — and it is opened anyway."""

    async def _first(self, db, **cfg):
        lost = h.token()
        job_id = await h.new_job(db, max_items=3, **cfg)
        job, s = await h.run(db, job_id, {"feed": [lost], "pages": {lost: h.page(lost)}})
        assert job.failed_items == 1 and job.new_items == 0
        return lost

    async def test_the_first_run_says_what_will_happen(self, db):
        lost = await self._first(db)
        (row,) = await h.skipped_rows(db, divar_id=lost)
        assert row.reason == "no_phone"
        assert "همین شهر و دسته" in row.detail, row.detail

    async def test_the_next_run_opens_it_and_gets_the_number(self, db):
        lost = await self._first(db)
        other = h.token()
        job_id = await h.new_job(db, max_items=3)
        job, s = await h.run(db, job_id, {
            "feed": [other],
            "pages": {lost: h.page(lost, phone=h.PHONE.format(401)),
                      other: h.page(other, phone=h.PHONE.format(402))}})
        assert lost in s.opened, "the listing was never tried again"
        assert (await h.property_row(db, lost)).phone_number == h.PHONE.format(401)
        assert (job.new_items, job.updated_items) == (1, 1)
        assert await h.skipped_rows(db, divar_id=lost) == [], \
            "recovered, so it is no longer on anyone's list"

    async def test_a_day_run_for_another_day_still_retries_it(self, db):
        """The daily schedule: its date filter would drop yesterday's listing
        before the reveal. A retry is judged by the run that saved it."""
        lost = await self._first(db)
        day = (datetime.now(TEHRAN) - timedelta(days=3)).date()
        job_id = await h.new_job(db, posted_date=day.isoformat())
        job, s = await h.run(db, job_id, {
            "feed": [], "pages": {lost: h.page(lost, phone=h.PHONE.format(403))}})
        assert lost in s.revealed
        assert job.updated_items == 1
        assert (await h.property_row(db, lost)).phone_number == h.PHONE.format(403)

    async def test_the_run_log_says_it_is_retrying(self, db):
        lost = await self._first(db)
        job_id = await h.new_job(db, max_items=3)
        await h.run(db, job_id, {"feed": [], "pages": {lost: h.page(lost, phone=h.PHONE.format(404))}})
        lines = await h.log_lines(db, job_id)
        assert any("بدون شماره" in m and "دوباره" in m and m.startswith("1 ") for m in lines), lines

    async def test_another_category_does_not_touch_it(self, db):
        lost = await self._first(db)
        job_id = await h.new_job(db, max_items=3, category="buy-apartment")
        _, s = await h.run(db, job_id, {"feed": [], "pages": {}})
        assert lost not in s.opened

    async def test_another_persons_run_does_not_spend_its_reveals_on_it(self, db):
        lost = await self._first(db)
        job_id = await h.new_job(db, max_items=3, owner_user_id=h.OWNER + 1)
        _, s = await h.run(db, job_id, {"feed": [], "pages": {}})
        assert lost not in s.opened

    async def test_three_misses_and_it_is_left_alone(self, db):
        """A number that never shows (a hidden or virtual one) must not cost a
        reveal on every run forever."""
        lost = await self._first(db)
        for _ in range(2):
            job_id = await h.new_job(db, max_items=3)
            _, s = await h.run(db, job_id, {"feed": [], "pages": {lost: h.page(lost)}})
            assert lost in s.opened
        job_id = await h.new_job(db, max_items=3)
        _, s = await h.run(db, job_id, {"feed": [], "pages": {lost: h.page(lost)}})
        assert lost not in s.opened
        assert len(await h.skipped_rows(db, divar_id=lost)) == 3

    async def test_a_chat_only_listing_is_not_retried(self, db):
        """The poster hid the number; no run will ever find one."""
        quiet_one = h.token()
        job_id = await h.new_job(db, max_items=3)
        await h.run(db, job_id, {"feed": [quiet_one],
                                 "pages": {quiet_one: h.page(quiet_one, channel="chat_only")}})
        job_id = await h.new_job(db, max_items=3)
        _, s = await h.run(db, job_id, {"feed": [], "pages": {}})
        assert quiet_one not in s.opened

    async def test_a_listing_deleted_from_the_table_is_not_brought_back(self, db):
        lost = await self._first(db)
        from sqlalchemy import delete
        from app.models.property import Property
        async with db() as s:
            await s.execute(delete(Property).where(Property.divar_id == lost))
            await s.commit()
        job_id = await h.new_job(db, max_items=3)
        job, s = await h.run(db, job_id, {"feed": [], "pages": {lost: h.page(lost, phone=h.PHONE.format(405))}})
        assert lost not in s.opened and job.new_items == 0
