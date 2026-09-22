"""
هوش مصنوعی، فاز ۰ — the one door every agent uses.

The explainer used to call the gateway itself, with its own key reading, its
own error handling and no record of what it spent. app/services/llm.py is
that door now: the connection, the per-job model, the privacy rule, the
ledger and the daily cap — in one place, so an agent added later inherits
all five instead of re-implementing them.
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_ai_core.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.services import llm  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
JS = (ROOT / "frontend/js/app.js").read_text(encoding="utf-8")
HTML = (ROOT / "frontend/index.html").read_text(encoding="utf-8")

_REAL_CLIENT = httpx.AsyncClient


def _gateway(monkeypatch, handler):
    class Fake(_REAL_CLIENT):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **kw)
    monkeypatch.setattr(llm.httpx, "AsyncClient", Fake)


def _answer(content, cost=0.001, toman=300):
    return httpx.Response(200, json={
        "model": "test/model", "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": cost, "total_cost_toman": toman}})


@pytest.fixture
def configured(monkeypatch):
    """A gateway that exists, a ledger that records, and no real session."""
    monkeypatch.setattr(llm.settings, "llm_api_key", "k-test", raising=False)
    monkeypatch.setattr(llm.settings, "llm_base_url", "https://ai.liara.ir/api/6aa50e58b5e9e82406b93188/v1", raising=False)
    monkeypatch.setattr(llm.settings, "llm_model", "openai/gpt-4.1-mini", raising=False)
    monkeypatch.setattr(llm.settings, "llm_model_read", "z-ai/glm-5.3-flash", raising=False)
    monkeypatch.setattr(llm.settings, "llm_model_embed", "openai/text-embedding-3-small", raising=False)
    rows, ledger = {}, []

    async def get_many(_db, keys):
        return {k: rows[k] for k in keys if k in rows}

    async def put(_db, key, value, actor=""):
        rows[key] = value

    async def record(agent, job, model, usage, ms, ok, error=""):
        ledger.append({"agent": agent, "job": job, "model": model, "ok": ok,
                       "cost": float(usage.get("cost") or 0), "error": error})

    async def spent(_db):
        return sum(r["cost"] for r in ledger)

    monkeypatch.setattr(llm.secret_box, "get_many", get_many)
    monkeypatch.setattr(llm.secret_box, "put", put)
    monkeypatch.setattr(llm, "_record", record)
    monkeypatch.setattr(llm, "spent_today", spent)
    return {"rows": rows, "ledger": ledger}


class TestPrivacy:
    """Liara is a third party. The owner's number is the office's asset and
    the customer's number is theirs; neither is needed to score a listing."""

    def test_numbers_and_addresses_never_leave(self):
        out = llm.mask_pii("تماس 09143495300 و ۰۹۱۲۳۴۵۶۷۸۹ دفتر 044-33445566 ایمیل ali@x.ir")
        for secret in ("09143495300", "۰۹۱۲۳۴۵۶۷۸۹", "33445566", "ali@x.ir"):
            assert secret not in out
        assert "تماس" in out and "دفتر" in out, "the sentence survives"

    def test_the_rest_of_the_text_is_untouched(self):
        assert llm.mask_pii("آپارتمان ۱۰۰ متری، ۲ خواب، سال ۱۴۰۰") == "آپارتمان ۱۰۰ متری، ۲ خواب، سال ۱۴۰۰"
        assert llm.mask_pii(None) == "" and llm.mask_pii("") == ""

    def test_every_caller_masks(self):
        src = (ROOT / "app/services/match_service.py").read_text(encoding="utf-8")
        assert src.count("_llm.mask_pii(") >= 4, "titles and the customer's own words"
        assert "headers={\"Authorization\"" not in src, "the explainer no longer talks to the gateway itself"
        assert 'from app.services import llm as _llm' in src


class TestTheGate:

    def test_nothing_configured_is_a_clear_refusal(self, monkeypatch):
        monkeypatch.setattr(llm.settings, "llm_api_key", "", raising=False)
        with pytest.raises(llm.NotConfigured):
            asyncio.run(llm.chat("write", [{"role": "user", "content": "x"}], agent="t", db=object()))

    def test_switched_off_in_the_panel_stops_everything(self, configured, monkeypatch):
        configured["rows"][llm.KEY_ENABLED] = "false"
        _gateway(monkeypatch, lambda r: _answer("no"))
        with pytest.raises(llm.Disabled):
            asyncio.run(llm.chat("write", [{"role": "user", "content": "x"}], agent="t", db=object()))

    def test_the_daily_cap_stops_the_next_call(self, configured, monkeypatch):
        configured["rows"][llm.KEY_CAP] = "0.002"
        _gateway(monkeypatch, lambda r: _answer("سلام", cost=0.0015))
        asyncio.run(llm.chat("write", [{"role": "user", "content": "x"}], agent="t", db=object()))
        asyncio.run(llm.chat("write", [{"role": "user", "content": "x"}], agent="t", db=object()))
        with pytest.raises(llm.BudgetExceeded):
            asyncio.run(llm.chat("write", [{"role": "user", "content": "x"}], agent="t", db=object()))

    def test_the_panel_test_ignores_the_cap(self, configured, monkeypatch):
        configured["rows"][llm.KEY_CAP] = "0"
        _gateway(monkeypatch, lambda r: _answer("سلام سورین"))
        out = asyncio.run(llm.test_connection(object()))
        assert out["ok"] and out["reply"] == "سلام سورین" and out["model"] == "test/model"


