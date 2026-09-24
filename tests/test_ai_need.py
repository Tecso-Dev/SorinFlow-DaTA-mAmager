"""
خوانندهٔ نیاز مشتری — free text becomes the intake form's criteria.

The consultant's note («یه واحد ۱۰۰ متری… طبقهٔ اول نباشه») and the portal
visitor's description used to be pasted into notes, where the engine could not
read them. The parser turns them into the columns the engine scores on — and
only proposes: the panel fills the empty fields, the bridge fills what the
form left blank, a person saves. No network here: the gateway is faked the
way test_ai_core fakes it.
"""
import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_ai_need.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.ai import need_parser as need  # noqa: E402
from app.api.routes import ai_need  # noqa: E402
from app.services import llm  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
_REAL_CLIENT = httpx.AsyncClient


def _gateway(monkeypatch, handler):
    class Fake(_REAL_CLIENT):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **kw)
    monkeypatch.setattr(llm.httpx, "AsyncClient", Fake)


def _answer(content):
    return httpx.Response(200, json={
        "model": "test/model", "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 400, "completion_tokens": 120, "cost": 0.0002, "total_cost_toman": 60}})


_EMPTY = {"deal_type": None, "kind": None, "city": None, "districts": [], "budget_max": None, "deposit_max": None,
          "rent_max": None, "area_min": None, "area_max": None, "rooms_min": None, "year_built_min": None,
          "must_have": [], "red_lines": [], "urgency": None, "notes": "", "confidence": {}}


def _reply(**fields):
    """A model answer: nothing known, overridden by `fields`."""
    return _answer(json.dumps({**_EMPTY, **fields}, ensure_ascii=False))


@pytest.fixture
def configured(monkeypatch):
    """A gateway that exists, a ledger that records, and no real session."""
    monkeypatch.setattr(llm.settings, "llm_api_key", "k-test", raising=False)
    monkeypatch.setattr(llm.settings, "llm_base_url", "https://ai.liara.ir/api/6aa50e58b5e9e82406b93188/v1", raising=False)
    monkeypatch.setattr(llm.settings, "llm_model", "openai/gpt-4.1-mini", raising=False)
    monkeypatch.setattr(llm.settings, "llm_model_read", "z-ai/glm-5.3-flash", raising=False)
    rows, ledger = {}, []

    async def get_many(_db, keys):
        return {k: rows[k] for k in keys if k in rows}

    async def record(agent, job, model, usage, ms, ok, error=""):
        ledger.append({"agent": agent, "job": job, "model": model, "ok": ok, "error": error})

    async def spent(_db, agent=None):
        return 0.0

    monkeypatch.setattr(llm.secret_box, "get_many", get_many)
    monkeypatch.setattr(llm, "_record", record)
    monkeypatch.setattr(llm, "spent_today", spent)
    return {"rows": rows, "ledger": ledger}


def _parse(text, **kw):
    return asyncio.run(need.parse_need(object(), text, **kw))


def _req(**kw):
    """A portal request, the way test_portal_bridge builds one."""
    base = dict(id=7, deal_type="buy", property_kind=None, city=None, districts=None, budget_min=None, budget_max=None,
                deposit_max=None, rent_max=None, area_min=None, area_max=None, rooms_min=None, year_built_min=None,
                needs_elevator=False, needs_parking=False, needs_storage=False, description=None,
                contact_name=None, contact_phone=None, customer_id=None, status="new")
    base.update(kw)
    return SimpleNamespace(**base)


class TestParseMoney:
    """The model is told to answer in integers; when it answers the way a
    person talks, the words are read rather than refused."""

    def test_persian_words_and_digits(self):
        assert need.parse_money("۵ میلیارد") == 5_000_000_000
        assert need.parse_money("۱۵۰ میلیون") == 150_000_000
        assert need.parse_money("۱٫۵ میلیارد") == 1_500_000_000
        assert need.parse_money("۱/۵ میلیارد تومان") == 1_500_000_000
        assert need.parse_money("۲ میلیارد و ۵۰۰ میلیون") == 2_500_000_000
        assert need.parse_money("۵۰۰ هزار تومان") == 500_000

    def test_plain_numbers_pass_through(self):
        assert need.parse_money("5000000000") == 5_000_000_000
        assert need.parse_money("5,000,000,000 تومان") == 5_000_000_000
        assert need.parse_money(150_000_000) == 150_000_000
        assert need.parse_money(1.5e9) == 1_500_000_000

    def test_a_range_is_its_ceiling(self):
        assert need.parse_money("۴ تا ۵ میلیارد") == 5_000_000_000

    def test_garbage_is_none(self):
        for bad in ("", None, "نامشخص", "null", "مشخص نیست", 0, "۰ تومان", True):
            assert need.parse_money(bad) is None, bad


class TestThePrompt:

    def test_the_customers_number_never_leaves(self):
        msgs = need.build_messages("زنگ زد از ۰۹۱۲۳۴۵۶۷۸۹، دنبال واحد ۹۰ متری تا ۴ میلیارد",
                                   hint={"city": "ارومیه 09141234567"})
        blob = json.dumps(msgs, ensure_ascii=False)
        assert "۰۹۱۲۳۴۵۶۷۸۹" not in blob and "09141234567" not in blob
        assert msgs[-1]["role"] == "user" and "۹۰ متری" in msgs[-1]["content"], "the sentence survives"

    def test_the_glossary_and_the_examples_ride_along(self):
        msgs = need.build_messages("x")
        system = msgs[0]["content"]
        assert msgs[0]["role"] == "system"
        for term in ("رهن", "ودیعه", "اجاره", "رهن کامل", "تبدیل", "خرید", "متراژ", "خواب", "نوساز", "کلنگی", "سند"):
            assert term in system, term
        assert "تومان" in system and "null" in system and "JSON" in system
        answers = [m for m in msgs if m["role"] == "assistant"]
        assert len(answers) == 3
        for a in answers:
            need.NeedCriteria.model_validate(json.loads(a["content"]))   # the examples obey the schema
        buy, rent, vague = (json.loads(a["content"]) for a in answers)
        assert buy["deal_type"] == "buy" and buy["red_lines"] and buy["budget_max"] == 5_000_000_000
        assert rent["deal_type"] == "rent" and rent["deposit_max"] and rent["rent_max"] and rent["must_have"]
        assert vague["deal_type"] is None and vague["budget_max"] is None and max(vague["confidence"].values()) < 0.8

    def test_a_hint_is_context_not_text(self):
        with_hint = need.build_messages("x", hint={"city": "ارومیه", "deal_type": "rent", "empty": "", "none": None})
        assert "city=ارومیه" in with_hint[0]["content"] and "deal_type=rent" in with_hint[0]["content"]
        assert "empty=" not in with_hint[0]["content"] and "none=" not in with_hint[0]["content"]
        assert need.build_messages("x")[0]["content"] == need.SYSTEM
        assert need.PROMPT_VERSION == 1


class TestTheAnswerBecomesACustomer:

    def test_a_purchase_with_a_red_line(self, configured, monkeypatch):
        seen = []

        def handler(req):
            seen.append(json.loads(req.read()))
            return _reply(deal_type="buy", kind="apartment", districts=["گلها"], budget_max=5_000_000_000,
                          area_min=90, area_max=110, rooms_min=2, must_have=["پارکینگ", "نوساز"],
                          red_lines=["طبقهٔ اول", "بدون آسانسور"], urgency="immediate",
                          notes="ترجیحاً رو به آفتاب", confidence={"budget_max": 0.95, "districts": 0.9})
        _gateway(monkeypatch, handler)
        out = _parse("یه واحد ۱۰۰ متری نوساز طرف گلها تا ۵ میلیارد، طبقهٔ اول نباشه، پارکینگ حتماً")
        c = out["customer"]
        assert c["deal_type"] == "buy" and c["desired_type"] == "apartment" and c["budget_max"] == 5_000_000_000
        assert c["desired_district"] == "گلها" and c["desired_specs"] == "100 متر / 2 خواب"
        assert c["red_lines"] == "طبقهٔ اول، بدون آسانسور"
        assert "نیاز دارد: پارکینگ، نوساز" in c["notes"] and "ترجیحاً رو به آفتاب" in c["notes"]
        assert c["temperature"] == "hot" and c["desired_city"] is None
        assert out["model"] == "test/model" and out["prompt_version"] == need.PROMPT_VERSION
        assert out["criteria"]["confidence"] == {"budget_max": 0.95, "districts": 0.9}
        body = seen[0]
        # the read job runs on a reasoning model: the core raises the budget to its floor
        assert body["temperature"] == 0 and body["max_tokens"] == 1500
        assert body["response_format"] == {"type": "json_object"} and body["model"] == "z-ai/glm-5.3-flash"
        assert body["messages"][-1]["content"].endswith("پارکینگ حتماً")
        row = configured["ledger"][-1]
        assert row["agent"] == "need" and row["job"] == "read" and row["ok"]

    def test_a_rental_is_judged_on_the_deposit(self, configured, monkeypatch):
        _gateway(monkeypatch, lambda r: _reply(deal_type="rent", kind="apartment", city="ارومیه",
                                               deposit_max=300_000_000, rent_max=10_000_000, rooms_min=2,
                                               must_have=["آسانسور"], urgency="month"))
        c = _parse("رهن تا ۳۰۰ و اجاره ۱۰")["customer"]
        assert c["deal_type"] == "rent" and c["budget_max"] == 300_000_000, "a stated deposit ceiling wins"
        assert c["desired_specs"] == "2 خواب" and c["desired_city"] == "ارومیه" and c["temperature"] == "warm"
        assert c["notes"] == "نیاز دارد: آسانسور"
        # a rent-only ceiling is converted the way the price watcher converts it
        _gateway(monkeypatch, lambda r: _reply(deal_type="rent", rent_max=10_000_000))
        assert _parse("اجاره ماهی ۱۰")["customer"]["budget_max"] == 10_000_000 * need.RENT_TO_DEPOSIT
        # the model filed a rental's ceiling under budget_max: still the deposit column
        _gateway(monkeypatch, lambda r: _reply(deal_type="rent", budget_max=250_000_000))
        assert _parse("x")["customer"]["budget_max"] == 250_000_000
        # no deal named, but a deposit is a rental
        _gateway(monkeypatch, lambda r: _reply(deposit_max=250_000_000))
        assert _parse("x")["customer"]["deal_type"] == "rent"

    def test_money_written_as_words_is_repaired(self, configured, monkeypatch):
        _gateway(monkeypatch, lambda r: _reply(deal_type="buy", budget_max="۵ میلیارد تومان",
                                               area_min="۸۰ متر", rooms_min="۲"))
        out = _parse("x")
        assert out["criteria"]["budget_max"] == 5_000_000_000 and out["customer"]["budget_max"] == 5_000_000_000
        assert out["customer"]["desired_specs"] == "80 متر / 2 خواب"
        _gateway(monkeypatch, lambda r: _reply(budget_max="نامشخص", deal_type="خرید", kind="ویلایی",
                                               urgency="زود", districts="گلها، سعدی", confidence={"kind": "high", "deal_type": 2}))
        out = _parse("x")
        assert out["criteria"]["budget_max"] is None and out["customer"]["budget_max"] is None
        assert out["customer"]["deal_type"] == "buy" and out["customer"]["desired_type"] == "house"
        assert out["criteria"]["urgency"] is None and out["customer"]["desired_district"] == "گلها، سعدی"
        assert out["criteria"]["confidence"] == {"deal_type": 1.0}, "unreadable confidence is dropped, not fatal"

    def test_nothing_said_is_nothing_filled(self, configured, monkeypatch):
        _gateway(monkeypatch, lambda r: _reply(districts=["سعدی"], notes="فقط «یه چیز خوب»", confidence={"districts": 0.7}))
        c = _parse("یه چیز خوب طرف سعدی")["customer"]
        assert c["desired_district"] == "سعدی" and c["notes"] == "فقط «یه چیز خوب»"
        for k in ("deal_type", "desired_type", "desired_city", "budget_max", "desired_specs", "red_lines", "temperature"):
            assert c[k] is None, k

    def test_empty_text_is_refused_before_the_model(self, configured, monkeypatch):
        calls = []
        _gateway(monkeypatch, lambda r: calls.append(1) or _reply())
        with pytest.raises(ValueError):
            _parse("   ")
        assert not calls

    def test_the_gate_still_holds(self, configured, monkeypatch):
        configured["rows"][llm.KEY_ENABLED] = "false"
        _gateway(monkeypatch, lambda r: _reply())
        with pytest.raises(llm.Disabled):
            _parse("یه واحد")


class TestTheRoute:

    @pytest.fixture
    def client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.auth.dependencies import get_current_user
        from app.database import get_db
        app = FastAPI()
        app.include_router(ai_need.router, prefix="/ai/need")
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(username="consultant")
        app.dependency_overrides[get_db] = lambda: object()
        return TestClient(app)

    def test_empty_text_is_a_400(self, client, monkeypatch):
        async def never(db, text, *, hint=None):
            raise AssertionError("no model call for empty text")
        monkeypatch.setattr(ai_need.need_parser, "parse_need", never)
        r = client.post("/ai/need/parse", json={"text": "   "})
        assert r.status_code == 400 and "متنی" in r.json()["detail"]
        assert client.post("/ai/need/parse", json={}).status_code == 422
        assert client.post("/ai/need/parse", json={"text": "x" * 2001}).status_code == 422

    def test_the_model_failing_is_a_502_in_persian(self, client, monkeypatch):
        async def boom(db, text, *, hint=None):
            raise llm.NotConfigured("هوش مصنوعی تنظیم نشده است (LLM_API_KEY / LLM_BASE_URL)")
        monkeypatch.setattr(ai_need.need_parser, "parse_need", boom)
        r = client.post("/ai/need/parse", json={"text": "یه واحد"})
        assert r.status_code == 502 and "هوش مصنوعی تنظیم نشده" in r.json()["detail"]

    def test_the_answer_comes_back_whole_and_the_text_stays_out_of_the_log(self, client, monkeypatch):
        async def fake(db, text, *, hint=None):
            assert text == "واحد ۹۰ متری طرف گلها" and hint == {"city": "ارومیه"}
            return {"criteria": {"confidence": {"area_min": 0.8}},
                    "customer": {"desired_specs": "90 متر", "budget_max": None},
                    "model": "m", "prompt_version": 1}
        monkeypatch.setattr(ai_need.need_parser, "parse_need", fake)
        lines = []
        sink = logger.add(lambda m: lines.append(str(m)), level="INFO")
        try:
            r = client.post("/ai/need/parse", json={"text": " واحد ۹۰ متری طرف گلها ", "hint": {"city": "ارومیه"}})
        finally:
            logger.remove(sink)
        assert r.status_code == 200 and r.json()["customer"]["desired_specs"] == "90 متر"
        log = "\n".join(line for line in lines if "[ai:need]" in line)
        assert "consultant" in log and "1 field" in log, log
        assert "گلها" not in log, "a customer's words are never logged"


class TestThePortal:

    def test_only_what_the_form_left_blank(self, configured, monkeypatch):
        _gateway(monkeypatch, lambda r: _reply(deal_type="rent", kind="house", city="تهران", districts=["گلها"],
                                               budget_max=3_000_000_000, red_lines=["طبقهٔ اول"], must_have=["پارکینگ"],
                                               area_min=120, urgency="immediate", notes="اضافه"))
        req = _req(deal_type="buy", property_kind="apartment", city="ارومیه", budget_max=5_000_000_000,
                   area_min=90, area_max=110, description="طرف گلها، طبقهٔ اول نباشه، پارکینگ حتماً")
        extra = asyncio.run(need.enrich_request(object(), req))
        assert extra == {"desired_district": "گلها", "red_lines": "طبقهٔ اول"}, \
            "districts and red lines were blank; the rest the form had said, and the model does not overrule a form"

    def test_a_bare_request_takes_everything_the_text_says(self, configured, monkeypatch):
        _gateway(monkeypatch, lambda r: _reply(kind="apartment", city="ارومیه", districts=["سعدی"],
                                               budget_max="۴ میلیارد", area_min=90, area_max=90))
        extra = asyncio.run(need.enrich_request(object(), _req(description="واحد ۹۰ متری طرف سعدی تا ۴ میلیارد")))
        assert extra == {"desired_city": "ارومیه", "desired_district": "سعدی", "desired_type": "apartment",
                         "budget_max": 4_000_000_000, "desired_specs": "90 متر"}
        assert "notes" not in extra and "deal_type" not in extra and "temperature" not in extra

    def test_no_description_no_call(self, configured, monkeypatch):
        calls = []
        _gateway(monkeypatch, lambda r: calls.append(1) or _reply())
        assert asyncio.run(need.enrich_request(object(), _req(description="  "))) is None
        assert asyncio.run(need.enrich_request(object(), _req())) is None
        assert not calls

    def test_the_model_failing_is_not_the_visitors_problem(self, configured, monkeypatch):
        _gateway(monkeypatch, lambda r: httpx.Response(500, text="down"))
        assert asyncio.run(need.enrich_request(object(), _req(description="طبقهٔ اول نباشه"))) is None
        monkeypatch.setattr(llm.settings, "llm_api_key", "", raising=False)
        assert asyncio.run(need.enrich_request(object(), _req(description="طبقهٔ اول نباشه"))) is None

    def test_the_hint_is_the_form(self, configured, monkeypatch):
        seen = []

        def handler(r):
            seen.append(json.loads(r.read())["messages"][0]["content"])
            return _reply()
        _gateway(monkeypatch, handler)
        assert asyncio.run(need.enrich_request(object(), _req(city="ارومیه", deal_type="rent", description="x"))) is None
        assert "city=ارومیه" in seen[0] and "deal_type=rent" in seen[0]


class TestTheShape:

    def test_the_panel_script_fills_only_empty_fields_without_native_dialogs(self):
        js = (ROOT / "frontend/js/ai/need.js").read_text(encoding="utf-8")
        assert "apiCall('/ai/need/parse'" in js and "aiFillCustomerFromText" in js and "ai-filled" in js
        for bad in ("alert(", "confirm(", "prompt("):
            assert bad not in js
        for id_ in ("cust-city", "cust-district", "cust-desired-type", "cust-deal-type", "cust-budget",
                    "cust-specs", "cust-redlines", "cust-notes", "cust-ai-text"):
            assert f"'{id_}'" in js, id_
        assert "isEmpty(el)" in js and "showToast(" in js and "formatNumber(" in js

    def test_the_integration_note_names_the_doors(self):
        md = (ROOT / "app/ai/NEED.INTEGRATION.md").read_text(encoding="utf-8")
        assert 'prefix="/ai/need"' in md and '_perm("crm")' in md
        assert "enrich_request(db, req)" in md and 'id="cust-ai-text"' in md and ".ai-filled" in md
        assert 'src="js/ai/need.js' in md
