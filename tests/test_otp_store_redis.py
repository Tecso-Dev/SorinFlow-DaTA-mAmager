"""
otp_store moved from an in-process dict (one asyncio.Event per pending
request) to Redis, because the app is splitting into api replicas, a worker
and a scheduler: a code typed on an api replica has no way to reach an
asyncio.Event sitting in the worker's memory, and any process restarting
used to wipe every pending prompt, cancel window and strike count with it.

These tests are about the migration itself — the three properties an
in-process dict structurally cannot have and Redis is the whole point of
adding: state outlives a process restart, two processes (simulated as two
independent Redis clients on one server) see and wake each other, and
nothing pending outlives its TTL. Everything else about otp_store's
behaviour (matching, resends, identity walls, ...) is covered where it
always was — test_otp_inbound.py, test_otp_modal_diagnostics.py, and the
rest — now calling the async API.
"""
import asyncio
import importlib
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_otp_redis.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

import app.database as db                          # noqa: E402
from app.scraper import otp_store                  # noqa: E402
from _fake_redis import fake_server, redis_factory  # noqa: E402


@pytest.fixture(autouse=True)
def _redis(monkeypatch):
    """Patches both otp_store.get_redis (what every call in this file goes
    through directly) and app.database.get_redis (what a reload() below
    re-reads), on one shared FakeServer."""
    server = fake_server()
    get_redis = redis_factory(server)
    monkeypatch.setattr(otp_store, "get_redis", get_redis, raising=True)
    monkeypatch.setattr(db, "get_redis", get_redis, raising=True)
    return server


class TestStateSurvivesARestart:
    """The in-memory version's whole failure mode: a pod restart (a deploy,
    an OOM, a crash) wiped _store, _switch, _identity and every cancel
    window with it. Reloading the module is the closest a single test gets
    to "a fresh process starts" — its own module-level dicts would come back
    empty exactly like a real restart's would — but Redis is a separate
    service the reload does not touch.
    """

    async def test_a_pending_prompt_is_still_there(self):
        await otp_store.request("restart-job:ad1", "09120000001")
        await otp_store.submit("restart-job:ad1", "445566")

        importlib.reload(otp_store)
        # A real restart's fresh process calls app.database.get_redis() and
        # reaches the same Redis; reload() only re-binds the module-level
        # name, so it has to be pointed at the fake server again the same
        # way — this is the process picking Redis back up, not new state.
        otp_store.get_redis = db.get_redis

        assert await otp_store.pop_code("restart-job:ad1") == "445566"

    async def test_cancel_windows_and_strikes_survive_too(self):
        await otp_store.cancel_all("restart-job2")
        await otp_store.note_timeout("restart-job2")
        await otp_store.note_timeout("restart-job2")

        importlib.reload(otp_store)
        otp_store.get_redis = db.get_redis

        assert await otp_store.is_cancelled("restart-job2") is True
        assert await otp_store.strikes("restart-job2") == 2


class TestTwoProcessesShareOnePrompt:
    """api and worker are separate processes; simulated here as two
    independent Redis clients talking to one FakeServer, since that is
    exactly what two processes talking to one real Redis look like from
    otp_store's side — it never holds a client open across calls."""

    async def test_a_code_submitted_on_one_wakes_the_waiter_on_the_other(self, _redis):
        await otp_store.request("cross-proc:ad1", "09120000009")

        started = time.monotonic()

        async def waiter():
            return await otp_store.wait_code("cross-proc:ad1", 5)

        async def other_process():
            # Not otp_store.submit() — a genuinely separate client, standing
            # in for the api replica that would otherwise be a different
            # pod with no way to import this same object at all.
            await asyncio.sleep(0.2)
            import fakeredis.aioredis
            other = fakeredis.aioredis.FakeRedis(server=_redis, decode_responses=True)
            await other.hsetnx(otp_store._prompt_key("cross-proc:ad1"), "code", "778899")
            await other.rpush(otp_store._signal_key("cross-proc:ad1"), "1")

        woke, _ = await asyncio.gather(waiter(), other_process())
        elapsed = time.monotonic() - started

        assert woke is True
        assert elapsed < 2, f"BLPOP should wake as soon as the push happens, took {elapsed:.2f}s"
        assert await otp_store.pop_code("cross-proc:ad1") == "778899"

    async def test_get_pending_on_one_client_sees_a_request_made_on_another(self, _redis):
        import fakeredis.aioredis
        process_a = fakeredis.aioredis.FakeRedis(server=_redis, decode_responses=True)
        # process A registers the prompt directly against its own client...
        await process_a.hset(otp_store._prompt_key("cross-proc:ad2"), mapping={
            "phone_hint": "0912", "ts": time.time(), "resend": "0", "resends": "0"})
        await process_a.sadd(otp_store._INDEX_KEY, "cross-proc:ad2")

        # ...process B (otp_store's own patched client) reads it back
        pending = await otp_store.get_pending()
        assert any(p["key"] == "cross-proc:ad2" for p in pending)


