"""
دستیار دفتر «سورین» — the office asks its own database, in Telegram.

The only real agent: the model picks a tool, reads what comes back, and
answers. Its power is exactly its tools — every one a read, none carrying a
phone number out — and who may ask is a linked panel user, in private
(tests/test_assistant_per_user.py has the links, the scope and the names).
"""
import asyncio
import json
import os
import re
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_ai_assistant.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.services import llm  # noqa: E402
from app.ai import assistant  # noqa: E402
from app.models.user import User  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
# the asker: the tools are stubbed here, so only the account's standing matters
SOBHAN = User(id=1, username="sobhan", full_name="سبحان", role="root", is_active=True, permissions=[])
JS = (ROOT / "frontend/js/app.js").read_text(encoding="utf-8")
HTML = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
_REAL_CLIENT = httpx.AsyncClient


def _gateway(monkeypatch, handler):
    class Fake(_REAL_CLIENT):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **kw)
    monkeypatch.setattr(llm.httpx, "AsyncClient", Fake)


def _turn(content=None, tool_calls=None):
    # as Liara answers: the nulls it adds are what broke the second round
    msg = {"role": "assistant", "content": content, "refusal": None, "reasoning": None}
    if tool_calls:
        msg["tool_calls"] = [{**c, "index": 0} for c in tool_calls]
    return httpx.Response(200, json={"model": "test/model", "choices": [{"finish_reason": "tool_calls" if tool_calls else "stop", "message": msg}],
                                     "usage": {"prompt_tokens": 20, "completion_tokens": 10, "cost": 0.0002, "total_cost_toman": 60}})


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(llm.settings, "llm_api_key", "k-test", raising=False)
    monkeypatch.setattr(llm.settings, "llm_base_url", "https://ai.liara.ir/api/6aa50e58b5e9e82406b93188/v1", raising=False)
    monkeypatch.setattr(llm.settings, "llm_model", "openai/gpt-4.1-mini", raising=False)
    rows, ledger = {}, []

    async def get_many(_db, keys):
        return {k: rows[k] for k in keys if k in rows}

    async def put(_db, key, value, actor=""):
        rows[key] = value

    async def record(agent, job, model, usage, ms, ok, error=""):
        ledger.append({"agent": agent, "ok": ok})

    async def spent(_db, agent=None):
        return 0.0
    monkeypatch.setattr(llm.secret_box, "get_many", get_many)
    monkeypatch.setattr(llm.secret_box, "put", put)
    monkeypatch.setattr(assistant.secret_box, "get_many", get_many)
    monkeypatch.setattr(assistant.secret_box, "put", put)
    monkeypatch.setattr(llm, "_record", record)
    monkeypatch.setattr(llm, "spent_today", spent)

    async def no_customers(_db, _user):
        return {}
    monkeypatch.setattr(assistant, "_customer_names", no_customers)
    return {"rows": rows, "ledger": ledger}


class _Db:
    """A session that records what the assistant writes and never touches a
    database — the tools are stubbed in these tests."""
    def __init__(self):
        self.added = []

    def add(self, row):
        self.added.append(row)

    async def commit(self):
        pass

    async def rollback(self):
        pass


def _linked(monkeypatch, accounts):
    """Telegram account id → the panel user it is linked to."""
    async def linked_user(_db, telegram_user_id):
        return accounts.get(int(telegram_user_id))
    monkeypatch.setattr(assistant, "linked_user", linked_user)


