"""
Cancel a scrape, start another on the same number at once.

Cancelling only marks the row. The old run notices at its next check — after
the listing in hand and the pause after it, up to five minutes when Divar
has us cooling down — and only then closes its browser and releases the
account's profile lock (sf:profile:<digits>). A run started in between hit
the lock and failed at once with «این شمارهٔ دیوار در یک اسکرپ دیگر در حال
اجراست» (1405/07/05). Now the new run waits for a browser whose run has
ended, a live run still refuses it at once, and the cancelled run notices
during its pause instead of after it.
"""
import asyncio
import os
import sys
import time
import uuid
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_cancel_restart.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.scraper import stealth as st                     # noqa: E402
from app.scraper import divar_scraper as ds               # noqa: E402
from app.scraper.divar_scraper import DivarScraper        # noqa: E402
from _fake_redis import fake_server, redis_factory        # noqa: E402

NUM = "09140001234"


@pytest.fixture
def lock_redis(monkeypatch):
    monkeypatch.setattr(st, "get_redis", redis_factory(fake_server()), raising=True)
    st._profile_locks.clear()
    st._PROFILES_IN_USE.clear()
    yield
    st._profile_locks.clear()
    st._PROFILES_IN_USE.clear()


def _scraper(*, live=False):
    s = DivarScraper.__new__(DivarScraper)
    s.db_session = None
    s.current_job = None
    s.said = []

    async def _live(account):
        return live
    s._live_run_on = _live

    async def _log(message, **kw):
        s.said.append(message)
    s._log_run = _log
    return s


class TestTheNewRunWaits:
    async def test_it_waits_for_a_cancelled_runs_browser_and_then_opens(self, lock_redis):
        old = await st._acquire_profile_lock(NUM)          # the cancelled run, still closing
        s = _scraper(live=False)

        async def _old_run_closes():
            await asyncio.sleep(0.3)
            await st._release_profile_lock(str(st.profile_dir(NUM)), *old)
        closer = asyncio.create_task(_old_run_closes())

        t0 = time.monotonic()
        await s._wait_for_released_profile(NUM, limit=5, step=0.05)
        await closer
        assert 0.25 <= time.monotonic() - t0 < 2
        mine = await st._acquire_profile_lock(NUM)          # what open_browser does next
        assert mine[1], "the new run could not take the number after it was released"
        await st._release_profile_lock(str(st.profile_dir(NUM)), *mine)
        assert any("صبر می‌کنیم" in m for m in s.said) and any("بسته شد" in m for m in s.said), s.said

    async def test_a_live_run_is_refused_at_once(self, lock_redis):
        held = await st._acquire_profile_lock(NUM)
        s = _scraper(live=True)
        t0 = time.monotonic()
        await s._wait_for_released_profile(NUM, limit=5, step=0.05)
        assert time.monotonic() - t0 < 0.2, "waited for a number a running scrape holds"
        with pytest.raises(RuntimeError, match="already open"):
            await st._acquire_profile_lock(NUM)
        assert s.said == []
        await st._release_profile_lock(str(st.profile_dir(NUM)), *held)

    async def test_it_gives_up_after_the_limit(self, lock_redis):
        held = await st._acquire_profile_lock(NUM)
        s = _scraper(live=False)
        t0 = time.monotonic()
        await s._wait_for_released_profile(NUM, limit=0.3, step=0.05)
        assert 0.3 <= time.monotonic() - t0 < 1.5
        with pytest.raises(RuntimeError, match="already open"):
            await st._acquire_profile_lock(NUM)             # the old «already open» failure follows
        await st._release_profile_lock(str(st.profile_dir(NUM)), *held)

    async def test_a_free_number_does_not_wait(self, lock_redis):
        s = _scraper(live=False)
        t0 = time.monotonic()
        await s._wait_for_released_profile(NUM, limit=5, step=0.05)
        assert time.monotonic() - t0 < 0.2 and s.said == []

    async def test_a_run_with_no_number_does_not_wait(self, lock_redis):
        s = _scraper(live=False)
        await s._wait_for_released_profile(None, limit=5, step=0.05)
        assert s.said == []

    def test_initialize_waits_before_the_first_open(self):
        import inspect
        src = inspect.getsource(DivarScraper.initialize)
        i = src.index("await self._wait_for_released_profile(phone_number)")
        assert src.index("await self._open_browser_for(phone_number, proxy)") > i


class _Clock:
    """Fake time: sleeping advances it, and every sleep is recorded."""
    def __init__(self):
        self.t, self.sleeps = 1000.0, []

    def monotonic(self):
        return self.t

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.t += seconds


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(ds, "time", SimpleNamespace(monotonic=c.monotonic))
    monkeypatch.setattr(ds, "asyncio", SimpleNamespace(sleep=c.sleep))
    return c