class TestNothingPendingOutlivesItsTTL:

    async def test_the_prompt_hash_ttl_matches_the_wait_window(self, monkeypatch):
        from app.config import get_settings
        cfg = get_settings()
        monkeypatch.setattr(cfg, "otp_wait_timeout", 300, raising=False)
        monkeypatch.setattr(cfg, "otp_wait_for_human", False, raising=False)

        await otp_store.request("ttl-job:ad1", "0912")
        r = await otp_store.get_redis()
        ttl = await r.ttl(otp_store._prompt_key("ttl-job:ad1"))
        # wait_window() (300) + 60s of slack, never negative/missing
        assert 300 < ttl <= 360, ttl

    async def test_restart_clock_refreshes_the_ttl_too(self, monkeypatch):
        from app.config import get_settings
        cfg = get_settings()
        monkeypatch.setattr(cfg, "otp_wait_timeout", 300, raising=False)
        monkeypatch.setattr(cfg, "otp_wait_for_human", False, raising=False)

        await otp_store.request("ttl-job:ad2", "0912")
        r = await otp_store.get_redis()
        await r.expire(otp_store._prompt_key("ttl-job:ad2"), 5)   # simulate most of the window gone
        await otp_store.restart_clock("ttl-job:ad2")
        ttl = await r.ttl(otp_store._prompt_key("ttl-job:ad2"))
        assert ttl > 100, "restart_clock must renew the TTL, not just the `ts` field"

    async def test_a_cancel_window_expires_on_its_own(self, monkeypatch):
        monkeypatch.setattr(otp_store, "_CANCEL_WINDOW", 1, raising=False)
        await otp_store.cancel_all("expiring-job")
        assert await otp_store.is_cancelled("expiring-job") is True
        await asyncio.sleep(1.3)
        assert await otp_store.is_cancelled("expiring-job") is False, \
            "a cancel window must lift itself, not require reset_cancel()"

    async def test_an_early_code_is_gone_after_its_ttl(self, monkeypatch):
        monkeypatch.setattr(otp_store, "EARLY_TTL", 1, raising=False)
        assert await otp_store.park_early_code("0912", "111111") is True
        await asyncio.sleep(1.3)
        # request() claims early codes; nothing left to claim means False
        assert await otp_store.request("late-job:ad1", "0912") is False

    async def test_a_login_code_is_gone_after_its_ttl(self, monkeypatch):
        monkeypatch.setattr(otp_store, "LOGIN_CODE_TTL", 1, raising=False)
        await otp_store.put_login_code("0912", "654321")
        await asyncio.sleep(1.3)
        assert await otp_store.take_login_code("0912") is None

    async def test_switch_requests_and_identity_walls_do_not_expire(self):
        """Unlike the five above, these two have no natural deadline — a
        switch waits for the run's next safe point and an identity wall
        waits for a person to clear it, either of which can take a while.
        Redis's TTL machinery is the wrong tool for "no timer", so these
        stay plain untimed keys, same as the in-memory dicts they replaced."""
        r = await otp_store.get_redis()
        await otp_store.request_switch("no-ttl-job", "0912999")
        await otp_store.note_identity_required("0912888", job_id="no-ttl-job")
        assert await r.ttl(otp_store._switch_key("no-ttl-job")) == -1
        assert await r.ttl(otp_store._identity_key("0912888")) == -1