class TestTheConversation:

    def test_the_model_reads_a_tool_and_answers_from_it(self, configured, monkeypatch):
        seen = []

        async def fake_queue(db, user):
            return {"calls_due": 18, "matches_waiting": 3, "price_drops_new": 1, "listings_today": 40, "listings_active": 812}
        monkeypatch.setitem(assistant._TOOL_FUNCS, "queue_status", fake_queue)

        def handler(req):
            body = json.loads(req.read()); seen.append(body)
            if len(seen) == 1:
                assert body["tools"] and body["messages"][0]["role"] == "system" and assistant.NAME in body["messages"][0]["content"]
                return _turn(tool_calls=[{"id": "c1", "type": "function", "function": {"name": "queue_status", "arguments": "{}"}}])
            turn = body["messages"][-2]
            assert turn["role"] == "assistant" and turn["tool_calls"][0]["id"] == "c1"
            assert "refusal" not in turn and "reasoning" not in turn and "index" not in turn["tool_calls"][0], \
                "the gateway refuses its own nulls when they are echoed back"
            tool_msg = body["messages"][-1]
            assert tool_msg["role"] == "tool" and tool_msg["tool_call_id"] == "c1" and '"calls_due": 18' in tool_msg["content"]
            return _turn(content="۱۸ تماس در صف است و ۳ تطبیق منتظر.")
        _gateway(monkeypatch, handler)
        db = _Db()
        out = asyncio.run(assistant.answer(db, "صف تماس چطوره؟", user=SOBHAN, chat_id="542901635"))
        assert out["text"] == "۱۸ تماس در صف است و ۳ تطبیق منتظر." and out["tools"] == ["queue_status"] and out["ok"]
        row = db.added[-1]
        assert row.question == "صف تماس چطوره؟" and row.answer == out["text"] and row.tools == ["queue_status"] and row.chat_id == "542901635"
        assert row.who == "sobhan", "who asked is the panel account, not whatever name Telegram shows"
        assert all(r["agent"] == "assistant" for r in configured["ledger"])

    def test_after_the_last_round_the_model_must_speak(self, configured, monkeypatch):
        calls = []

        async def fake_queue(db, user):
            return {"calls_due": 1}
        monkeypatch.setitem(assistant._TOOL_FUNCS, "queue_status", fake_queue)

        def handler(req):
            body = json.loads(req.read()); calls.append("tools" in body)
            if "tools" in body:
                return _turn(tool_calls=[{"id": f"c{len(calls)}", "type": "function", "function": {"name": "queue_status", "arguments": "{}"}}])
            return _turn(content="جواب نهایی")
        _gateway(monkeypatch, handler)
        out = asyncio.run(assistant.answer(_Db(), "x", user=SOBHAN))
        assert out["text"] == "جواب نهایی" and len(out["tools"]) == assistant.MAX_ROUNDS
        assert calls == [True] * assistant.MAX_ROUNDS + [False], "the final turn carries no tools, so it cannot stall"

    def test_a_bad_tool_name_or_arguments_is_an_answer_not_a_crash(self):
        out = asyncio.run(assistant.run_tool(_Db(), SOBHAN, "delete_everything", "{}"))
        assert "وجود ندارد" in out["error"]
        out = asyncio.run(assistant.run_tool(_Db(), SOBHAN, "property", "not json"))
        assert "error" in out

    def test_the_question_and_the_tool_results_are_masked(self, configured, monkeypatch):
        seen = []

        async def fake_prop(db, user, serial_no):
            return {"found": True, "title": "x", "owner_phone_leak": "09141112233"}
        monkeypatch.setitem(assistant._TOOL_FUNCS, "property", fake_prop)

        def handler(req):
            body = json.loads(req.read()); seen.append(body)
            if len(seen) == 1:
                return _turn(tool_calls=[{"id": "c1", "type": "function", "function": {"name": "property", "arguments": '{"serial_no": 5}'}}])
            return _turn(content="ok")
        _gateway(monkeypatch, handler)
        asyncio.run(assistant.answer(_Db(), "ملک ۵ رو بده، شماره‌م 09121234567", user=SOBHAN))
        assert "09121234567" not in seen[0]["messages"][-1]["content"]
        assert "09141112233" not in seen[1]["messages"][-1]["content"], "a tool result never carries a number out"

    def test_the_model_being_down_is_a_polite_answer(self, configured, monkeypatch):
        _gateway(monkeypatch, lambda r: httpx.Response(503, text="down"))
        db = _Db()
        out = asyncio.run(assistant.answer(db, "x", user=SOBHAN))
        assert not out["ok"] and "دسترسی ندارم" in out["text"] and db.added[-1].ok is False


