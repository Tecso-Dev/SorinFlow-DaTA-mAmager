"""
«مرورگر اسکرپر بالا نیامد: نامشخص» — and the number another run already had.

Six runs failed on 1405/07/04 with that line or with «profile … is already
open». The line said «نامشخص» whenever initialize() had returned False rather
than raised, which was most of the ways a run fails to start: the owner's only
number refused by Divar, every number open in other runs. And two runs of one
owner both took the same least-spent number, so the second failed with
«already open» while the owner's other numbers sat free.

initialize() runs here on a real Postgres session and the real Redis-backed
profile lock (fakeredis), through the real _open_browser_for and open_browser —
only Chromium itself and Divar's answer to a restored session are stand-ins.
"""
import asyncio
import functools
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_run_start_reasons.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.models.cookie import Cookie                       # noqa: E402,F401  (the tables below)
from app.models.scraping_job import ScrapingJob, ScrapingLog   # noqa: E402,F401
from app.models.user import User                           # noqa: E402,F401
from app.scraper import divar_scraper as ds                # noqa: E402
from app.scraper import stealth as st                      # noqa: E402
from _fake_redis import fake_server, patch_redis, redis_factory   # noqa: E402

# Obviously not anybody's numbers.
N1, N2, N3 = "09990000001", "09990000002", "09990000003"


# ── Chromium, as far as open_browser can tell ────────────────────────────────

class _Page:
    def __init__(self, ctx):
        self.context = ctx

    def is_closed(self):
        return self.context.closed


class _Cdp:
    async def send(self, *_a, **_k):
        return {}


class _Context:
    def __init__(self):
        self.pages, self.browser, self.closed = [], None, False

    async def new_page(self):
        page = _Page(self)
        self.pages.append(page)
        return page

    async def new_cdp_session(self, _page):
        return _Cdp()

    async def clear_cookies(self):
        pass

    async def close(self):
        self.closed = True


class _Playwright:
    """Launches record whose profile they opened; `fail` makes them raise."""

    def __init__(self, fail=None):
        self.chromium, self.launched, self.fail = self, [], fail

    async def start(self):
        return self

    async def launch_persistent_context(self, user_data_dir, **_k):
        if self.fail is not None:
            raise self.fail
        self.launched.append(os.path.basename(str(user_data_dir)))
        return _Context()

    async def stop(self):
        pass


# ── rows ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def world(monkeypatch, tmp_path):
    """An owner with numbers, the lock on fakeredis, and a way to make other
    runs: each test starts from nothing and leaves nothing."""
    if not os.environ["DATABASE_URL"].startswith("postgresql"):
        pytest.skip("needs Postgres — scraping_jobs.job_id is a Postgres UUID column")
    monkeypatch.setenv("SCRAPER_PROFILE_DIR", str(tmp_path))
    monkeypatch.setattr(st, "get_redis", redis_factory(fake_server()), raising=True)
    st._profile_locks.clear()
    st._PROFILES_IN_USE.clear()
    w = _World(monkeypatch)
    yield w
    st._profile_locks.clear()
    st._PROFILES_IN_USE.clear()