def _pacer(clock, *, cancel_after=None, cooldown=0.0):
    s = DivarScraper.__new__(DivarScraper)
    s.stealth_config = SimpleNamespace(min_delay=2.0, max_delay=5.0)
    s._cooldown_until = clock.t + cooldown
    s.checks = 0

    async def _cancelled():
        s.checks += 1
        return cancel_after is not None and s.checks >= cancel_after
    s._cancelled_now = _cancelled
    return s


class TestTheCancelledRunLetsGoSooner:
    async def test_a_five_minute_cooldown_ends_at_the_cancel(self, clock):
        s = _pacer(clock, cancel_after=2, cooldown=300)
        await s._human_like_delay(stop_on_cancel=True)
        assert sum(clock.sleeps) <= 4, clock.sleeps
        assert s.checks == 2

    async def test_without_a_cancel_the_whole_pause_is_kept(self, clock):
        s = _pacer(clock, cancel_after=None, cooldown=10)
        await s._human_like_delay(stop_on_cancel=True)
        assert sum(clock.sleeps) >= 12, "the pace between listings was cut without a cancel"
        assert max(clock.sleeps) <= 2.0

    async def test_other_pauses_are_left_as_they_were(self, clock):
        """Collection and the listing itself keep their pacing: a cancel
        there must not turn into requests fired back to back."""
        s = _pacer(clock, cancel_after=1, cooldown=10)
        await s._human_like_delay()
        assert s.checks == 0 and clock.sleeps[0] == 10

    def test_both_pauses_between_listings_stop_on_cancel(self):
        import inspect
        src = inspect.getsource(DivarScraper.start_scraping_job)
        assert src.count("await self._human_like_delay(stop_on_cancel=True)") == 2
        assert "await self._human_like_delay()\n" not in src


# ── against real rows ─────────────────────────────────────────────────────

@pytest.fixture
def pg():
    url = os.environ["DATABASE_URL"]
    if not url.startswith("postgresql"):
        pytest.skip("needs Postgres — scraping_jobs.job_id is a postgresql UUID", allow_module_level=False)
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    return create_async_engine(url), async_sessionmaker


async def _rows(maker_pair, *specs):
    eng, maker = maker_pair
    from app.database import Base
    from app.models.scraping_job import ScrapingJob
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    ids = []
    async with maker(eng, expire_on_commit=False)() as s:
        for phone, status in specs:
            j = ScrapingJob(job_id=uuid.uuid4(), status=status, divar_phone=phone)
            s.add(j)
            await s.flush()
            ids.append(j)
        await s.commit()
    return ids


class TestWhoIsOnTheNumber:
    async def test_only_a_running_or_waiting_run_counts(self, pg):
        eng, maker = pg
        a, b, c = "09140009001", "09140009002", "09140009003"
        cancelled, running, paused, other = await _rows(
            pg, (a, "cancelled"), (b, "running"), (c, "paused"), ("09140009004", "completed"))
        try:
            async with maker(eng, expire_on_commit=False)() as session:
                s = DivarScraper.__new__(DivarScraper)
                s.db_session = session
                s._job_id_str = str(uuid.uuid4())
                assert await s._live_run_on(a) is False, "a cancelled run still counted as using the number"
                assert await s._live_run_on(b) is True
                assert await s._live_run_on(c) is True, "a run waiting for a code holds its browser"
                assert await s._live_run_on("09140009004") is False
                assert not session.in_transaction(), "left a transaction open before a sleep"
                s._job_id_str = str(running.job_id)            # the run asking is that running one
                assert await s._live_run_on(b) is False
        finally:
            await eng.dispose()

    async def test_the_cancel_is_seen_without_holding_a_transaction(self, pg):
        eng, maker = pg
        (job,) = await _rows(pg, ("09140009005", "running"))
        try:
            async with maker(eng, expire_on_commit=False)() as session:
                s = DivarScraper.__new__(DivarScraper)
                s.db_session = session
                s.current_job = job
                assert await s._cancelled_now() is False
                assert not session.in_transaction()
                async with maker(eng)() as other:          # the cancel endpoint, elsewhere
                    from sqlalchemy import update
                    from app.models.scraping_job import ScrapingJob
                    await other.execute(update(ScrapingJob).where(ScrapingJob.id == job.id)
                                        .values(status="cancelled"))
                    await other.commit()
                assert await s._cancelled_now() is True
                assert not session.in_transaction()
        finally:
            await eng.dispose()


