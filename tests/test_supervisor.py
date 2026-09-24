"""
Background loops under a supervisor (app/services/supervisor.py).

Every periodic loop used to be a bare asyncio.create_task: one that raised —
or whose session teardown raised past its own try/except — stopped for the
life of the process with nothing saying so, while /health stayed green.
These drive supervise() and the process heartbeat with tiny timings against
a fakeredis, and check what they do and what they leave where the
monitoring page reads it.
"""
import asyncio
import json
import os
import re
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_supervisor.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

import fakeredis  # noqa: E402
import fakeredis.aioredis  # noqa: E402
from loguru import logger  # noqa: E402

from app import database  # noqa: E402
from app.services import supervisor as sv  # noqa: E402


@pytest.fixture
def redis(monkeypatch):
    fake = fakeredis.aioredis.FakeRedis(server=fakeredis.FakeServer(), decode_responses=True)

    async def _get():
        return fake
    monkeypatch.setattr(database, "get_redis", _get)
    monkeypatch.setattr(sv, "BACKOFF_START", 0.01)
    monkeypatch.setattr(sv, "BACKOFF_MAX", 0.04)
    monkeypatch.setattr(sv, "WRITE_EVERY", 0.01)
    monkeypatch.setattr(sv, "_redis_down", False)
    return fake


@pytest.fixture
def logs():
    seen = []
    sink = logger.add(lambda m: seen.append(m.record["message"]), level="INFO")
    yield seen
    logger.remove(sink)


