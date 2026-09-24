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

    async def spent(_db, agent=None):
        return sum(r["cost"] for r in ledger if not agent or r["agent"] == agent)

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
        # a generous cap of its own, so only the shared cap below is what stops it
        configured["rows"][llm.agent_cap_key("explainer")] = "100"
        _gateway(monkeypatch, lambda r: _answer("سلام", cost=0.0015))
        asyncio.run(llm.chat("write", [{"role": "user", "content": "x"}], agent="explainer", db=object()))
        asyncio.run(llm.chat("write", [{"role": "user", "content": "x"}], agent="explainer", db=object()))
        with pytest.raises(llm.BudgetExceeded):
            asyncio.run(llm.chat("write", [{"role": "user", "content": "x"}], agent="explainer", db=object()))

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
        # the card lives on the AI screen now, not on the users page
        assert HTML.index('id="section-ai"') < HTML.index('id="ai-card"') < HTML.index('id="section-monitoring"')
        card = HTML[HTML.index('id="ai-card"'):HTML.index('id="section-monitoring"')]
        assert "کلید و آدرس سرویس در GitHub" in card
        for job in ("write", "read", "vision", "embed"):
            assert f'id="ai-model-{job}"' in card
        assert 'id="ai-cap"' in card and 'id="ai-notes"' in card and 'id="ai-enabled"' in card
        assert 'onclick="aiTest()"' in card and 'onclick="aiSave()"' in card and 'onclick="aiUsage()"' in card
        assert 'id="ai-key"' not in card and "type=\"password\"" not in card, "the key is not editable here"

    def test_it_loads_with_its_own_section(self):
        assert "loadAiScreen(); loadAi(); loadAiLog(); loadAiChats();" in JS
        assert "'nav-link-ai': ['root', 'super_admin']" in JS, "the screen is the two top roles'"
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


class TestTheAiScreen:
    """«مدیریت این ایجنت‌ها و مانیتورینگ و لاگ آن‌ها» — one screen with every
    agent, where it is used, how far it has come, what it cost, and every call
    it made. Root and super_admin only, like the users page."""

    def test_the_section_and_its_nav_are_role_gated(self):
        assert 'id="section-ai"' in HTML and 'id="nav-link-ai"' in HTML and "showSection('ai')" in HTML
        assert "'nav-link-ai': ['root', 'super_admin']" in JS
        assert "'monitoring', 'ai', 'sms'" in JS, "the hash route exists"

    def test_every_agent_is_named_with_where_it_is_used(self):
        src = (ROOT / "app/api/routes/ai.py").read_text(encoding="utf-8")
        cards = src[src.index("AGENT_CARDS = ["):src.index('@router.get("/overview")')]
        for key in ("explainer", "reader", "need", "embed", "vision", "assistant"):
            assert f'"key": "{key}"' in cards, key
        assert cards.count('"where":') == 6, "each card says where in the panel it is used"
        assert "CRM ← ملک‌های مشابه" in cards and "فرم مشتری ← پر کردن از متن" in cards
        assert "تلگرام (چت‌های بکاپ)" in cards and "هوش تصویری" in cards

    def test_one_request_draws_the_screen(self):
        src = (ROOT / "app/api/routes/ai.py").read_text(encoding="utf-8")
        fn = src[src.index('@router.get("/overview")'):src.index("async def _embed_state")]
        for piece in ('"agents"', '"quota"', '"liara"', '"usage"', "agents_enabled", "_usage_by_agent_today"):
            assert piece in fn, piece
        assert "loadAiScreen" in JS and "apiCall('/ai/overview')" in JS

    def test_an_agent_can_be_stopped_without_stopping_the_rest(self):
        assert "async def agent_enabled(" in (ROOT / "app/services/llm.py").read_text(encoding="utf-8")
        for f, key in (("app/ai/listing_reader.py", "reader"), ("app/ai/embeddings.py", "embed"),
                       ("app/ai/photo_tagger.py", "vision")):
            src = (ROOT / f).read_text(encoding="utf-8")
            assert f'llm.agent_enabled(db, "{key}")' in src, f
        src = (ROOT / "app/api/routes/ai.py").read_text(encoding="utf-8")
        assert '@router.put("/agents/{key}")' in src and "llm.AGENTS" in src
        assert 'onchange="aiAgentToggle(' in JS and "apiCall(`/ai/agents/" in JS

    def test_the_log_is_filterable_and_the_failures_are_visible(self):
        src = (ROOT / "app/api/routes/ai.py").read_text(encoding="utf-8")
        fn = src[src.index('@router.get("/log")'):src.index('@router.put("/settings")')]
        assert "failed_only" in fn and "AiUsage.ok.is_(False)" in fn and "AiUsage.agent == agent" in fn
        assert 'id="ai-log-agent"' in HTML and 'id="ai-log-failed"' in HTML and 'id="ai-log-rows"' in HTML
        assert "ai-log-bad" in JS and "loadAiLog" in JS

    def test_a_background_agent_can_be_run_by_hand(self):
        fn = JS[JS.index("async function aiRunAgent"):JS.index("async function loadAiLog")]
        for url in ("/ai/reader/run", "/ai/embed/run", "/ai/photo/run"):
            assert url in fn, url
        assert "r.read ?? r.embedded ?? r.tagged" in fn

    def test_liaras_own_free_quota_is_shown(self):
        src = (ROOT / "app/services/llm.py").read_text(encoding="utf-8")
        fn = src[src.index("async def liara_quota"):src.index("async def liara_activity")]
        assert "free-tokens" in fn and "workspaces/" in fn and "liara_api_token" in fn
        assert 'id="ai-t-quota"' in HTML and "remainingPromptFreeTokens" in JS

    def test_a_failure_count_says_whether_it_is_still_failing(self):
        """36 of the 40 failures on the first live day came from a model that
        was replaced the same afternoon. A bare «۲۴ خطا» on the card reads as
        «broken now»; it has to say what happened after them."""
        src = (ROOT / "app/api/routes/ai.py").read_text(encoding="utf-8")
        fn = src[src.index("async def _usage_by_agent_today"):src.index("class AgentSwitchIn")]
        for piece in ("last_error_at", "ok_since_error", "last_error", "AiUsage.created_at > last_bad"):
            assert piece in fn, piece
        card = JS[JS.index("function _aiAgentCard"):JS.index("async function loadAiScreen")]
        assert "ok_since_error" in card and "is-stale" in card and "aiShowErrors(" in card
        fn2 = JS[JS.index("function aiShowErrors"):JS.index("async function loadAiLog")]
        assert "ai-log-failed" in fn2 and "ai-log-agent" in fn2 and "loadAiLog()" in fn2
        assert ".ai-err.is-stale" in (ROOT / "frontend/css/style.css").read_text(encoding="utf-8")


