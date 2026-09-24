"""
Background loops under supervision, and each process's heartbeat.

Every periodic loop used to be a bare asyncio.create_task() in the lifespan.
A loop that raised — or whose `async with async_session_maker()` teardown
raised past the try/except inside it, which is where the matcher, the price
watch, the digest and the AI readers keep theirs — simply ended, and nothing
said so: the feature stopped for the life of the process while /health stayed
green. supervise() restarts a loop that raised or returned, cancels and
restarts one that stopped beating, and writes down what it saw where the
monitoring page reads it.

Redis keys (GET /api/monitoring/runtime reads them):
  sf:loops               hash, one field per loop name: JSON with role, host,
                         last_beat, restarts, last_error (the exception's type
                         and line — never its message, which can carry a DSN,
                         a phone number or a token), last_error_at,
                         started_at, stall_after, off.
  sf:proc:{role}:{host}  one per process, TTL 60, rewritten every 15 s.
"""
import asyncio
import inspect
import json
import os
import socket
import time
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, Optional

from loguru import logger

from app import database
from app.config import get_settings

settings = get_settings()

HOST = socket.gethostname()
LOOPS_KEY = "sf:loops"
PROC_KEY = "sf:proc:{role}:{host}"

BACKOFF_START = 1.0      # seconds before the first restart, doubling …
BACKOFF_MAX = 300.0      # … up to five minutes
HEALTHY_AFTER = 600.0    # a run this long starts the backoff over at 1 s
WRITE_EVERY = 30.0       # a loop's sf:loops entry is rewritten at most this often, and on every restart
HEARTBEAT_EVERY = 15.0
PROC_TTL = 60

_beats: Dict[str, float] = {}     # loop name → time.monotonic() of its last beat
_redis_down = False


def beat(name: str) -> None:
    """Called once per iteration by every supervised loop. A dict write and
    nothing more: it runs on every pass of every loop, and the supervisor is
    the one that talks to Redis."""
    _beats[name] = time.monotonic()


def _redis_failed(what: str, e: Exception) -> None:
    """Once per outage, not once per loop every thirty seconds."""
    global _redis_down
    if not _redis_down:
        _redis_down = True
        logger.warning(f"[supervisor] Redis unavailable ({what}: {type(e).__name__}) — "
                       "loop and process state is not published until it is back")


def _redis_ok() -> None:
    global _redis_down
    if _redis_down:
        _redis_down = False
        logger.info("[supervisor] Redis is back — loop and process state published again")


def _where(exc: BaseException) -> str:
    """The exception's type and the line that raised it — never str(exc).
    This string is kept in Redis and shown on a page."""
    tb = exc.__traceback__
    while tb is not None and tb.tb_next is not None:
        tb = tb.tb_next
    at = f" at {Path(tb.tb_frame.f_code.co_filename).name}:{tb.tb_lineno}" if tb else ""
    return f"{type(exc).__name__}{at}"


async def _publish(name: str, state: dict) -> None:
    try:
        # Any: redis-py types its asyncio client with the sync client's
        # «value or awaitable» unions, which no await can satisfy.
        r: Any = await database.get_redis()
        await r.hset(LOOPS_KEY, name, json.dumps(state))
        _redis_ok()
    except Exception as e:
        _redis_failed(LOOPS_KEY, e)


