"""
/api/crm/match/property/{id} through the real app — the request must not
wait on the model, even when it is slow.

The caching machinery (fingerprint, lock, quiet-on-a-Redis-outage) is
unit-tested against fakeredis directly in test_match_reasons_cache.py; this
is the one end-to-end proof that the route itself never awaits the model —
with a real (deliberately slow) mocked gateway call, on the real app.
"""
import asyncio
import json
import os
import re
import sys
import time

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_match_reasons_endpoint.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.services import llm  # noqa: E402

_REAL_CLIENT = httpx.AsyncClient
GATEWAY_DELAY = 0.5   # the model "thinking" — long enough that an inline await is unmistakable


def _gateway(monkeypatch, handler):
    class Fake(_REAL_CLIENT):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **kw)
    monkeypatch.setattr(llm.httpx, "AsyncClient", Fake)


def _answer(reasons):
    content = json.dumps({"results": reasons}, ensure_ascii=False)
    return httpx.Response(200, json={
        "model": "test/model", "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 200, "completion_tokens": 60, "cost": 0.0001, "total_cost_toman": 30}})


def _slow_rerank_handler(calls):
    """Stands in for the model: answers with a reason for every candidate id
    the prompt actually named, after a delay — asyncio.sleep, not time.sleep,
    so it yields the loop instead of freezing it (a blocking mock would stall
    every request on this thread, not just the one that scheduled it, which
    would defeat the point of the test). `calls` records when it actually
    ran, so the test can prove it happened after the response, rather than
    guess from a wall-clock budget — request latency in a test environment
    (a fresh asyncpg connection, a cold import) is not something to race."""
    async def handler(req):
        calls.append(time.monotonic())
        await asyncio.sleep(GATEWAY_DELAY)
        body = json.loads(req.read())
        ids = [int(x) for x in re.findall(r"id=(\d+)", body["messages"][-1]["content"])]
        return _answer([{"id": i, "reason": f"دلیل {i}"} for i in ids])
    return handler


@pytest.fixture(scope="module")
def client():
    import fakeredis.aioredis
    import app.database as db
    from app.config import get_settings
    if not str(db.engine.url).startswith("postgresql"):
        pytest.skip("needs Postgres — see test_auth_roles.py", allow_module_level=True)
    cfg = get_settings()
    saved = (cfg.environment, cfg.api_key, cfg.cookies_path, cfg.scrape_scheduler, cfg.match_engine)
    cfg.environment, cfg.api_key = "test", ""
    cfg.cookies_path = "/tmp/sorinflow-test-cookies-match-reasons"
    cfg.scrape_scheduler = False
    cfg.match_engine = False
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)

    async def _get_redis():
        return fake
    db.get_redis = _get_redis
    import app.services.verification as v
    v.get_redis = _get_redis
    from fastapi.testclient import TestClient
    import app.main as m
    with TestClient(m.app) as c:
        yield c
    (cfg.environment, cfg.api_key, cfg.cookies_path, cfg.scrape_scheduler, cfg.match_engine) = saved


def _seed():
    """A consultant, a listing, and one real neighbour for it to be matched
    against — same district, inside the tight price band."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.models.user import User
    from app.models.property import Property
    from app.auth.jwt import get_password_hash

    async def _go():
        eng = create_async_engine(os.environ["DATABASE_URL"])
        maker = async_sessionmaker(eng, expire_on_commit=False)
        try:
            async with maker() as s:
                s.add(User(username="mr_agent", full_name="مشاور", role="admin", permissions=["crm"],
                           hashed_password=get_password_hash("pw123456"), is_active=True))
                target = Property(title="آپارتمان ۱۰۰ متری خیابان گلها", tag_number="mr-1", divar_id="mr-1",
                                  url="https://divar.ir/v/mr-1", city_name="ارومیه", district="خیابان گلها",
                                  area=100, rooms=2, property_type="آپارتمان", listing_type="buy",
                                  total_price=4_500_000_000, is_active=True, phone_number="09141110000")
                neighbour = Property(title="آپارتمان ۹۵ متری خیابان گلها", tag_number="mr-2", divar_id="mr-2",
                                     url="https://divar.ir/v/mr-2", city_name="ارومیه", district="خیابان گلها",
                                     area=95, rooms=2, property_type="آپارتمان", listing_type="buy",
                                     total_price=4_400_000_000, is_active=True, phone_number="09141110001")
                s.add_all([target, neighbour])
                await s.commit()
                return {"target": target.id, "neighbour": neighbour.id}
        finally:
            await eng.dispose()
    return asyncio.run(_go())


def _tok(client, username):
    r = client.post("/api/users/token", data={"username": username, "password": "pw123456"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


class TestTheReasonNeverBlocksTheRequest:

    def test_the_first_call_answers_at_once_and_a_later_one_has_the_reason(self, client, monkeypatch):
        ids = _seed()
        auth = _tok(client, "mr_agent")
        monkeypatch.setattr(llm.settings, "llm_api_key", "k-test", raising=False)
        monkeypatch.setattr(llm.settings, "llm_base_url", "https://ai.example/v1", raising=False)
        monkeypatch.setattr(llm.settings, "llm_model", "test/model", raising=False)
        calls = []
        _gateway(monkeypatch, _slow_rerank_handler(calls))

        r1 = client.get(f"/api/crm/match/property/{ids['target']}", headers=auth)
        assert r1.status_code == 200, r1.text
        data1 = r1.json()
        assert not calls, "the model must not run before the response is ready — an awaited call, not a scheduled one"
        assert data1["items"], "the deterministic ranking shipped without the model"
        assert data1["reasons_pending"] is True
        assert all("ai_reason" not in it for it in data1["items"])

        # a page load right on its heels finds the lock already held: still
        # no wait, still no second model call
        r1b = client.get(f"/api/crm/match/property/{ids['target']}", headers=auth)
        assert r1b.json()["reasons_pending"] is True

        time.sleep(GATEWAY_DELAY * 2)   # the one background call finishes well within this
        r2 = client.get(f"/api/crm/match/property/{ids['target']}", headers=auth)
        assert r2.status_code == 200, r2.text
        data2 = r2.json()
        assert data2["reasons_pending"] is False
        reasoned = [it for it in data2["items"] if it.get("ai_reason")]
        assert reasoned, "the reason arrived from the cache"
        assert reasoned[0]["ai_reason"] == f"دلیل {reasoned[0]['id']}"
        assert len(calls) == 1, "the lock made the burst of three requests cost one model call"
