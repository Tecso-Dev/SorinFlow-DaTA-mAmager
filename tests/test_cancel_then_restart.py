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
