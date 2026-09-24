"""
GET /api/monitoring/runtime — the processes, their loops and the scrape
queue, as each process reports itself to Redis. The api, the worker and the
scheduler are separate processes now; this is where they show up together.
"""
import json
import os
import sys
import time
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_runtime_page.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

import fakeredis  # noqa: E402
import fakeredis.aioredis  # noqa: E402

import app.main as main  # noqa: E402
from app import database  # noqa: E402
from app.services import scrape_queue, supervisor  # noqa: E402


@pytest.fixture
def redis(monkeypatch):
    fake = fakeredis.aioredis.FakeRedis(server=fakeredis.FakeServer(), decode_responses=True)

    async def _get():
        return fake
    monkeypatch.setattr(database, "get_redis", _get)
    return fake


async def test_it_shows_what_the_processes_wrote(redis):
    from app.api.routes import monitoring
    now = time.time()
    await redis.set("sf:proc:worker:w-1", json.dumps(
        {"role": "worker", "host": "w-1", "pid": 1, "started_at": now - 600, "at": now - 5,
         "draining": True, "running": ["a"], "sandbox": {"mode": "on"}}), ex=60)
    await redis.set("sf:proc:api:api-1", json.dumps(
        {"role": "api", "host": "api-1", "pid": 1, "started_at": now - 60, "at": now - 1,
         "draining": False, "running": []}), ex=60)
    loops = {
        "fresh": {"last_beat": now - 10, "stall_after": 60, "restarts": 0, "off": False},
        "stuck": {"last_beat": now - 1000, "stall_after": 600, "restarts": 2, "off": False},
        "asleep": {"last_beat": None, "started_at": now - 30, "stall_after": 600, "off": False},
        "switched_off": {"last_beat": None, "started_at": now - 9000, "stall_after": 60, "off": True},
    }
    for name, state in loops.items():
        await redis.hset(supervisor.LOOPS_KEY, name, json.dumps({"role": "scheduler", **state}))
    await redis.lpush(scrape_queue.QUEUE, "q1", "q2")
    await redis.set(scrape_queue.CLAIM.format("job-1"), "w-1:1:abc", ex=90)

    d = await monitoring.runtime()
    assert [p["role"] for p in d["processes"]] == ["api", "worker"]
    worker = d["processes"][1]
    assert worker["draining"] is True and worker["sandbox"] == {"mode": "on"}
    assert 4 <= worker["age_seconds"] < 30
    stale = {loop["name"]: loop["stale"] for loop in d["loops"]}
    assert stale == {"asleep": False, "fresh": False, "stuck": True, "switched_off": False}
    assert d["queue_length"] == 2
    assert d["running"] == [{"job_id": "job-1", "worker": "w-1:1:abc"}]


async def test_redis_down_is_said_not_crashed(monkeypatch):
    from fastapi import HTTPException
    from app.api.routes import monitoring

    async def _down():
        raise ConnectionError("redis is down")
    monkeypatch.setattr(database, "get_redis", _down)
    with pytest.raises(HTTPException) as e:
        await monitoring.runtime()
    assert e.value.status_code == 503 and "Redis" in e.value.detail


@pytest.mark.parametrize("role,perms,status", [
    ("admin", ["monitoring"], 403), ("super_admin", [], 200), ("root", [], 200)])
def test_only_root_and_super_admin_see_it(redis, role, perms, status):
    """It names hosts and job ids; an admin with the monitoring page still
    sees the rest of it."""
    from fastapi.testclient import TestClient
    from app.auth.dependencies import get_current_user
    main.app.dependency_overrides[get_current_user] = \
        lambda: SimpleNamespace(id=1, role=role, permissions=perms, is_active=True)
    try:
        r = TestClient(main.app).get("/api/monitoring/runtime")
    finally:
        main.app.dependency_overrides.pop(get_current_user, None)
    assert r.status_code == status, r.text


class TestThePanel:
    """The card itself is exercised in tests/js/panel_escaping.mjs against
    the real app.js; this only checks it is wired to the page."""

    def test_the_card_is_on_the_monitoring_page_and_refreshes_with_it(self):
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent
        html = (root / "frontend/index.html").read_text(encoding="utf-8")
        js = (root / "frontend/js/app.js").read_text(encoding="utf-8")
        section = html[html.index('id="section-monitoring"'):]
        assert 'id="mon-runtime-card"' in section and 'class="card mb-3 d-none"' in section
        loader = js[js.index("async function loadMonitoring("):js.index("const GCP_STATE_FA")]
        assert "loadRuntime()" in loader
        card = js[js.index("async function loadRuntime("):]
        assert "['root', 'super_admin'].includes(_currentUser?.role)" in card[:600]