# ── phase 2: a budget per agent, on top of the shared one ───────────────────

class TestPerAgentBudget:
    """A separate daily budget per agent, so a noisy reader cannot spend the
    whole day and the assistant keeps answering. The shared cap still bounds
    the office as a whole, on top."""

    def test_each_agent_defaults_to_half_the_global_cap(self, configured):
        configured["rows"][llm.KEY_CAP] = "4.00"
        cfg = asyncio.run(llm.config(object()))
        assert cfg["agent_caps"] == {a: 2.0 for a in llm.AGENTS}

    def test_a_panel_override_wins_over_the_default(self, configured):
        configured["rows"][llm.KEY_CAP] = "4.00"
        configured["rows"][llm.agent_cap_key("reader")] = "0.75"
        cfg = asyncio.run(llm.config(object()))
        assert cfg["agent_caps"]["reader"] == 0.75
        assert cfg["agent_caps"]["explainer"] == 2.0, "an untouched agent still gets the default share"

    def test_an_agents_own_cap_stops_only_that_agent(self, configured, monkeypatch):
        configured["rows"][llm.KEY_CAP] = "10"
        configured["rows"][llm.agent_cap_key("reader")] = "0.001"
        _gateway(monkeypatch, lambda r: _answer("سلام", cost=0.002))
        asyncio.run(llm.chat("read", [{"role": "user", "content": "x"}], agent="reader", db=object()))
        with pytest.raises(llm.BudgetExceeded):
            asyncio.run(llm.chat("read", [{"role": "user", "content": "x"}], agent="reader", db=object()))
        # a different agent, same day: its own cap (half of 10 = 5) is untouched
        out = asyncio.run(llm.chat("write", [{"role": "user", "content": "x"}], agent="explainer", db=object()))
        assert out["content"] == "سلام"

    def test_the_message_is_persian_and_names_the_agent_and_both_figures(self, configured, monkeypatch):
        configured["rows"][llm.KEY_CAP] = "10.00"
        configured["rows"][llm.agent_cap_key("reader")] = "0.50"
        _gateway(monkeypatch, lambda r: _answer("سلام", cost=0.60))
        asyncio.run(llm.chat("read", [{"role": "user", "content": "x"}], agent="reader", db=object()))
        with pytest.raises(llm.BudgetExceeded) as e:
            asyncio.run(llm.chat("read", [{"role": "user", "content": "x"}], agent="reader", db=object()))
        msg = str(e.value)
        assert "reader" in msg and "دلار" in msg and "0.60" in msg and "0.50" in msg, msg

    def test_the_global_cap_still_applies_on_top(self, configured, monkeypatch):
        configured["rows"][llm.KEY_CAP] = "0.001"
        _gateway(monkeypatch, lambda r: _answer("سلام", cost=0.002))
        asyncio.run(llm.chat("write", [{"role": "user", "content": "x"}], agent="explainer", db=object()))
        with pytest.raises(llm.BudgetExceeded) as e:
            # "need" has spent nothing itself — only the shared cap stops it
            asyncio.run(llm.chat("write", [{"role": "user", "content": "x"}], agent="need", db=object()))
        assert "کل" in str(e.value)

    def test_the_endpoint_validates_the_range_and_shares_the_guard(self):
        src = (ROOT / "app/api/routes/ai.py").read_text(encoding="utf-8")
        assert '@router.put("/agents/{key}/cap")' in src
        fn = src[src.index("class AgentCapIn"):src.index('@router.get("/log")')]
        assert "ge=0, le=100" in fn and "_super_admin" in fn and "agent_cap_key" in fn

    def test_the_card_shows_and_edits_each_agents_budget(self):
        card = JS[JS.index("function _aiAgentCard"):JS.index("async function loadAiScreen")]
        assert "cap_usd" in card and "aiAgentCapEdit(" in card
        fn = JS[JS.index("async function aiAgentCapEdit"):JS.index("async function aiRunAgent")]
        assert "/cap`" in fn and "askText(" in fn
        for bad in ("prompt(", "confirm(", "alert("):
            assert bad not in fn