class _World:
    def __init__(self, monkeypatch):
        self.mp = monkeypatch
        self.said = []
        self.users, self.jobs = [], []

        async def _log(_self, message, *, level="info", **_extra):
            self.said.append((level, message))
        monkeypatch.setattr(ds.DivarScraper, "_log_run", _log)

    def engine(self):
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        eng = create_async_engine(os.environ["DATABASE_URL"])
        return eng, async_sessionmaker(eng, expire_on_commit=False, autoflush=False)

    async def tables(self, eng):
        from app.database import Base
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def owner(self, maker, numbers):
        """A user with these numbers, least spent first in the given order."""
        from app.models.cookie import Cookie
        from app.models.user import User
        async with maker() as s:
            u = User(username=f"start-{uuid.uuid4().hex[:10]}", hashed_password="-",
                     role="admin", permissions=["scraper"], is_active=True)
            s.add(u)
            await s.flush()
            for spent, phone in enumerate(numbers):
                s.add(Cookie(phone_number=phone, owner_user_id=u.id, reveals=spent,
                             cookies=[{"name": "sAccessToken", "value": "x.y.z"}]))
            await s.commit()
            self.users.append(u.id)
            return u.id

    async def live_run_on(self, maker, phone, status="running"):
        """Another run that has `phone` open: its row, and its lock."""
        from app.models.scraping_job import ScrapingJob
        if status:
            async with maker() as s:
                j = ScrapingJob(status=status, divar_phone=phone)
                s.add(j)
                await s.commit()
                self.jobs.append(j.id)
                jid = str(j.job_id)
        else:
            jid = None
        await st._acquire_profile_lock(phone)
        return jid

    async def cleanup(self, maker):
        from sqlalchemy import delete, select
        async with maker() as s:
            if self.jobs:
                mine = select(ScrapingJob.job_id).where(ScrapingJob.id.in_(self.jobs))
                await s.execute(delete(ScrapingLog).where(ScrapingLog.job_id.in_(mine)))
                await s.execute(delete(ScrapingJob).where(ScrapingJob.id.in_(self.jobs)))
            if self.users:
                await s.execute(delete(Cookie).where(Cookie.owner_user_id.in_(self.users)))
                await s.execute(delete(User).where(User.id.in_(self.users)))
            await s.commit()

    async def start(self, session, owner, *, named=None, restorable=(), pw=None, on_restore=None):
        """initialize() the way run_scraping_job calls it."""
        pw = pw or _Playwright()
        self.mp.setattr(ds, "async_playwright", lambda: pw)
        sc = ds.DivarScraper(db_session=session, proxy_enabled=False, headless=True)
        sc.owner_user_id = owner
        sc._job_id_str = str(uuid.uuid4())
        tried = []

        async def _restore(phone):
            tried.append(phone)
            if on_restore:
                on_restore(sc, phone)
            return phone in restorable
        sc.auth.restore_session = _restore
        ok = await sc.initialize(phone_number=named)
        return sc, ok, pw, tried


def _quick_wait(monkeypatch):
    """initialize waits up to 3 minutes for a number whose run has ended."""
    fast = functools.partialmethod(ds.DivarScraper._wait_for_released_profile, limit=3, step=0.05)
    monkeypatch.setattr(ds.DivarScraper, "_wait_for_released_profile", fast)


def _plain(reason):
    """What every reason must be: said, in words, with what to do, in the column."""
    assert reason, "initialize returned False and left no reason"
    assert "نامشخص" not in reason
    assert len(reason) <= 300, len(reason)
    assert " — " in reason, f"no «what to do» part: {reason}"


# ── two runs, one owner ──────────────────────────────────────────────────────