class TestTheModels:

    def test_each_job_gets_its_own_model_and_the_panel_wins(self, configured, monkeypatch):
        seen = []

        def handler(req):
            import json
            seen.append(json.loads(req.read())["model"])
            return _answer("ok")
        _gateway(monkeypatch, handler)
        asyncio.run(llm.chat("write", [{"role": "user", "content": "x"}], agent="t", db=object()))
        asyncio.run(llm.chat("read", [{"role": "user", "content": "x"}], agent="t", db=object()))
        assert seen == ["openai/gpt-4.1-mini", "z-ai/glm-5.3-flash"]
        configured["rows"][llm.KEY_MODELS["read"]] = "google/gemini-2.5-flash"
        asyncio.run(llm.chat("read", [{"role": "user", "content": "x"}], agent="t", db=object()))
        assert seen[-1] == "google/gemini-2.5-flash"
        cfg = asyncio.run(llm.config(object()))
        assert cfg["model_sources"] == {"write": "env", "read": "panel", "vision": "env", "embed": "env"}

    def test_the_office_notes_ride_on_what_people_read(self, configured, monkeypatch):
        configured["rows"][llm.KEY_NOTES] = "همیشه بنویس بازدید با هماهنگی قبلی."
        seen = []

        def handler(req):
            import json
            seen.append(json.loads(req.read())["messages"])
            return _answer("ok")
        _gateway(monkeypatch, handler)
        asyncio.run(llm.chat("write", [{"role": "user", "content": "x"}], agent="t", db=object()))
        asyncio.run(llm.chat("read", [{"role": "user", "content": "x"}], agent="t", db=object()))
        assert seen[0][0]["role"] == "system" and "هماهنگی قبلی" in seen[0][0]["content"]
        assert seen[1][0]["role"] == "user", "a reading job gets no office style"


class TestStructuredAnswers:

    def test_a_fenced_json_answer_is_still_json(self, configured, monkeypatch):
        _gateway(monkeypatch, lambda r: _answer('```json\n{"results":[{"id":1,"reason":"ok"}]}\n```'))
        out = asyncio.run(llm.chat("read", [{"role": "user", "content": "x"}], agent="t", db=object(), json_mode=True))
        assert out["data"] == {"results": [{"id": 1, "reason": "ok"}]}

    def test_a_broken_answer_is_retried_once_then_refused(self, configured, monkeypatch):
        calls = []

        def handler(req):
            calls.append(1)
            return _answer("این JSON نیست" if len(calls) == 1 else '{"ok": true}')
        _gateway(monkeypatch, handler)
        out = asyncio.run(llm.chat("read", [{"role": "user", "content": "x"}], agent="t", db=object(), json_mode=True))
        assert out["data"] == {"ok": True} and len(calls) == 2

        calls.clear()
        _gateway(monkeypatch, lambda r: _answer("هنوز JSON نیست"))
        with pytest.raises(llm.LLMError):
            asyncio.run(llm.chat("read", [{"role": "user", "content": "x"}], agent="t", db=object(), json_mode=True))
        assert [r for r in configured["ledger"] if not r["ok"]], "a refusal is on the ledger too"

    def test_a_schema_is_enforced(self, configured, monkeypatch):
        from pydantic import BaseModel

        class Facts(BaseModel):
            floor: int

        _gateway(monkeypatch, lambda r: _answer('{"floor": "سوم"}'))
        with pytest.raises(llm.LLMError):
            asyncio.run(llm.chat("read", [{"role": "user", "content": "x"}], agent="t", db=object(), schema=Facts))
        _gateway(monkeypatch, lambda r: _answer('{"floor": 3}'))
        out = asyncio.run(llm.chat("read", [{"role": "user", "content": "x"}], agent="t", db=object(), schema=Facts))
        assert out["data"] == {"floor": 3}

    def test_the_gateway_refusing_is_an_error_not_a_silent_empty(self, configured, monkeypatch):
        _gateway(monkeypatch, lambda r: httpx.Response(401, json={"error": {"message": "Unauthorized"}}))
        with pytest.raises(llm.LLMError) as e:
            asyncio.run(llm.chat("write", [{"role": "user", "content": "x"}], agent="t", db=object()))
        assert "401" in str(e.value)