@pytest.fixture
def real_db(tmp_path):
    """A sqlite database of its own with just the tables llm.py touches, and
    nothing monkeypatched in front of it — for the handful of tests that
    check the real SQL rather than the `configured` fixture's fake ledger."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool
    from app.database import Base
    from app.models.ai_usage import AiUsage
    from app.models.app_setting import AppSetting
    tables = [t.__table__ for t in (AppSetting, AiUsage)]
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/core.db", poolclass=NullPool)

    async def _build():
        async with eng.begin() as c:
            await c.run_sync(lambda sc: Base.metadata.create_all(sc, tables=tables))
    asyncio.run(_build())
    maker = async_sessionmaker(eng, expire_on_commit=False)
    yield maker
    asyncio.run(eng.dispose())


class TestSpentTodayFiltersByAgent:
    """The `agent` filter is real SQL, not just a fixture's fake — checked
    against an actual (small, throwaway) database."""

    def test_the_real_query_sums_only_that_agent(self, real_db):
        from app.models.ai_usage import AiUsage

        async def _run():
            async with real_db() as s:
                s.add_all([
                    AiUsage(agent="reader", job="read", cost_usd=0.5, ok=True),
                    AiUsage(agent="reader", job="read", cost_usd=0.25, ok=True),
                    AiUsage(agent="explainer", job="write", cost_usd=1.0, ok=True),
                ])
                await s.commit()
                assert await llm.spent_today(s, agent="reader") == pytest.approx(0.75)
                assert await llm.spent_today(s, agent="explainer") == pytest.approx(1.0)
                assert await llm.spent_today(s, agent="vision") == 0.0
                assert await llm.spent_today(s) == pytest.approx(1.75), "no agent = everyone, as before"
        asyncio.run(_run())


class TestConfigAgentCapsAgainstARealDatabase:

    def test_the_real_round_trip_through_secret_box(self, real_db, monkeypatch):
        monkeypatch.setattr(llm.settings, "llm_model", "openai/gpt-4.1-mini", raising=False)

        async def _run():
            async with real_db() as s:
                await llm.secret_box.put(s, llm.KEY_CAP, "6.00", "tester")
                await llm.secret_box.put(s, llm.agent_cap_key("vision"), "1.00", "tester")
                cfg = await llm.config(s)
                assert cfg["agent_caps"]["vision"] == 1.00
                assert cfg["agent_caps"]["reader"] == 3.00   # half of 6, no override on file
        asyncio.run(_run())