class TestTheOwnersNextFreeNumber:

    async def test_a_second_run_takes_the_next_free_number(self, world):
        eng, maker = world.engine()
        try:
            await world.tables(eng)
            owner = await world.owner(maker, [N1, N2])
            await world.live_run_on(maker, N1)                 # the first run, on the least spent
            async with maker() as session:
                sc, ok, pw, tried = await world.start(session, owner, restorable={N1, N2})
                assert ok, sc._init_reason
                assert sc.active_phone == N2 and pw.launched == [N2] and tried == [N2]
                await sc.close()
        finally:
            await world.cleanup(maker)
            await eng.dispose()

    async def test_a_number_whose_run_has_ended_is_passed_over_for_a_free_one(self, world, monkeypatch):
        """Held by a cancelled run whose browser is still closing: no need to
        wait for it while another number is free."""
        _quick_wait(monkeypatch)
        eng, maker = world.engine()
        try:
            await world.tables(eng)
            owner = await world.owner(maker, [N1, N2])
            await world.live_run_on(maker, N1, status="cancelled")
            async with maker() as session:
                t0 = asyncio.get_running_loop().time()
                sc, ok, pw, _tried = await world.start(session, owner, restorable={N1, N2})
                assert ok and sc.active_phone == N2
                assert asyncio.get_running_loop().time() - t0 < 1.5, "it waited for the closing one"
                await sc.close()
        finally:
            await world.cleanup(maker)
            await eng.dispose()

    async def test_when_only_a_closing_number_is_left_it_waits_for_it(self, world, monkeypatch):
        _quick_wait(monkeypatch)
        eng, maker = world.engine()
        try:
            await world.tables(eng)
            owner = await world.owner(maker, [N1, N2])
            await world.live_run_on(maker, N1)                               # live
            await world.live_run_on(maker, N2, status="cancelled")           # closing
            held = st._profile_locks.copy()

            async def _closes():
                await asyncio.sleep(0.3)
                rkey = st._profile_lock_key(N2)
                await st._release_profile_lock(str(st.profile_dir(N2)), rkey, held[rkey], False)
            closer = asyncio.create_task(_closes())
            async with maker() as session:
                sc, ok, pw, _tried = await world.start(session, owner, restorable={N2})
                await closer
                assert ok and sc.active_phone == N2 and pw.launched == [N2]
                await sc.close()
        finally:
            await world.cleanup(maker)
            await eng.dispose()

    async def test_every_number_busy_is_said_and_no_browser_is_opened(self, world):
        eng, maker = world.engine()
        try:
            await world.tables(eng)
            owner = await world.owner(maker, [N1, N2])
            await world.live_run_on(maker, N1)
            await world.live_run_on(maker, N2)
            async with maker() as session:
                sc, ok, pw, tried = await world.start(session, owner, restorable={N1, N2})
                assert not ok and pw.launched == [] and tried == []
                _plain(sc._init_reason)
                assert N1 in sc._init_reason and N2 in sc._init_reason
                assert "«ادامه»" in sc._init_reason and "«احراز هویت دیوار»" in sc._init_reason
                assert ("error", sc._init_reason) in world.said, "the run's own log says it once"
                await sc.close()
        finally:
            await world.cleanup(maker)
            await eng.dispose()

    async def test_losing_the_race_between_looking_and_opening_takes_the_next(self, world, monkeypatch):
        """Both runs looked at the same moment and saw N1 free; the one that
        opened second falls through to N2 instead of failing."""
        eng, maker = world.engine()
        try:
            await world.tables(eng)
            owner = await world.owner(maker, [N1, N2])
            await world.live_run_on(maker, N1)

            async def _looked_too_early(_account):
                return False
            monkeypatch.setattr(st, "profile_in_use", _looked_too_early)
            async with maker() as session:
                sc, ok, pw, _tried = await world.start(session, owner, restorable={N1, N2})
                assert ok, sc._init_reason
                assert sc.active_phone == N2 and pw.launched == [N2]
                await sc.close()
        finally:
            await world.cleanup(maker)
            await eng.dispose()


class TestANumberOrBrowserAnotherRunHas:

    async def test_a_named_number_names_the_run_that_has_it_and_offers_automatic(self, world):
        eng, maker = world.engine()
        try:
            await world.tables(eng)
            owner = await world.owner(maker, [N1, N2])
            holder = await world.live_run_on(maker, N1)
            async with maker() as session:
                sc, ok, pw, _tried = await world.start(session, owner, named=N1, restorable={N1})
                assert not ok and pw.launched == []
                _plain(sc._init_reason)
                assert N1 in sc._init_reason and holder[:8] in sc._init_reason
                assert "«خودکار»" in sc._init_reason and "«ادامه»" in sc._init_reason
                await sc.close()
        finally:
            await world.cleanup(maker)
            await eng.dispose()

    async def test_two_runs_without_a_number_say_so(self, world):
        """The shared no-number browser opens in one run at a time; the second
        run was told «این شمارهٔ دیوار در یک اسکرپ دیگر…» about a number it did
        not have."""
        eng, maker = world.engine()
        try:
            await world.tables(eng)
            owner = await world.owner(maker, [])
            await st._acquire_profile_lock(None)             # the first numberless run
            async with maker() as session:
                sc, ok, _pw, _tried = await world.start(session, owner)
                assert not ok
                _plain(sc._init_reason)
                assert "شمارهٔ دیوار ندارد" in sc._init_reason
                assert "«احراز هویت دیوار»" in sc._init_reason
                await sc.close()
        finally:
            await world.cleanup(maker)
            await eng.dispose()