async def _until(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < end, "condition not reached in time"
        await asyncio.sleep(0.01)


async def _stop(task):
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def _state(fake, name):
    raw = await fake.hget(sv.LOOPS_KEY, name)
    return json.loads(raw) if raw else None


def _delays(logs, name):
    return [re.search(r"in ([\d.]+)s$", m).group(1)
            for m in logs if m.startswith(f"[supervisor] {name} ") and "restart #" in m]


class TestRestart:

    async def test_a_loop_that_raises_is_restarted_with_a_growing_backoff(self, redis, logs):
        calls = []
        healthy = asyncio.Event()

        async def loop():
            calls.append(1)
            if len(calls) <= 4:
                raise ValueError("token=abc123 is exactly what must not reach the page")
            sv.beat("t_raise")
            healthy.set()
            await asyncio.sleep(3600)

        task = asyncio.create_task(sv.supervise("t_raise", loop, stall_after=60, role="scheduler"))
        await asyncio.wait_for(healthy.wait(), 5)
        assert _delays(logs, "t_raise") == ["0.01", "0.02", "0.04", "0.04"], "1 s → 5 min, doubling, capped"
        state = await _state(redis, "t_raise")      # written at every restart
        assert state["restarts"] == 4 and state["role"] == "scheduler" and state["host"] == sv.HOST
        assert state["last_error"].startswith("raised ValueError at test_supervisor.py:")
        assert state["last_error_at"] and state["stall_after"] == 60 and state["off"] is False
        assert "abc123" not in json.dumps(state), "an exception's message never reaches Redis"
        await _stop(task)

    async def test_a_healthy_run_starts_the_backoff_over(self, redis, logs, monkeypatch):
        monkeypatch.setattr(sv, "HEALTHY_AFTER", 0.3)
        calls = []
        settled = asyncio.Event()

        async def loop():
            calls.append(1)
            if len(calls) in (1, 2):
                raise RuntimeError("early")
            if len(calls) == 3:
                sv.beat("t_reset")
                await asyncio.sleep(0.6)           # healthy for longer than HEALTHY_AFTER
                raise RuntimeError("late")
            sv.beat("t_reset")
            settled.set()
            await asyncio.sleep(3600)

        task = asyncio.create_task(sv.supervise("t_reset", loop, stall_after=60))
        await asyncio.wait_for(settled.wait(), 5)
        assert _delays(logs, "t_reset") == ["0.01", "0.02", "0.01"]
        await _stop(task)

    async def test_a_loop_that_returns_after_beating_is_restarted(self, redis, logs):
        calls = []
        again = asyncio.Event()

        async def loop():
            calls.append(1)
            sv.beat("t_return")
            if len(calls) == 1:
                return
            again.set()
            await asyncio.sleep(3600)

        task = asyncio.create_task(sv.supervise("t_return", loop, stall_after=60))
        await asyncio.wait_for(again.wait(), 5)
        assert any("t_return returned — restart #1" in m for m in logs)
        await _stop(task)

    async def test_a_loop_that_returns_before_its_first_beat_is_switched_off(self, redis, logs):
        """MATCH_ENGINE=0 and friends: the loop returns at once, by design.
        Restarting it every five minutes forever would be noise on the page
        and in the log."""
        calls = []

        async def loop():
            calls.append(1)

        task = asyncio.create_task(sv.supervise("t_off", loop, stall_after=60))
        await asyncio.wait_for(task, 5)
        assert calls == [1] and not task.cancelled()
        state = await _state(redis, "t_off")
        assert state["off"] is True and state["restarts"] == 0
        assert any("t_off switched itself off" in m for m in logs)


class TestStall:

    async def test_a_loop_that_stops_beating_is_cancelled_and_restarted(self, redis, logs):
        seen = {"cancelled": 0, "runs": 0}
        second = asyncio.Event()

        async def loop():
            seen["runs"] += 1
            if seen["runs"] == 1:
                sv.beat("t_stall")
                try:
                    await asyncio.sleep(3600)     # wedged: never beats again
                except asyncio.CancelledError:
                    seen["cancelled"] += 1
                    raise
            second.set()
            while True:
                sv.beat("t_stall")
                await asyncio.sleep(0.01)

        task = asyncio.create_task(sv.supervise("t_stall", loop, stall_after=0.3))
        await asyncio.wait_for(second.wait(), 5)
        assert seen["cancelled"] == 1, "the wedged run is cancelled, not left running beside the new one"
        await asyncio.sleep(0.5)
        assert seen["runs"] == 2, "a loop that beats is left alone"
        state = await _state(redis, "t_stall")
        assert state["restarts"] == 1 and state["last_error"].startswith("stalled")
        await _stop(task)

    async def test_the_delay_before_the_first_beat_counts(self, redis):
        """A loop that hangs in its start-up sleep is as stuck as one that
        hangs mid-pass."""
        runs = []

        async def loop():
            runs.append(1)
            await asyncio.sleep(3600)

        task = asyncio.create_task(sv.supervise("t_startup", loop, stall_after=0.05))
        await _until(lambda: len(runs) >= 2)
        await _stop(task)


class TestShutdown:

    async def test_cancelling_the_supervisor_cancels_the_loop_and_propagates(self, redis):
        seen = []
        running = asyncio.Event()

        async def loop():
            sv.beat("t_cancel")
            running.set()
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                seen.append("loop cancelled")
                raise

        task = asyncio.create_task(sv.supervise("t_cancel", loop, stall_after=60))
        await asyncio.wait_for(running.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert task.cancelled() and seen == ["loop cancelled"]


class TestWhatThePageReads:

    async def test_a_running_loop_publishes_its_beat(self, redis):
        async def loop():
            while True:
                sv.beat("t_beat")
                await asyncio.sleep(0.01)

        task = asyncio.create_task(sv.supervise("t_beat", loop, stall_after=60, role="all"))
        await asyncio.sleep(0.2)
        state = await _state(redis, "t_beat")
        assert state["restarts"] == 0 and state["last_error"] is None
        assert abs(state["last_beat"] - time.time()) < 1 and state["started_at"] <= state["last_beat"]
        await _stop(task)

    async def test_redis_down_does_not_stop_the_supervision(self, monkeypatch, logs):
        async def _down():
            raise ConnectionError("redis is down")
        monkeypatch.setattr(database, "get_redis", _down)
        monkeypatch.setattr(sv, "BACKOFF_START", 0.01)
        monkeypatch.setattr(sv, "WRITE_EVERY", 0.01)
        monkeypatch.setattr(sv, "_redis_down", False)
        calls = []
        healthy = asyncio.Event()

        async def loop():
            calls.append(1)
            if len(calls) <= 2:
                raise RuntimeError("x")
            sv.beat("t_noredis")
            healthy.set()
            await asyncio.sleep(3600)

        task = asyncio.create_task(sv.supervise("t_noredis", loop, stall_after=60))
        await asyncio.wait_for(healthy.wait(), 5)
        assert sum("Redis unavailable" in m for m in logs) == 1, "said once per outage"
        await _stop(task)


class TestTheHeartbeat:

    async def test_it_writes_the_process_and_touches_the_file(self, redis, tmp_path, monkeypatch):
        from app.scraper import stealth
        from app.services import scrape_queue
        monkeypatch.setattr(scrape_queue, "draining", False)     # an earlier app in this run drained
        beat_file = tmp_path / "hb"
        monkeypatch.setattr(sv.settings, "heartbeat_file", str(beat_file))
        monkeypatch.setattr(sv, "HEARTBEAT_EVERY", 0.02)
        monkeypatch.setattr(stealth, "sandbox_status", lambda: {"mode": "on"}, raising=False)
        task = asyncio.create_task(sv.heartbeat_loop("worker"))
        await _until(lambda: beat_file.exists())
        await asyncio.sleep(0.05)
        key = f"sf:proc:worker:{sv.HOST}"
        body = json.loads(await redis.get(key))
        assert body["role"] == "worker" and body["pid"] == os.getpid() and body["host"] == sv.HOST
        assert body["draining"] is False and body["running"] == []
        assert body["sandbox"] == {"mode": "on"}
        assert 0 < await redis.ttl(key) <= 60
        first = beat_file.stat().st_mtime_ns
        await _until(lambda: beat_file.stat().st_mtime_ns != first)
        await _stop(task)

    async def test_an_api_process_reports_no_browser(self, redis, tmp_path, monkeypatch):
        from app.scraper import stealth
        monkeypatch.setattr(sv.settings, "heartbeat_file", str(tmp_path / "hb"))
        monkeypatch.setattr(stealth, "sandbox_status", lambda: {"mode": "on"}, raising=False)
        task = asyncio.create_task(sv.heartbeat_loop("api"))
        await _until(lambda: (tmp_path / "hb").exists())
        await asyncio.sleep(0.05)
        body = json.loads(await redis.get(f"sf:proc:api:{sv.HOST}"))
        assert "sandbox" not in body
        await _stop(task)

    async def test_redis_down_the_file_is_still_touched(self, tmp_path, monkeypatch):
        """The kubelet reads the file. A Redis outage must not look like a
        dead worker and get it killed mid-scrape."""
        async def _down():
            raise ConnectionError("redis is down")
        monkeypatch.setattr(database, "get_redis", _down)
        monkeypatch.setattr(sv.settings, "heartbeat_file", str(tmp_path / "hb"))
        monkeypatch.setattr(sv, "HEARTBEAT_EVERY", 0.02)
        task = asyncio.create_task(sv.heartbeat_loop("scheduler"))
        await _until(lambda: (tmp_path / "hb").exists())
        first = (tmp_path / "hb").stat().st_mtime_ns
        await _until(lambda: (tmp_path / "hb").stat().st_mtime_ns != first)
        assert not task.done()
        await _stop(task)
