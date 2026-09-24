"""
Shared fakeredis wiring for tests exercising the Redis-backed state Phase 3
moved off in-process dicts: app/scraper/otp_store.py, the cross-process
profile lock in app/scraper/stealth.py, and the Divar-login registry in
app/api/routes/auth.py.

Not in conftest.py on purpose — see the Phase 3 contract for this stream.
Bare `import _fake_redis` works from any test file the same way
`from test_account_rotation import ...` already does elsewhere in this
suite: pytest's default (no tests/__init__.py) import mode puts each test
file's own directory on sys.path.

A FRESH client per get_redis() call, not one cached instance, is the whole
point of `redis_factory`/`patch_redis` below. Several of these tests mix a
direct `await otp_store.something()` in the test body with an HTTP call
through the app's TestClient, and each runs on its OWN event loop:
TestClient keeps one loop alive for its portal thread for the life of the
`with TestClient(...) as c:` block, while a bare `asyncio.run()` in a test
body opens and tears down a new one every time it is called. fakeredis's
async client binds internal queues to the loop that constructed it, so
handing ONE client to two different loops raises "bound to a different
event loop". A fresh client per call costs nothing — there is no real
socket underneath — and every client built from the same FakeServer shares
one dataset, so state still crosses the loop boundary exactly the way a
real Redis server would.
"""
from typing import Optional

import fakeredis
import fakeredis.aioredis


def fake_server() -> fakeredis.FakeServer:
    return fakeredis.FakeServer()


def redis_factory(server: Optional[fakeredis.FakeServer] = None):
    """A `get_redis`-shaped async callable backed by `server` (a fresh one
    if not given). Pass the same `server` to every factory/patch call that
    needs to see the same data."""
    server = server or fake_server()

    async def _get_redis():
        return fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    return _get_redis


def patch_redis(monkeypatch, *modules, server: Optional[fakeredis.FakeServer] = None):
    """Point every already-imported `get_redis` name at one fake server.

    Each module bound its own `get_redis` reference at import time via
    `from app.database import get_redis` (this codebase's existing
    pattern — see test_login_hardening.py's `client` fixture), so patching
    app.database.get_redis alone would not reach a module that already has
    its own copy of the name. Pass every module under test that touches
    Redis: typically `otp_store`, and `app.api.routes.auth` when the test
    exercises the Divar-login registry.
    """
    get_redis = redis_factory(server)
    for m in modules:
        monkeypatch.setattr(m, "get_redis", get_redis, raising=True)
    return get_redis