async def supervise(name: str, fn: Callable[[], Coroutine[Any, Any, None]], stall_after: float,
                    role: str = "all") -> None:
    """Run fn() for the life of the process.

    Raised or returned: restarted after 1 s, 2 s, 4 s … up to 5 min, and a
    run that then lasts 10 min starts the count over. No beat(name) for
    stall_after seconds: cancelled and restarted the same way. Returned
    before its first beat: the loop switched itself off by its own setting
    (MATCH_ENGINE=0, DIVAR_SESSION_CHECK_MINUTES=0 …) — recorded as off, not
    restarted. Cancelling the supervisor cancels the loop and propagates.
    """
    state: Dict[str, Any] = {"role": role, "host": HOST, "stall_after": stall_after,
                             "restarts": 0, "last_beat": None, "last_error": None,
                             "last_error_at": None, "started_at": None, "off": False}
    delay = BACKOFF_START
    while True:
        _beats.pop(name, None)
        started = time.monotonic()
        state["started_at"] = time.time()
        task = asyncio.create_task(fn(), name=f"loop:{name}")
        try:
            why = await _watch(name, task, stall_after, started, state)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.wait({task})
            if not task.cancelled():
                task.exception()          # retrieved: asyncio must not warn about it later
        if why is None:
            state["off"] = True
            await _publish(name, state)
            logger.info(f"[supervisor] {name} switched itself off — not restarting it")
            return
        if time.monotonic() - started >= HEALTHY_AFTER:
            delay = BACKOFF_START
        state["restarts"] += 1
        state["last_error"], state["last_error_at"] = why, time.time()
        await _publish(name, state)
        logger.warning(f"[supervisor] {name} {why} — restart #{state['restarts']} in {delay:g}s")
        await asyncio.sleep(delay)
        delay = min(delay * 2, BACKOFF_MAX)


async def _watch(name: str, task: asyncio.Task, stall_after: float, started: float,
                 state: dict) -> Optional[str]:
    """Wait until the loop ends or stalls, publishing its state on the way.
    Returns why it must be restarted, or None when it switched itself off."""
    poll = min(WRITE_EVERY, max(stall_after / 4, 0.01))
    published = None
    while True:
        done, _ = await asyncio.wait({task}, timeout=poll)
        now = time.monotonic()
        last = _beats.get(name)
        if last is not None:
            state["last_beat"] = time.time() - (now - last)
        if done:
            if task.cancelled():
                return "was cancelled"
            exc = task.exception()
            if exc is not None:
                logger.opt(exception=exc).error(f"[supervisor] {name} raised {type(exc).__name__}")
                return f"raised {_where(exc)}"
            return "returned" if last is not None else None
        silent = now - (last if last is not None else started)
        if silent > stall_after:
            return f"stalled — no beat for {int(silent)}s"
        if published is None or now - published >= WRITE_EVERY:
            await _publish(name, state)
            published = now


async def _sandbox_status():
    """Chromium's sandbox as the scraper reports it, once it reports it."""
    try:
        from app.scraper import stealth
        fn = getattr(stealth, "sandbox_status", None)
        if fn is None:
            return None
        value = fn()
        return await value if inspect.isawaitable(value) else value
    except Exception as e:
        return f"unknown ({type(e).__name__})"


async def heartbeat_loop(role: str) -> None:
    """This process, every 15 s: sf:proc:{role}:{host} in Redis (TTL 60, so a
    process that stops writing leaves the page within a minute) and the mtime
    of HEARTBEAT_FILE, which the worker's and the scheduler's liveness probe
    reads. Neither failing ends it — a Redis outage must not look like a dead
    process to the kubelet, so the file is touched first and on its own."""
    from app.services import scrape_queue

    key = PROC_KEY.format(role=role, host=HOST)
    started_at = time.time()
    file_warned = False
    while True:
        try:
            await asyncio.to_thread(Path(settings.heartbeat_file).touch)
        except OSError as e:
            if not file_warned:
                file_warned = True
                logger.warning(f"[heartbeat] cannot touch {settings.heartbeat_file}: {type(e).__name__}")
        try:
            body = {"role": role, "host": HOST, "pid": os.getpid(), "started_at": started_at,
                    "at": time.time(), "draining": scrape_queue.draining,
                    "running": scrape_queue.running_ids()}
            if role in ("all", "worker"):
                sandbox = await _sandbox_status()
                if sandbox is not None:
                    body["sandbox"] = sandbox
            r: Any = await database.get_redis()
            await r.set(key, json.dumps(body, default=str), ex=PROC_TTL)
            _redis_ok()
        except Exception as e:
            _redis_failed("heartbeat", e)
        await asyncio.sleep(HEARTBEAT_EVERY)
