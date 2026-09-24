"""
The cross-process Chromium profile lock (app/scraper/stealth.py).

One Chromium may hold a user_data_dir at a time; two jobs on the same
account fail inside Chromium with an unreadable error. `_PROFILES_IN_USE`
(an in-process set) only ever protected against a second job in the SAME
process — no help once the worker runs several replicas or a scheduler and
a worker are separate pods. `sf:profile:{account}` in Redis is the lock now;
the in-process set survives as the fallback for when Redis itself cannot be
reached, which is why these tests check both paths and the boundary between
them.
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_profile_lock.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.scraper import stealth as st          # noqa: E402
from _fake_redis import fake_server, redis_factory  # noqa: E402


@pytest.fixture(autouse=True)
def _redis(monkeypatch):
    server = fake_server()
    monkeypatch.setattr(st, "get_redis", redis_factory(server), raising=True)
    # Every test starts with a clean slate for the module-level bookkeeping
    # the lock keeps alongside Redis (the refresher's own membership, and
    # the fallback set for when Redis is down) — otherwise one test's
    # leftover lock reads as contention in the next.
    st._profile_locks.clear()
    st._PROFILES_IN_USE.clear()
    yield server
    st._profile_locks.clear()
    st._PROFILES_IN_USE.clear()


class TestContentionAcrossProcesses:
    """Two "processes" = two independent Redis clients on one FakeServer,
    the same stand-in test_otp_store_redis.py uses — from the lock's side,
    that is indistinguishable from two real pods sharing one Redis."""

    async def test_a_second_process_is_refused(self, _redis):
        rkey, token, fallback = await st._acquire_profile_lock("09120001111")
        assert fallback is False and rkey and token

        import fakeredis.aioredis
        other_process_view = fakeredis.aioredis.FakeRedis(server=_redis, decode_responses=True)
        held = await other_process_view.get(rkey)
        assert held == token, "the lock is not visible to a second client on the same Redis"

        with pytest.raises(RuntimeError, match="already open"):
            await st._acquire_profile_lock("09120001111")

        await st._release_profile_lock(str(st.profile_dir("09120001111")), rkey, token, fallback)

    async def test_a_different_account_is_never_contended(self, _redis):
        rkey1, token1, fb1 = await st._acquire_profile_lock("09120002222")
        rkey2, token2, fb2 = await st._acquire_profile_lock("09120003333")   # must not raise
        await st._release_profile_lock(str(st.profile_dir("09120002222")), rkey1, token1, fb1)
        await st._release_profile_lock(str(st.profile_dir("09120003333")), rkey2, token2, fb2)

    async def test_released_is_immediately_reopenable(self, _redis):
        rkey, token, fallback = await st._acquire_profile_lock("09120004444")
        await st._release_profile_lock(str(st.profile_dir("09120004444")), rkey, token, fallback)
        # must not raise "already open" — the release actually took
        rkey2, token2, _ = await st._acquire_profile_lock("09120004444")
        await st._release_profile_lock(str(st.profile_dir("09120004444")), rkey2, token2, False)


class TestReleaseOnlyByToken:
    """The lock is released with a WATCH/MULTI/EXEC compare-and-delete
    (fakeredis has no Lua scripting without an optional dependency nobody
    installed, so this is the atomic primitive used in place of a script —
    see _compare_and_delete's own docstring), so a stale caller — one whose
    TTL already lapsed and was re-acquired by somebody else — can never
    delete a lock it no longer holds."""

    async def test_a_wrong_token_does_not_release_someone_elses_lock(self, _redis):
        rkey, token, _ = await st._acquire_profile_lock("09120005555")
        r = await st.get_redis()
        released = await st._compare_and_delete(r, rkey, "not-the-real-token")
        assert released is False
        assert await r.get(rkey) == token, "a foreign token must not have touched the real lock"

    async def test_releasing_twice_is_harmless(self, _redis):
        rkey, token, fallback = await st._acquire_profile_lock("09120006666")
        await st._release_profile_lock(str(st.profile_dir("09120006666")), rkey, token, fallback)
        # the second release finds nothing to delete — must not raise
        await st._release_profile_lock(str(st.profile_dir("09120006666")), rkey, token, fallback)

    async def test_profile_in_use_reflects_redis_not_this_processs_memory(self, _redis):
        assert await st.profile_in_use("09120007777") is False
        rkey, token, fallback = await st._acquire_profile_lock("09120007777")
        assert await st.profile_in_use("09120007777") is True
        await st._release_profile_lock(str(st.profile_dir("09120007777")), rkey, token, fallback)
        assert await st.profile_in_use("09120007777") is False


class TestTheBackgroundRefresher:
    """120s TTL, refreshed every 30s — lazily started on the first lock and
    stopped once nothing is held, so an idle process runs no background
    loop at all."""

    async def test_it_keeps_a_short_ttl_alive(self, _redis, monkeypatch):
        monkeypatch.setattr(st, "_PROFILE_REFRESH_INTERVAL", 0.2, raising=False)
        rkey, token, _ = await st._acquire_profile_lock("09120008888")
        r = await st.get_redis()
        await r.expire(rkey, 1)   # force a short TTL to prove the refresh extends it
        await asyncio.sleep(0.6)
        assert await r.ttl(rkey) > 1, "the refresher did not extend the lock's TTL"
        await st._release_profile_lock(str(st.profile_dir("09120008888")), rkey, token, False)

    async def test_it_stops_once_nothing_is_held(self, _redis, monkeypatch):
        monkeypatch.setattr(st, "_PROFILE_REFRESH_INTERVAL", 0.1, raising=False)
        rkey, token, fallback = await st._acquire_profile_lock("09120009999")
        st._ensure_profile_refresh_task()
        task = st._profile_refresh_task
        await st._release_profile_lock(str(st.profile_dir("09120009999")), rkey, token, fallback)
        await asyncio.sleep(0.3)
        assert task.done(), "the refresher must exit once _profile_locks is empty"


class TestFallbackWhenRedisIsDown:
    """A single worker replica (today's deployment) stays safe even if
    Redis is briefly unreachable — the in-process set is exactly what this
    guard always was before Redis backed it."""

    async def test_falls_back_and_still_refuses_a_second_open(self, monkeypatch):
        async def _boom():
            raise ConnectionError("redis unreachable")
        monkeypatch.setattr(st, "get_redis", _boom, raising=True)

        rkey, token, fallback = await st._acquire_profile_lock("09120000001")
        assert fallback is True and rkey is None and token is None
        assert str(st.profile_dir("09120000001")) in st._PROFILES_IN_USE

        with pytest.raises(RuntimeError, match="already open"):
            await st._acquire_profile_lock("09120000001")

        await st._release_profile_lock(str(st.profile_dir("09120000001")), rkey, token, fallback)
        assert str(st.profile_dir("09120000001")) not in st._PROFILES_IN_USE

    async def test_the_error_text_is_identical_either_way(self, _redis, monkeypatch):
        """divar_scraper.py and run_scraping_job both match on the substring
        "already open" — whichever path refused the second open, the caller
        must not have to tell them apart."""
        rkey, token, fallback = await st._acquire_profile_lock("09120000002")
        try:
            await st._acquire_profile_lock("09120000002")
        except RuntimeError as e:
            redis_mode_text = str(e)
        await st._release_profile_lock(str(st.profile_dir("09120000002")), rkey, token, fallback)

        async def _boom():
            raise ConnectionError("redis unreachable")
        monkeypatch.setattr(st, "get_redis", _boom, raising=True)
        rkey2, token2, fallback2 = await st._acquire_profile_lock("09120000002")
        try:
            await st._acquire_profile_lock("09120000002")
        except RuntimeError as e:
            fallback_mode_text = str(e)
        await st._release_profile_lock(str(st.profile_dir("09120000002")), rkey2, token2, fallback2)

        assert redis_mode_text == fallback_mode_text
        assert "already open" in redis_mode_text


class TestTheLockIsHeldBeforeCleaningUpSingletonFiles:
    """Chromium leaves SingletonLock/Cookie/Socket behind after a crash, and
    a stale one is safe to remove — but only once nothing else can still be
    mid-launch against the same profile. Removing them before the lock is
    held would let two racing launches both pass the "already open" check
    and then both delete each other's Singleton* files out from under a
    browser that was about to use them."""

    def test_open_browser_acquires_before_touching_singleton_files(self):
        import inspect
        src = inspect.getsource(st.open_browser)
        lock_at = src.index("await _acquire_profile_lock(")
        # the loop itself, not the comment above it that also names the file
        cleanup_at = src.index('for stale in ("SingletonLock"')
        assert lock_at < cleanup_at


class TestDivarAuthAlwaysReleases:
    """DivarAuth.close_browser() is how a login browser ends. It used to close
    the page first, and a page that was already gone (a crashed browser)
    raised there — before close_context() could release the lock, which the
    refresher then kept alive for the life of the process."""

    async def test_a_page_that_fails_to_close_still_releases_the_lock(self, _redis):
        from app.scraper.auth import DivarAuth

        rkey, token, fallback = await st._acquire_profile_lock("09120005555")

        class _Page:
            async def close(self):
                raise RuntimeError("Target page, context or browser has been closed")

        class _Context:
            async def close(self):
                pass

        ctx = _Context()
        ctx._sorinflow_profile_key = str(st.profile_dir("09120005555"))
        ctx._sorinflow_lock = (rkey, token, fallback)
        auth = DivarAuth.__new__(DivarAuth)      # no cookies dir, no DB — just the close path
        auth.page, auth.context, auth.browser = _Page(), ctx, None

        await auth.close_browser()

        assert rkey not in st._profile_locks
        rkey2, token2, _ = await st._acquire_profile_lock("09120005555")   # must not raise
        await st._release_profile_lock(str(st.profile_dir("09120005555")), rkey2, token2, False)