class TestASessionThatWillNotOpen:

    async def test_the_only_number_refused_says_log_in_again(self, world):
        """The commonest «نامشخص»: the owner's one number, its session no longer
        accepted. The run log said «شمارهٔ تماس استخراج نمی‌شود» — as if the run
        went on — and the finish line said nothing at all."""
        eng, maker = world.engine()
        try:
            await world.tables(eng)
            owner = await world.owner(maker, [N1])
            async with maker() as session:
                sc, ok, _pw, tried = await world.start(session, owner, restorable=())
                assert not ok and tried == [N1]
                _plain(sc._init_reason)
                assert N1 in sc._init_reason and "دوباره وارد شوید" in sc._init_reason
                assert "استخراج نمی‌شود" not in " ".join(m for _l, m in world.said)
                await sc.close()
        finally:
            await world.cleanup(maker)
            await eng.dispose()

    async def test_the_fallback_passes_over_a_number_another_run_has(self, world):
        eng, maker = world.engine()
        try:
            await world.tables(eng)
            owner = await world.owner(maker, [N1, N2, N3])
            await world.live_run_on(maker, N2)
            async with maker() as session:
                sc, ok, pw, tried = await world.start(session, owner, restorable=())
                assert not ok
                assert tried == [N1, N3] and N2 not in pw.launched, "it tried a number another run has"
                _plain(sc._init_reason)
                assert N1 in sc._init_reason and N2 in sc._init_reason and N3 in sc._init_reason
                await sc.close()
        finally:
            await world.cleanup(maker)
            await eng.dispose()

    async def test_a_browser_that_closed_under_the_restore_is_not_an_expired_session(self, world):
        def _crash(sc, _phone):
            sc.context.closed = True
        eng, maker = world.engine()
        try:
            await world.tables(eng)
            owner = await world.owner(maker, [N1])
            async with maker() as session:
                sc, ok, _pw, _tried = await world.start(session, owner, on_restore=_crash)
                assert not ok
                _plain(sc._init_reason)
                assert "بسته شد" in sc._init_reason
                assert "نپذیرفت" not in sc._init_reason and "دوباره وارد شوید" not in sc._init_reason
                await sc.close()
        finally:
            await world.cleanup(maker)
            await eng.dispose()

    async def test_a_restored_fallback_still_starts(self, world):
        eng, maker = world.engine()
        try:
            await world.tables(eng)
            owner = await world.owner(maker, [N1, N2])
            async with maker() as session:
                sc, ok, _pw, tried = await world.start(session, owner, restorable={N2})
                assert ok and sc.active_phone == N2 and tried == [N1, N2]
                assert sc._init_reason is None
                await sc.close()
        finally:
            await world.cleanup(maker)
            await eng.dispose()


class TestChromiumThatWillNotStart:

    @pytest.mark.parametrize("error,words", [
        (RuntimeError("BrowserType.launch_persistent_context: Executable doesn't exist at "
                      "/ms-playwright/chromium-1097/chrome-linux/chrome"), "پیدا نشد"),
        (RuntimeError("BrowserType.launch_persistent_context: Browser closed.\n"
                      "==================== Browser output: ====================\n"
                      "[err] No usable sandbox! Update your kernel"), "sandbox"),
        (RuntimeError("BrowserType.launch_persistent_context: Browser closed."), "بسته شد"),
        (TimeoutError(), "زمان مجاز"),
        (OSError(28, "No space left on device"), "دیسک"),
        (PermissionError(13, "Permission denied"), "اجازه"),
        (ValueError("something nobody has seen before"), "something nobody has seen before"),
    ], ids=["no-chromium", "sandbox", "crashed", "timeout-without-text", "disk", "permission",
            "anything-else"])
    async def test_it_is_explained(self, world, error, words):
        eng, maker = world.engine()
        try:
            await world.tables(eng)
            owner = await world.owner(maker, [N1])
            async with maker() as session:
                sc, ok, _pw, _tried = await world.start(session, owner, pw=_Playwright(fail=error),
                                                        restorable={N1})
                assert not ok
                _plain(sc._init_reason)
                assert words in sc._init_reason
                assert await st.profile_in_use(N1) is False, "a failed launch kept the number"
                await sc.close()
        finally:
            await world.cleanup(maker)
            await eng.dispose()


# ── the finish line: run_scraping_job ────────────────────────────────────────

class _NotStarting:
    """DivarScraper as run_scraping_job sees it, whose initialize says no."""
    reason, error, logged = None, "", False
    made: list = []

    def __init__(self, **_kw):
        _NotStarting.made.append(self)

    async def initialize(self, phone_number=None):
        self._init_reason, self._init_error = self.reason, self.error
        self._init_logged = self.logged
        return False

    async def start_scraping_job(self, **_kw):
        raise AssertionError("a run whose browser did not come up went on")

    async def close(self):
        pass