class TestTelegram:

    def test_only_a_linked_user_is_answered_and_every_chat_is_remembered(self, configured, monkeypatch):
        sent = []

        async def fake_tg(token, method, route, **kw):
            sent.append((method, (kw.get("json") or {}).get("chat_id")))
            return httpx.Response(200, json={"ok": True}), None
        from app.services import backup_service as bk
        monkeypatch.setattr(bk, "tg_request", fake_tg)

        async def fake_answer(db, text, *, user, chat_id=""):
            assert user is SOBHAN, "the question is read with the linked account's rights"
            return {"text": "جواب", "tools": [], "ms": 1, "ok": True}
        monkeypatch.setattr(assistant, "answer", fake_answer)
        _linked(monkeypatch, {542901635: SOBHAN})
        db = _Db()
        stranger = {"update_id": 1, "message": {"chat": {"id": 999, "first_name": "غریبه", "type": "private"}, "from": {"id": 999}, "text": "سلام"}}
        friend = {"update_id": 2, "message": {"chat": {"id": 542901635, "first_name": "سبحان", "type": "private"}, "from": {"id": 542901635, "first_name": "سبحان"}, "text": "صف تماس؟"}}
        assert asyncio.run(assistant.handle_update(db, stranger, token="t", route={})) is None
        assert asyncio.run(assistant.handle_update(db, friend, token="t", route={})) == "جواب"
        assert sent == [("sendChatAction", 542901635), ("sendMessage", 542901635)], "the stranger got nothing, not even a refusal"
        seen = asyncio.run(assistant.seen_chats(db))
        assert [c["id"] for c in seen] == ["542901635", "999"], "both are remembered for «پیدا کن»"

    def test_start_gets_the_help_without_a_model_call(self, configured, monkeypatch):
        sent = []

        async def fake_tg(token, method, route, **kw):
            sent.append(((kw.get("json") or {}).get("text") or "")[:20])
            return httpx.Response(200, json={"ok": True}), None
        from app.services import backup_service as bk
        monkeypatch.setattr(bk, "tg_request", fake_tg)
        _gateway(monkeypatch, lambda r: (_ for _ in ()).throw(AssertionError("no model call for /start")))
        _linked(monkeypatch, {1: SOBHAN})
        upd = {"update_id": 3, "message": {"chat": {"id": 1, "type": "private"}, "from": {"id": 1}, "text": "/start@Sorinflow_bot"}}
        out = asyncio.run(assistant.handle_update(_Db(), upd, token="t", route={}))
        assert out == assistant.HELP and sent and sent[0].startswith("سلام")

    def test_the_offset_moves_past_everything_seen(self, configured, monkeypatch):
        configured["rows"][assistant.KEY_OFFSET] = "10"
        from app.services import backup_service as bk

        async def fake_resolve(db=None):
            return {"token": "t", "chat_id": "1", "source": "panel"}

        async def fake_route(db=None):
            return {}

        async def fake_tg(token, method, route, **kw):
            if method == "getUpdates":
                assert kw["params"]["offset"] == 10 and kw["params"]["timeout"] == assistant.POLL_TIMEOUT
                return httpx.Response(200, json={"ok": True, "result": [
                    {"update_id": 10, "message": {"chat": {"id": 2, "type": "private"}, "from": {"id": 2}, "text": "x"}},
                    {"update_id": 11, "message": {"chat": {"id": 1, "type": "private"}, "from": {"id": 1}, "text": "/help"}}]}), None
            return httpx.Response(200, json={"ok": True}), None
        monkeypatch.setattr(bk, "resolve_telegram", fake_resolve)
        monkeypatch.setattr(bk, "resolve_route", fake_route)
        monkeypatch.setattr(bk, "tg_request", fake_tg)
        _linked(monkeypatch, {1: SOBHAN})
        res = asyncio.run(assistant.poll_once(_Db()))
        assert res == {"updates": 2, "answered": 1, "offset": 12}
        assert configured["rows"][assistant.KEY_OFFSET] == "12"

    def test_switched_off_polls_nothing(self, configured, monkeypatch):
        configured["rows"][assistant.KEY_ENABLED] = "false"
        from app.services import backup_service as bk

        async def fake_resolve(db=None):
            return {"token": "t", "chat_id": "1", "source": "panel"}
        monkeypatch.setattr(bk, "resolve_telegram", fake_resolve)
        assert asyncio.run(assistant.poll_once(_Db())) == {"skipped": "disabled"}


