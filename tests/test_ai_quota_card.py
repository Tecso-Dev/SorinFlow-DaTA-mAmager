"""
What the panel is told while the gateway has no credit (#35).

/ai/status and /ai/overview carry the pause — the gateway's own words, until
when the agents wait, who met the refusal first — and nothing once it has
ended by itself at Tehran midnight. The card's test is the way out: it goes
out while the agents are paused, and an answer lifts the pause. The card's own
code, in the page, is test_ai_pause_card.py. The fakes are in _ai_quota_kit.py.
"""
import asyncio
import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_ai_quota_card.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from _ai_quota_kit import (                                                  # noqa: E402
    GATEWAY_WORDS, OUT_OF_CREDIT, _answer, _chat, _freeze, _gateway, _refuse, open_world, reset_process,
)
from app.services import llm, secret_box                                     # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_process():
    reset_process()


@pytest.fixture
async def world(monkeypatch, tmp_path):
    async with open_world(monkeypatch, tmp_path) as maker:
        yield maker


# ── the card and its routes ──────────────────────────────────────────────────

class TestTheCard:

    def _client(self, world):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api.routes import ai as ai_routes
        from app.database import get_db
        app = FastAPI()
        app.include_router(ai_routes.router, prefix="/ai")

        async def _db():
            async with world() as s:
                yield s
        app.dependency_overrides[get_db] = _db
        app.dependency_overrides[ai_routes._super_admin.dependency] = lambda: SimpleNamespace(username="root", role="root")
        return TestClient(app)

    def test_status_says_nothing_is_paused_until_something_is(self, world):
        r = self._client(world).get("/ai/status")
        assert r.status_code == 200, r.text
        assert r.json()["pause"] is None

    def test_status_and_overview_carry_the_exact_words_and_until_when(self, world, monkeypatch):
        _freeze(monkeypatch, "2026-09-28T12:00:00")
        _gateway(monkeypatch, lambda r: _refuse(402, OUT_OF_CREDIT))

        async def meet():
            with pytest.raises(llm.QuotaExhausted):
                await _chat("vision", "vision")
        asyncio.run(meet())
        c = self._client(world)
        for path in ("/ai/status", "/ai/overview"):
            r = c.get(path)
            assert r.status_code == 200, (path, r.text)
            pause = r.json()["pause"]
            assert pause["message"] == GATEWAY_WORDS, path
            assert pause["until"] == "2026-09-28T20:30:00+00:00" and pause["status"] == 402 and pause["agent"] == "vision"
            assert pause["since"] == "2026-09-28T12:00:00+00:00"

    def test_an_expired_pause_is_not_shown(self, world, monkeypatch):
        async def stale():
            async with world() as db:
                await secret_box.put(db, llm.KEY_PAUSE, json.dumps({
                    "since": "2020-01-01T00:00:00+00:00", "until": "2020-01-02T20:30:00+00:00",
                    "status": 402, "message": "old", "agent": "vision", "job": "vision"}), "t")
        asyncio.run(stale())
        assert self._client(world).get("/ai/status").json()["pause"] is None

    def test_the_test_button_gives_the_gateways_words_when_refused_for_credit_and_lifts_the_pause_when_answered(self, world, monkeypatch):
        c = self._client(world)
        _gateway(monkeypatch, lambda r: _refuse(402, OUT_OF_CREDIT))
        r = c.post("/ai/test")
        assert r.status_code == 502
        assert GATEWAY_WORDS in r.json()["detail"], "the admin reads the gateway's own words on the button that asked"
        assert c.get("/ai/status").json()["pause"]["message"] == GATEWAY_WORDS

        _gateway(monkeypatch, lambda r: _answer("سلام سورین"))
        ok = c.post("/ai/test")
        assert ok.status_code == 200 and ok.json()["pause_cleared"] is True
        assert c.get("/ai/status").json()["pause"] is None

    def test_a_test_refused_for_something_else_keeps_saying_what_the_gateway_said(self, world, monkeypatch):
        c = self._client(world)
        _gateway(monkeypatch, lambda r: _refuse(401, {"error": {"message": "Incorrect API key provided"}}))
        r = c.post("/ai/test")
        assert r.status_code == 502 and "Incorrect API key provided" in r.json()["detail"]