@pytest.fixture
def finish(world, monkeypatch):
    from app.api.routes import scraper as routes
    from app.scraper import otp_store
    from app.services import divar_session
    patch_redis(monkeypatch, otp_store)
    swept = []

    async def _sweep(**kw):
        swept.append(kw)
        return {"alive": 1, "dead": 0, "unknown": 0}
    monkeypatch.setattr(divar_session, "sweep", _sweep)
    monkeypatch.setattr(routes, "DivarScraper", _NotStarting)
    _NotStarting.made = []
    return routes, swept


async def _pending(world, maker, status="pending"):
    from app.models.scraping_job import ScrapingJob
    async with maker() as s:
        j = ScrapingJob(status=status, config={"city": "urmia", "category": "rent-apartment"})
        s.add(j)
        await s.commit()
        world.jobs.append(j.id)
        return str(j.job_id)


async def _row_and_log(maker, job_id):
    from sqlalchemy import select
    from app.models.scraping_job import ScrapingJob, ScrapingLog
    async with maker() as s:
        row = (await s.execute(select(ScrapingJob).where(
            ScrapingJob.job_id == uuid.UUID(job_id)))).scalar_one()
        lines = (await s.execute(select(ScrapingLog).where(
            ScrapingLog.job_id == uuid.UUID(job_id)).order_by(ScrapingLog.id))).scalars().all()
        return row, [(line.details.get("stage"), line.level, line.message) for line in lines]


async def _run(routes, job_id):
    await routes.run_scraping_job(job_id=job_id, city="urmia", category="rent-apartment",
                                  max_items=5, download_images=False,
                                  db_url=os.environ["DATABASE_URL"])


class TestTheFinishLine:

    @pytest.mark.parametrize("reason,error,logged", [
        ("اسکرپ شروع نشد: نشست 09990000001 باز نشد — در «احراز هویت دیوار» دوباره وارد شوید", "", True),
        (None, "BrowserType.launch_persistent_context: Browser closed.", False),
        (None, "", False),                    # a path that forgot to say anything
    ], ids=["initialize-said-why", "only-the-technical-text", "nothing-at-all"])
    async def test_a_run_that_did_not_start_says_why_once(self, world, finish, reason, error, logged):
        routes, _swept = finish
        _NotStarting.reason, _NotStarting.error, _NotStarting.logged = reason, error, logged
        eng, maker = world.engine()
        try:
            await world.tables(eng)
            job_id = await _pending(world, maker)
            await _run(routes, job_id)
            row, lines = await _row_and_log(maker, job_id)
            assert row.status == "failed" and row.completed_at is not None
            _plain(row.finish_reason)
            assert row.error_message == row.finish_reason
            if reason:
                assert row.finish_reason == reason
            errors = [line for line in lines if line[1] == "error"]
            # initialize logged it itself (a stand-in here, so nothing reached
            # the table) — or run_scraping_job did, exactly once
            assert errors == ([] if logged else [("error", "error", row.finish_reason)])
        finally:
            await world.cleanup(maker)
            await eng.dispose()

    async def test_a_cancel_while_it_was_starting_stands(self, world, finish):
        routes, _swept = finish
        _NotStarting.reason, _NotStarting.error, _NotStarting.logged = None, "Browser closed.", False
        eng, maker = world.engine()
        try:
            await world.tables(eng)
            job_id = await _pending(world, maker)
            from sqlalchemy import update
            from app.models.scraping_job import ScrapingJob
            real = _NotStarting.initialize

            async def _cancelled_meanwhile(self, phone_number=None):
                async with maker() as s:
                    await s.execute(update(ScrapingJob).where(ScrapingJob.job_id == uuid.UUID(job_id))
                                    .values(status="cancelled"))
                    await s.commit()
                return await real(self, phone_number)
            world.mp.setattr(_NotStarting, "initialize", _cancelled_meanwhile)
            await _run(routes, job_id)
            row, _lines = await _row_and_log(maker, job_id)
            assert row.status == "cancelled" and row.finish_reason is None
        finally:
            await world.cleanup(maker)
            await eng.dispose()

    async def test_a_run_cancelled_in_the_queue_opens_nothing(self, world, finish):
        routes, swept = finish
        eng, maker = world.engine()
        try:
            await world.tables(eng)
            job_id = await _pending(world, maker, status="cancelled")
            await _run(routes, job_id)
            assert _NotStarting.made == [] and swept == [], "Divar was asked, a browser was built"
            row, lines = await _row_and_log(maker, job_id)
            assert row.status == "cancelled" and lines == []
        finally:
            await world.cleanup(maker)
            await eng.dispose()