# ── a cancel always wins ──────────────────────────────────────────────────
#
# The run wrote its own status through the ORM object, which holds what the
# row said when it was last read. Waiting for an SMS code wrote «paused» over
# a cancel committed in the meantime, the code (or the timeout) then wrote
# «running», and the run carried on to the end; the end of the run wrote
# «completed» over a cancel — or the sweep's «failed» — that landed during the
# last listing. These drive start_scraping_job on a real row, with the
# browser and Divar replaced.

from app.api.routes import scraper as routes          # noqa: E402
from test_deleted_listing import FakePage, LIVE       # noqa: E402

A, B, C = "https://divar.ir/v/aaaaAAAA", "https://divar.ir/v/bbbbBBBB", "https://divar.ir/v/ccccCCCC"


async def _status_from_elsewhere(eng, job_id, status):
    """What the cancel button, or the queue's sweep, commits on its own session."""
    from sqlalchemy import update
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from app.models.scraping_job import ScrapingJob
    async with async_sessionmaker(eng)() as other:
        await other.execute(update(ScrapingJob).where(ScrapingJob.job_id == job_id)
                            .values(status=status))
        await other.commit()


async def _row_now(eng, job_id):
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from app.models.scraping_job import ScrapingJob
    async with async_sessionmaker(eng)() as s:
        return (await s.execute(select(ScrapingJob).where(ScrapingJob.job_id == job_id))).scalar_one()


class _Run:
    """start_scraping_job on a real row, the way the worker calls it, with
    everything that would reach Divar or a browser replaced. Records what the
    run did after the cancel."""

    def __init__(self, session, job, *, delay=(0.01, 0.02)):
        from app.scraper import divar_scraper as _ds
        s = _ds.DivarScraper(db_session=session, proxy_enabled=False, headless=True)
        s._job_id_str = str(job.job_id)
        s.stealth_config.min_delay, s.stealth_config.max_delay = delay
        self.opened, self.saved, self.photos = [], [], []

        async def _nothing(*_a, **_k):
            return None

        async def _save(data):
            self.saved.append(data["divar_id"])
            s._last_save_created = True
            return object()

        async def _photos(images, divar_id):
            self.photos.append(divar_id)
            return []

        s._check_rate_limit = s._dwell_like_a_reader = s._space_out_reveal = _nothing
        s.maybe_rotate_account = s._persist_active_session = _nothing
        s.save_property, s.download_images = _save, _photos
        self.s, self.job = s, job

    async def go(self, urls):
        return await self.s.start_scraping_job(
            city="—", category="اسکرپ تکی", max_items=len(urls), download_images=True,
            job_id=str(self.job.job_id), urls=urls)


@pytest.fixture
def run_db(pg, monkeypatch):
    """Postgres rows plus fakeredis for the OTP store the run consults."""
    from _fake_redis import patch_redis
    from app.scraper import otp_store
    patch_redis(monkeypatch, otp_store)
    return pg


async def _one_job(pair, status="pending"):
    (job,) = await _rows(pair, (None, status))
    return job


class TestACancelWhileWaitingForACode:
    """Cancel pressed while the ad is open; Divar then asks for a code. The
    pause must not undo the cancel, and the wait must end at once."""

    async def test_the_cancel_stands_and_the_run_stops_at_this_listing(self, run_db, monkeypatch):
        eng, maker = run_db
        job = await _one_job(run_db)
        waited = {}

        class _Extractor:
            """ContactExtractor's side of the protocol: pause, wait while
            asking should_cancel every slice, resume."""
            contact_channel, needs_identity, account_count = None, False, 1

            def __init__(self, page, images_dir, **kw):
                self.kw = kw

            async def get_phone_number(self):
                await _status_from_elsewhere(eng, job.job_id, "cancelled")   # the cancel lands
                await self.kw["on_pause"]()                                  # then Divar asks for a code
                t0 = time.monotonic()
                while time.monotonic() - t0 < 4:                            # «up to six hours»
                    if await self.kw["should_cancel"]():
                        break
                    await asyncio.sleep(0.05)
                waited["s"] = time.monotonic() - t0
                await self.kw["on_resume"]()
                return None

        monkeypatch.setattr(ds, "ContactExtractor", _Extractor)
        try:
            async with maker(eng, expire_on_commit=False, autoflush=False)() as session:
                r = _Run(session, job)
                r.s.page = FakePage(200, LIVE)
                await r.go([A, B])
            row = await _row_now(eng, job.job_id)
            assert row.status == "cancelled", "the pause or the resume wrote over the cancel"
            assert waited["s"] < 1, f"the code wait went on for {waited['s']:.1f}s after the cancel"
            assert r.s.page.url.endswith("aaaaAAAA"), "the run opened the next listing"
            assert r.saved == [] and r.photos == [], "a listing the run was told to drop was saved"
        finally:
            await eng.dispose()

    async def test_a_code_that_comes_back_before_a_cancel_still_resumes(self, run_db):
        eng, maker = run_db
        job = await _one_job(run_db, status="running")
        try:
            async with maker(eng, expire_on_commit=False, autoflush=False)() as session:
                s = ds.DivarScraper(db_session=session, proxy_enabled=False, headless=True)
                s.current_job, s._job_id_str = job, str(job.job_id)
                await s._pause_for_code()
                assert (await _row_now(eng, job.job_id)).status == "paused"
                assert await s._cancelled_now() is False, "a paused run is not a stopped one"
                await s._resume_after_code()
                assert (await _row_now(eng, job.job_id)).status == "running"
                assert not session.in_transaction()
        finally:
            # a «running» row left behind is an orphan to the next module's sweep
            await _status_from_elsewhere(eng, job.job_id, "completed")
            await eng.dispose()