class TestTheLedger:

    def test_every_call_is_recorded_with_liaras_own_cost(self, configured, monkeypatch):
        _gateway(monkeypatch, lambda r: _answer("ok", cost=0.0004, toman=127))
        asyncio.run(llm.chat("write", [{"role": "user", "content": "x"}], agent="explainer", db=object()))
        row = configured["ledger"][-1]
        assert row["agent"] == "explainer" and row["job"] == "write" and row["ok"] and row["cost"] == 0.0004

    def test_the_day_starts_in_tehran(self):
        """The cap resets at midnight in Tehran, not in UTC: 20:30 UTC is
        already 00:00 the next day there, so that call belongs to tomorrow."""
        before = datetime(2026, 9, 22, 20, 0, tzinfo=timezone.utc)    # 23:30 Tehran, the 22nd
        after = datetime(2026, 9, 22, 21, 0, tzinfo=timezone.utc)     # 00:30 Tehran, the 23rd
        assert llm._day_start_utc(before) == datetime(2026, 9, 21, 20, 30, tzinfo=timezone.utc)
        assert llm._day_start_utc(after) == datetime(2026, 9, 22, 20, 30, tzinfo=timezone.utc)
        assert llm._day_start_utc(after) - llm._day_start_utc(before) == timedelta(days=1)

    def test_the_table_is_a_guarded_revision(self):
        rev = (ROOT / "migrations/versions/0004_ai_usage.py").read_text(encoding="utf-8")
        assert 'revision = "0004"' in rev and 'down_revision = "0003"' in rev
        assert "insp.get_table_names()" in rev and "op.create_table" in rev
        model = (ROOT / "app/models/ai_usage.py").read_text(encoding="utf-8")
        assert '__tablename__ = "ai_usage"' in model
        for f in ("app/models/__init__.py", "app/database.py", "migrations/env.py",
                  "migrations/versions/0001_baseline.py", "scripts/restore_backup.py"):
            assert "ai_usage" in (ROOT / f).read_text(encoding="utf-8"), f


class TestThePanel:

    def test_the_card_shows_state_never_the_key(self):
        assert 'id="ai-card"' in HTML and 'id="ai-badge"' in HTML
        card = HTML[HTML.index('id="ai-card"'):HTML.index('id="backup-card"')]
        assert "کلید و آدرس سرویس در GitHub" in card
        for job in ("write", "read", "vision", "embed"):
            assert f'id="ai-model-{job}"' in card
        assert 'id="ai-cap"' in card and 'id="ai-notes"' in card and 'id="ai-enabled"' in card
        assert 'onclick="aiTest()"' in card and 'onclick="aiSave()"' in card and 'onclick="aiUsage()"' in card
        assert 'id="ai-key"' not in card and "type=\"password\"" not in card, "the key is not editable here"

    def test_it_loads_with_the_users_section(self):
        assert "loadBackup(); loadAi();" in JS
        fn = JS[JS.index("async function loadAi"):JS.index("async function aiSave")]
        assert "apiCall('/ai/status')" in fn and "cap_reached" in fn
        for bad in ("prompt(", "confirm(", "alert("):
            assert bad not in fn

    def test_the_routes_are_for_the_two_top_roles(self):
        src = (ROOT / "app/api/routes/ai.py").read_text(encoding="utf-8")
        assert '_role_dep("root", "super_admin")' in src
        assert src.count("_super_admin") >= 5, "status, settings, test and usage"
        mounted = (ROOT / "app/api/routes/__init__.py").read_text(encoding="utf-8")
        assert 'router.include_router(ai.router, prefix="/ai"' in mounted


class TestReasoningModels:
    """Liara's GLM thinks inside the same token budget it answers from and its
    reasoning cannot be switched off — with a 300-token budget every answer
    came back empty and the reader burned a pass on «malformed answer»."""

    def test_a_thinking_model_gets_a_floor_and_a_shorter_thought(self, configured, monkeypatch):
        import json
        seen = []

        def handler(req):
            seen.append(json.loads(req.read()))
            return _answer('{"ok": true}')
        _gateway(monkeypatch, handler)
        asyncio.run(llm.chat("read", [{"role": "user", "content": "x"}], agent="t", db=object(), json_mode=True, max_tokens=300))
        body = seen[-1]
        assert body["model"] == "z-ai/glm-5.3-flash"
        assert body["max_tokens"] == llm.REASONING_MIN_TOKENS and body["thinking"] == {"type": "disabled"}
        asyncio.run(llm.chat("write", [{"role": "user", "content": "x"}], agent="t", db=object(), max_tokens=300))
        assert seen[-1]["max_tokens"] == 300 and "thinking" not in seen[-1], "a plain model keeps what it was given"

    def test_an_empty_answer_cut_by_length_is_retried_with_more_room(self, configured, monkeypatch):
        import json
        seen = []

        def handler(req):
            body = json.loads(req.read()); seen.append(body["max_tokens"])
            if len(seen) == 1:
                return httpx.Response(200, json={"model": "z-ai/glm-5.3-flash",
                                                 "choices": [{"finish_reason": "length", "message": {"content": "", "reasoning_content": "…"}}],
                                                 "usage": {"prompt_tokens": 50, "completion_tokens": 1500, "cost": 0.0003}})
            return _answer('{"kind": "shop"}')
        _gateway(monkeypatch, handler)
        out = asyncio.run(llm.chat("read", [{"role": "user", "content": "x"}], agent="t", db=object(), json_mode=True))
        assert out["data"] == {"kind": "shop"} and seen == [1500, 4500]
        assert [r for r in configured["ledger"] if not r["ok"]], "the empty answer is on the ledger as a failure"