class TestTheShape:

    def test_the_tools_are_reads_and_keep_numbers_in(self):
        names = {t["function"]["name"] for t in assistant.TOOLS}
        assert names == set(assistant._TOOL_FUNCS) == {"count_listings", "search_listings", "queue_status", "customers", "property", "today_digest"}
        src = (ROOT / "app/ai/assistant.py").read_text(encoding="utf-8")
        # the module writes the question's row and a Telegram link's, and
        # moves its own cursor; nothing of the office's data is touched
        for verb in ("db.delete(", "sa.update(", "sqlalchemy import update", "sqlalchemy import insert",
                     "db.add(Property", "db.add(Customer", "db.add(Lead"):
            assert verb not in src, verb
        assert src.count("db.add(") == 2 and "db.add(AiChat(" in src and "db.add(TelegramLink(" in src
        # the one SQL delete is the old link a new one replaces (r.delete is Redis)
        assert re.findall(r"(?<![.\w])delete\((\w+)", src) == ["TelegramLink"]
        assert '"phone": "در پنل"' in src and '"owner_phone": "در پنل"' in src
        assert "شمارهٔ تلفن مالک یا مشتری را هرگز ننویس" in assistant.PERSONA

    def test_it_is_wired_like_the_other_agents(self):
        main = (ROOT / "app/main.py").read_text(encoding="utf-8")
        assert "assistant_task = asyncio.create_task(_assistant_loop())" in main and "assistant_task.cancel()" in main
        mounted = (ROOT / "app/api/routes/__init__.py").read_text(encoding="utf-8")
        assert 'router.include_router(ai_assistant.router, prefix="/ai/assistant"' in mounted
        rev = (ROOT / "migrations/versions/0008_ai_chats.py").read_text(encoding="utf-8")
        assert 'revision = "0008"' in rev and 'down_revision = "0007"' in rev and "insp.get_table_names()" in rev
        for f in ("app/models/__init__.py", "app/database.py", "migrations/env.py",
                  "migrations/versions/0001_baseline.py", "scripts/restore_backup.py"):
            assert "ai_chat" in (ROOT / f).read_text(encoding="utf-8"), f
        probe = (ROOT / "app/api/routes/backup.py").read_text(encoding="utf-8")
        assert "_assistant.seen_chats(db)" in probe, "«پیدا کن» still finds chats while the assistant consumes updates"
        assert "ZoneInfo" not in (ROOT / "app/ai/assistant.py").read_text(encoding="utf-8")

    def test_the_card_has_the_switch_the_question_and_the_log(self):
        assert 'id="ai-assistant"' in HTML and 'onclick="aiAssistantAsk()"' in HTML and 'onclick="aiAssistantLog()"' in HTML
        assert 'onchange="aiAssistantToggle(this.checked)"' in HTML
        fn = JS[JS.index("async function aiAssistantAsk"):JS.index("async function aiAssistantLog")]
        assert "apiCall('/ai/assistant/ask'" in fn and "askText(" in fn and "askInfo(" in fn
        for bad in ("prompt(", "confirm(", "alert("):
            assert bad not in fn