class TestTheEndOfTheRunDoesNotOverwrite:
    @pytest.mark.parametrize("outside", ["cancelled", "failed"])
    async def test_a_stop_during_the_last_listing_stands(self, run_db, outside):
        """The number in hand is kept — its reveal is spent — but the row
        keeps what the cancel button (or the sweep) said."""
        eng, maker = run_db
        job = await _one_job(run_db)
        try:
            async with maker(eng, expire_on_commit=False, autoflush=False)() as session:
                r = _Run(session, job)

                async def _detail(url, **_kw):
                    r.opened.append(url)
                    await _status_from_elsewhere(eng, job.job_id, outside)
                    return {"url": url, "divar_id": url.rsplit("/", 1)[1], "title": "آپارتمان",
                            "phone_number": "09120000000", "contact_channel": "phone"}
                r.s.scrape_property_detail = _detail
                await r.go([A])
            row = await _row_now(eng, job.job_id)
            assert row.status == outside, f"«completed» was written over «{outside}»"
            assert r.saved == ["aaaaAAAA"]
        finally:
            await eng.dispose()

    async def test_without_a_stop_it_still_completes(self, run_db):
        eng, maker = run_db
        job = await _one_job(run_db)
        try:
            async with maker(eng, expire_on_commit=False, autoflush=False)() as session:
                r = _Run(session, job)

                async def _detail(url, **_kw):
                    return {"url": url, "divar_id": url.rsplit("/", 1)[1], "title": "آپارتمان",
                            "phone_number": "09120000000", "contact_channel": "phone"}
                r.s.scrape_property_detail = _detail
                await r.go([A, B])
            row = await _row_now(eng, job.job_id)
            assert row.status == "completed" and row.new_items == 2
        finally:
            await eng.dispose()

    async def test_a_cancel_before_the_run_took_the_row_is_not_undone_by_its_start(self, run_db):
        """The check at the start read the row once; a cancel committed after
        that read was overwritten by «running»."""
        eng, maker = run_db
        job = await _one_job(run_db)
        try:
            async with maker(eng, expire_on_commit=False, autoflush=False)() as session:
                r = _Run(session, job)

                async def _prune(*_a, **_k):              # runs between the read and the write
                    await _status_from_elsewhere(eng, job.job_id, "cancelled")
                monkeypatch_prune = pytest.MonkeyPatch()
                from app.services import job_log
                monkeypatch_prune.setattr(job_log, "prune", _prune)
                try:
                    await r.go([A])
                finally:
                    monkeypatch_prune.undo()
            assert (await _row_now(eng, job.job_id)).status == "cancelled"
            assert r.saved == [] and r.opened == []
        finally:
            await eng.dispose()


class TestACancelInTheMiddleOfAListing:
    """How long a run goes on after «لغو» lands halfway through a listing:
    to the end of that listing at most, and not into the next one."""

    async def test_with_a_number_in_hand_the_listing_is_kept_and_the_pause_is_cut(self, run_db):
        eng, maker = run_db
        job = await _one_job(run_db)
        try:
            async with maker(eng, expire_on_commit=False, autoflush=False)() as session:
                # a pause between listings of 30 s: only the cancel can end it early
                r = _Run(session, job, delay=(30.0, 30.0))
                cancelled_at = {}

                async def _detail(url, **_kw):
                    r.opened.append(url)
                    await _status_from_elsewhere(eng, job.job_id, "cancelled")
                    cancelled_at["t"] = time.monotonic()
                    return {"url": url, "divar_id": url.rsplit("/", 1)[1], "title": "آپارتمان",
                            "phone_number": "09120000000", "contact_channel": "phone"}
                r.s.scrape_property_detail = _detail
                await r.go([A, B, C])
                took = time.monotonic() - cancelled_at["t"]
            assert r.opened == [A], "the run went on to the next listing"
            assert r.saved == ["aaaaAAAA"], "the number already revealed was thrown away"
            assert took < 5, f"the run took {took:.1f}s to stop after the cancel"
            assert (await _row_now(eng, job.job_id)).status == "cancelled"
        finally:
            await eng.dispose()

    async def test_without_a_number_it_stops_before_the_photos_and_the_save(self, run_db):
        eng, maker = run_db
        job = await _one_job(run_db)
        try:
            async with maker(eng, expire_on_commit=False, autoflush=False)() as session:
                r = _Run(session, job)

                async def _detail(url, **_kw):
                    r.opened.append(url)
                    await _status_from_elsewhere(eng, job.job_id, "cancelled")
                    return {"url": url, "divar_id": url.rsplit("/", 1)[1], "title": "آپارتمان",
                            "images": ["https://s100.divarcdn.com/static/photo/x.jpg"],
                            "contact_channel": "unavailable"}
                r.s.scrape_property_detail = _detail
                await r.go([A, B])
            assert r.opened == [A]
            assert r.photos == [] and r.saved == [], "photos downloaded for a listing being dropped"
            row = await _row_now(eng, job.job_id)
            assert row.status == "cancelled" and row.failed_items == 0
        finally:
            await eng.dispose()

    async def test_the_sweep_marking_the_run_failed_stops_it_too(self, run_db):
        """The row says the run is over; carrying on would do work nobody can
        see, on a number a «ادامه» of it is about to want."""
        eng, maker = run_db
        job = await _one_job(run_db)
        try:
            async with maker(eng, expire_on_commit=False, autoflush=False)() as session:
                r = _Run(session, job)

                async def _detail(url, **_kw):
                    r.opened.append(url)
                    if url == A:
                        await _status_from_elsewhere(eng, job.job_id, "failed")
                    return {"url": url, "divar_id": url.rsplit("/", 1)[1], "title": "آپارتمان",
                            "phone_number": "09120000000", "contact_channel": "phone"}
                r.s.scrape_property_detail = _detail
                await r.go([A, B, C])
            assert r.opened == [A]
            assert (await _row_now(eng, job.job_id)).status == "failed"
        finally:
            await eng.dispose()


class TestTheCancelButton:
    async def test_a_run_that_finished_meanwhile_is_not_marked_cancelled(self, run_db, monkeypatch):
        """The button read the row, then wrote «cancelled» — over a
        «completed» the run had committed in between."""
        eng, maker = run_db
        job = await _one_job(run_db, status="running")
        from fastapi import HTTPException
        from types import SimpleNamespace as NS

        async def _no_prompts(_job_id):
            return 0
        from app.scraper import otp_store
        monkeypatch.setattr(otp_store, "clear_job", _no_prompts)
        try:
            async with maker(eng, expire_on_commit=False)() as db:
                real_execute = db.execute
                calls = []

                async def _execute(stmt, *a, **k):
                    res = await real_execute(stmt, *a, **k)
                    calls.append(stmt)
                    if len(calls) == 1:                   # the button has read «running»…
                        await _status_from_elsewhere(eng, job.job_id, "completed")   # …the run ends
                    return res
                db.execute = _execute
                with pytest.raises(HTTPException) as e:
                    await routes.cancel_scraping_job(str(job.job_id), db, NS(id=1, role="root"))
            assert e.value.status_code == 400
            assert (await _row_now(eng, job.job_id)).status == "completed"
        finally:
            await eng.dispose()

    async def test_a_running_run_is_cancelled(self, run_db, monkeypatch):
        eng, maker = run_db
        job = await _one_job(run_db, status="paused")
        from types import SimpleNamespace as NS

        async def _no_prompts(_job_id):
            return 0
        from app.scraper import otp_store
        monkeypatch.setattr(otp_store, "clear_job", _no_prompts)
        try:
            async with maker(eng, expire_on_commit=False)() as db:
                got = await routes.cancel_scraping_job(str(job.job_id), db, NS(id=1, role="root"))
            assert got["was"] == "paused"
            row = await _row_now(eng, job.job_id)
            assert row.status == "cancelled" and row.completed_at is not None
        finally:
            await eng.dispose()
