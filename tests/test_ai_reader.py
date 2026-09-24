"""
خوانندهٔ آگهی — the facts a listing states only in its own words.

The scraper stops at metres, rooms and a price; the rest — the real kind,
the floor, the document, «قابل تبدیل», «مناسب کافه», the red flags — is in
the text. The reader asks the model once per listing, keeps what it said
with a confidence per field, and effective() merges it under the scraped
columns. No network here: the gateway is a MockTransport, the way
test_ai_core fakes it, and the database is a sqlite file of this test's own.
"""
import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_ai_reader.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from sqlalchemy import select                                          # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool                                   # noqa: E402

import app.models                                                      # noqa: E402,F401  every table on Base.metadata
from app.ai import listing_reader as reader                            # noqa: E402
from app.database import Base                                          # noqa: E402
from app.models.property import Property                               # noqa: E402
from app.services import llm                                           # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DB_FILE = ROOT / "_test_ai_reader_own.db"
_REAL_CLIENT = httpx.AsyncClient

SHOP = {"kind": "shop", "floor": 1, "total_floors": None, "year_built": None, "document": None,
        "condition": None, "has_elevator": None, "has_parking": None, "has_storage": None,
        "has_balcony": None, "district": "خیابان کاشانی", "convertible": True, "exchange": None,
        "vacant": True, "negotiable": None, "suitable_for": ["کافه"], "red_flags": [],
        "summary": "مغازهٔ تخلیه در خیابان کاشانی، رهن و اجارهٔ قابل تبدیل، مناسب کافه",
        "confidence": {"kind": 1, "floor": 0.9, "district": 1, "convertible": 1, "vacant": 1, "suitable_for": 1}}


# ── the fakes, in test_ai_core's style ───────────────────────────────────────

def _gateway(monkeypatch, handler):
    class Fake(_REAL_CLIENT):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **kw)
    monkeypatch.setattr(llm.httpx, "AsyncClient", Fake)


def _answer(content, cost=0.001):
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False)
    return httpx.Response(200, json={
        "model": "test/model", "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": cost, "total_cost_toman": 300}})


def _asked_title(req) -> str:
    """The listing a request is about — the title line of its last message."""
    body = json.loads(req.read())
    return body["messages"][-1]["content"].split("\n", 1)[0]


@pytest.fixture
def configured(monkeypatch):
    """A gateway that exists, a ledger that records, settings in a dict."""
    monkeypatch.setattr(llm.settings, "llm_api_key", "k-test", raising=False)
    monkeypatch.setattr(llm.settings, "llm_base_url", "https://ai.liara.ir/api/6aa50e58b5e9e82406b93188/v1", raising=False)
    monkeypatch.setattr(llm.settings, "llm_model", "openai/gpt-4.1-mini", raising=False)
    monkeypatch.setattr(llm.settings, "llm_model_read", "z-ai/glm-5.3-flash", raising=False)
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
    monkeypatch.setattr(reader, "_last_stop", "")
    return {"rows": rows, "ledger": ledger}


@pytest.fixture
def store():
    """A sqlite file of this test's own with the listings table (and what it
    points at — the whole schema has a Postgres UUID sqlite cannot render),
    and a session maker."""
    from app.models.property import Category, City
    tables = [City.__table__, Category.__table__, Property.__table__]
    engine = create_async_engine(f"sqlite+aiosqlite:///{DB_FILE}", poolclass=NullPool)

    async def fresh():
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: Base.metadata.drop_all(c, tables=tables))
            await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))
    asyncio.run(fresh())
    yield async_sessionmaker(engine, expire_on_commit=False)
    asyncio.run(engine.dispose())
    DB_FILE.unlink(missing_ok=True)


def _prop(i, title="آپارتمان ۹۰ متری", description="", **kw):
    kw.setdefault("is_active", True)
    return Property(tag_number=f"T{i}", divar_id=f"D{i}", url=f"https://divar.ir/v/{i}", title=title,
                    description=description, serial_no=1000 + i, **kw)


async def _seed(maker, *props):
    async with maker() as db:
        db.add_all(props)
        await db.commit()
        return [p.id for p in props]


# ── the prompt ───────────────────────────────────────────────────────────────

class TestThePrompt:

    def test_the_pack_speaks_the_markets_language_and_asks_for_null(self):
        assert reader.PROMPT_VERSION == 1
        system = reader.build_messages(_prop(1))[0]["content"]
        for term in ("قابل تبدیل", "معاوضه", "تخلیه", "کلنگی", "بازسازی", "تک‌برگ", "قولنامه‌ای",
                     "وقفی", "مشاع", "سرقفلی", "در رهن بانک", "بر:", "نبش", "کوچه"):
            assert term in system, term
        assert "خیابان گلها" in system and "خ گلها" in system, "a district and its other spelling"
        assert "null" in system and "حدس نزن" in system
        assert "red_flags" in system and "confidence" in system

    def test_the_owners_number_never_leaves(self):
        p = _prop(1, title="مغازه ۴۰ متری تماس 09143495300",
                  description="قابل تبدیل — ۰۹۱۲۳۴۵۶۷۸۹ یا دفتر 044-33445566، ایمیل ali@x.ir")
        blob = json.dumps(reader.build_messages(p), ensure_ascii=False)
        for secret in ("09143495300", "۰۹۱۲۳۴۵۶۷۸۹", "33445566", "ali@x.ir"):
            assert secret not in blob
        assert "قابل تبدیل" in blob, "the words survive"

    def test_three_worked_examples_answer_in_the_schema(self):
        msgs = reader.build_messages(_prop(1))
        shots = [m for m in msgs if m["role"] == "assistant"]
        assert len(shots) == 3 and len(msgs) == 8
        answers = [json.loads(s["content"]) for s in shots]
        for a in answers:
            assert reader.ListingFacts.model_validate(a).model_dump() == a, "the example is exactly what the schema returns"
        kinds = [a["kind"] for a in answers]
        assert kinds == ["apartment", "shop", "house"]
        assert answers[1]["convertible"] is True and "کافه" in answers[2]["suitable_for"]
        assert any("مغازه" in f for f in answers[2]["red_flags"]), "the mis-filed shop is named"

    def test_the_scraped_fields_ride_along_as_context(self):
        p = _prop(1, property_type="مغازه", listing_type="rent", area=40, deposit=100_000_000,
                  rent_price=5_000_000, city_name="ارومیه")
        last = reader.build_messages(p)[-1]["content"]
        assert "نوع ملک: مغازه" in last and "رهن و اجاره" in last and "ودیعه: 100000000" in last
        assert "طبقه: —" in last, "an empty field is a dash, not a guess"


class TestTheSchema:
    """A near-miss becomes a value or None, never a refusal that costs a retry."""

    def test_near_misses(self):
        f = reader.ListingFacts.model_validate({
            "kind": "villa", "floor": "طبقه ۳", "year_built": "۹۸", "has_elevator": "بله",
            "document": "سند تک برگ", "condition": "کلنگی", "confidence": {"kind": "x", "floor": 2},
            "suitable_for": "کافه", "summary": "تماس 09143495300", "extra": 1})
        assert f.kind is None and f.floor == 3 and f.year_built == 1398 and f.has_elevator is True
        assert f.document is None and f.condition == "کلنگی" and f.confidence == {"floor": 1.0}
        assert f.suitable_for == ["کافه"] and "09143495300" not in f.summary

    def test_a_year_outside_the_calendar_is_unknown(self):
        assert reader.ListingFacts(year_built=2015).year_built is None
        assert reader.ListingFacts(year_built=1402).year_built == 1402


# ── reading ──────────────────────────────────────────────────────────────────

class TestReading:

    def test_facts_are_stored_with_the_version_and_the_model(self, configured, store, monkeypatch):
        seen = []

        def handler(req):
            seen.append(json.loads(req.read()))
            return _answer(SHOP)
        _gateway(monkeypatch, handler)

        async def scenario():
            [pid] = await _seed(store, _prop(1, description="مغازه تخلیه، قابل تبدیل، مناسب کافه"))
            async with store() as db:
                p = await db.get(Property, pid)
                facts = await reader.read_listing(db, p)
            async with store() as db:
                return facts, await db.get(Property, pid)
        facts, p = asyncio.run(scenario())
        assert facts["kind"] == "shop" and facts["convertible"] is True
        assert p.ai_facts["prompt_version"] == 1 and p.ai_facts["model"] == "test/model"
        assert p.ai_facts["suitable_for"] == ["کافه"] and p.ai_read_at is not None
        body = seen[0]
        assert body["model"] == "z-ai/glm-5.3-flash" and body["temperature"] == 0
        assert body["response_format"] == {"type": "json_object"}
        assert configured["ledger"][-1]["agent"] == "reader" and configured["ledger"][-1]["job"] == "read"

    def test_a_malformed_answer_leaves_the_listing_untouched(self, configured, store, monkeypatch):
        _gateway(monkeypatch, lambda r: _answer("این JSON نیست"))

        async def scenario():
            [pid] = await _seed(store, _prop(1))
            async with store() as db:
                out = await reader.read_listing(db, await db.get(Property, pid))
            async with store() as db:
                return out, await db.get(Property, pid)
        out, p = asyncio.run(scenario())
        assert out is None and p.ai_facts is None and p.ai_read_at is None
        assert [r for r in configured["ledger"] if not r["ok"]], "the refusal is on the ledger"

    def test_the_gate_errors_come_out_so_a_pass_can_stop(self, configured, store, monkeypatch):
        configured["rows"][llm.KEY_ENABLED] = "false"
        _gateway(monkeypatch, lambda r: _answer(SHOP))

        async def scenario():
            [pid] = await _seed(store, _prop(1))
            async with store() as db:
                await reader.read_listing(db, await db.get(Property, pid))
        with pytest.raises(llm.Disabled):
            asyncio.run(scenario())

    def test_a_bake_off_model_goes_through_the_same_door(self, configured, store, monkeypatch):
        seen = []

        def handler(req):
            seen.append(json.loads(req.read())["model"])
            return _answer(SHOP)
        _gateway(monkeypatch, handler)

        async def scenario():
            [pid] = await _seed(store, _prop(1))
            async with store() as db:
                p = await db.get(Property, pid)
                facts = await reader.ask(db, p, model="google/gemini-2.5-flash", agent="bakeoff")
            async with store() as db:
                return facts, await db.get(Property, pid)
        facts, p = asyncio.run(scenario())
        assert seen == ["google/gemini-2.5-flash"] and facts["kind"] == "shop"
        assert p.ai_facts is None, "a bake-off stores nothing"
        assert configured["ledger"][-1]["agent"] == "bakeoff"
        assert configured["ledger"][-1]["model"] == "google/gemini-2.5-flash"


# ── the pass ─────────────────────────────────────────────────────────────────

class TestThePass:

    def test_the_cursor_moves_past_successes_only(self, configured, store, monkeypatch):
        broken = {"on"}

        def handler(req):
            if "خراب" in _asked_title(req) and broken:
                return httpx.Response(500, json={"error": "boom"})
            return _answer(SHOP)
        _gateway(monkeypatch, handler)

        async def scenario():
            ids = await _seed(store, _prop(1, title="آگهی یک"), _prop(2, title="آگهی خراب"), _prop(3, title="آگهی سه"),
                              _prop(4, title="", description="بی‌عنوان"), _prop(5, title="غیرفعال", is_active=False))
            async with store() as db:
                first = await reader.run_once(db, limit=50)
                cur1 = await reader._cursor(db)
                stored1 = configured["rows"][reader.KEY_CURSOR]
                read1 = {p.id for p in (await db.execute(select(Property).where(Property.ai_read_at.isnot(None)))).scalars()}
                broken.clear()
                second = await reader.run_once(db, limit=50)
                cur2 = await reader._cursor(db)
                third = await reader.run_once(db, limit=50)
            return ids, first, cur1, stored1, read1, second, cur2, third
        ids, first, cur1, stored1, read1, second, cur2, third = asyncio.run(scenario())
        assert first == {"scanned": 3, "read": 2, "failed": 1, "cursor": ids[0], "stopped": None}
        assert cur1 == ids[0] and stored1 == f"{reader.PROMPT_VERSION}:{ids[0]}"
        assert read1 == {ids[0], ids[2]}, "the listing after the failure was read and kept"
        assert second == {"scanned": 1, "read": 1, "failed": 0, "cursor": ids[1], "stopped": None}, \
            "the failed one is retried; the one already read is not paid for twice"
        assert cur2 == ids[1]
        assert third["scanned"] == 0 and third["cursor"] == ids[1]

    def test_a_full_cap_stops_the_pass_at_the_last_success(self, configured, store, monkeypatch):
        configured["rows"][llm.KEY_CAP] = "0.002"
        # a generous cap of its own, so only the shared cap below is what stops it
        configured["rows"][llm.agent_cap_key("reader")] = "100"
        _gateway(monkeypatch, lambda r: _answer(SHOP, cost=0.0015))

        async def scenario():
            ids = await _seed(store, _prop(1), _prop(2), _prop(3))
            async with store() as db:
                out = await reader.run_once(db, limit=50)
                cur = await reader._cursor(db)
                configured["rows"][llm.KEY_CAP] = "1"
                again = await reader.run_once(db, limit=50)
            return ids, out, cur, again
        ids, out, cur, again = asyncio.run(scenario())
        assert out["stopped"] == "BudgetExceeded" and out["read"] == 2 and out["cursor"] == ids[1]
        assert cur == ids[1]
        assert again == {"scanned": 1, "read": 1, "failed": 0, "cursor": ids[2], "stopped": None}

    def test_not_configured_is_quiet(self, configured, store, monkeypatch):
        monkeypatch.setattr(llm.settings, "llm_api_key", "", raising=False)
        _gateway(monkeypatch, lambda r: _answer(SHOP))

        async def scenario():
            await _seed(store, _prop(1))
            async with store() as db:
                return await reader.run_once(db), await reader.run_once(db)
        a, b = asyncio.run(scenario())
        assert a["stopped"] == "NotConfigured" and a["read"] == 0 and a["cursor"] == 0
        assert b == a and configured["ledger"] == [], "nothing was asked, nothing was paid"

    def test_a_new_prompt_version_restarts_the_cursor(self, configured, store, monkeypatch):
        configured["rows"][reader.KEY_CURSOR] = f"{reader.PROMPT_VERSION - 1}:40"

        async def scenario():
            async with store() as db:
                return await reader._cursor(db)
        assert asyncio.run(scenario()) == 0

    def test_the_loop_is_switched_with_the_matcher_and_needs_no_tzdata(self):
        src = (ROOT / "app/ai/listing_reader.py").read_text(encoding="utf-8")
        assert 'getattr(settings, "match_engine", True)' in src
        assert "ZoneInfo" not in src and "zoneinfo" not in src
        assert "TICK_SECONDS = 120" in src and "START_DELAY = 150" in src
        assert src.count("except (llm.NotConfigured, llm.Disabled, llm.BudgetExceeded)") == 2


# ── the merge rule ───────────────────────────────────────────────────────────

class TestEffective:

    def test_the_scraped_column_wins_and_the_fact_fills_the_gap(self):
        p = _prop(1, property_type="مغازه", floor=3, year_built=None, district="", has_elevator=False, has_parking=True)
        p.ai_facts = {"kind": "house", "floor": 5, "year_built": 1398, "district": "بلوار سعدی", "has_elevator": True,
                      "has_parking": False, "convertible": True, "document": "قولنامه‌ای", "condition": "قدیمی",
                      "suitable_for": ["کافه"], "red_flags": ["سند قولنامه‌ای"], "confidence": {"kind": 0.95}}
        e = reader.effective(p)
        assert e["floor"] == 3, "scraped wins"
        assert e["year_built"] == 1398 and e["district"] == "بلوار سعدی", "facts fill the gaps"
        assert e["has_elevator"] is True, "a scraped False is «not seen», so the fact fills it"
        assert e["has_parking"] is True, "a scraped True is never taken away"
        assert e["convertible"] is True and e["document"] == "قولنامه‌ای" and e["condition"] == "قدیمی"
        assert e["suitable_for"] == ["کافه"] and e["red_flags"] == ["سند قولنامه‌ای"]

    def test_kind_follows_the_fact_only_when_sure_or_when_the_scraper_had_nothing(self):
        p = _prop(1, property_type="مغازه")
        p.ai_facts = {"kind": "house", "confidence": {"kind": 0.95}}
        assert reader.effective(p)["kind"] == "house"
        p.ai_facts["confidence"]["kind"] = 0.5
        assert reader.effective(p)["kind"] == "shop"
        blank = _prop(2, title="ملک", property_type=None, category_name=None)
        blank.ai_facts = {"kind": "apartment", "confidence": {"kind": 0.6}}
        assert reader.effective(blank)["kind"] == "apartment"
        blank.ai_facts = {"kind": "other", "confidence": {"kind": 1}}
        assert reader.effective(blank)["kind"] is None, "«other» is not a family"

    def test_unread_listings_are_just_their_columns(self):
        p = _prop(1, property_type="آپارتمان", floor=0)
        e = reader.effective(p)
        assert e["kind"] == "apartment" and e["floor"] == 0, "the ground floor is a value, not a gap"
        assert e["convertible"] is None and e["suitable_for"] == [] and e["red_flags"] == []


# ── the router ───────────────────────────────────────────────────────────────

class TestTheRouter:

    def _client(self, store):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api.routes import ai_reader
        from app.database import get_db
        app = FastAPI()
        app.include_router(ai_reader.router, prefix="/ai/reader")

        async def _db():
            async with store() as s:
                yield s
        app.dependency_overrides[get_db] = _db
        app.dependency_overrides[ai_reader._super_admin.dependency] = lambda: SimpleNamespace(username="root", role="root")
        return TestClient(app)

    def test_status_shape(self, configured, store, monkeypatch):
        _gateway(monkeypatch, lambda r: _answer(SHOP))
        asyncio.run(_seed(store, _prop(1), _prop(2)))
        c = self._client(store)
        r = c.get("/ai/reader/status")
        assert r.status_code == 200, r.text
        assert r.json() == {"cursor": 0, "total": 2, "read": 0, "behind": 2, "last_read_at": None,
                            "prompt_version": 1, "model": "z-ai/glm-5.3-flash"}
        run = c.post("/ai/reader/run?limit=1")
        assert run.status_code == 200 and run.json()["read"] == 1
        after = c.get("/ai/reader/status").json()
        assert after["read"] == 1 and after["behind"] == 1 and after["cursor"] == run.json()["cursor"]
        assert after["last_read_at"]

    def test_reread_and_the_errors(self, configured, store, monkeypatch):
        _gateway(monkeypatch, lambda r: _answer(SHOP))
        [pid] = asyncio.run(_seed(store, _prop(1)))
        c = self._client(store)
        assert c.post("/ai/reader/999999").status_code == 404
        assert c.post("/ai/reader/run?limit=500").status_code == 422
        r = c.post(f"/ai/reader/{pid}")
        assert r.status_code == 200 and r.json()["facts"]["kind"] == "shop" and r.json()["facts"]["prompt_version"] == 1
        configured["rows"][llm.KEY_ENABLED] = "false"
        r = c.post(f"/ai/reader/{pid}")
        assert r.status_code == 503 and "خاموش" in r.json()["detail"]

    def test_it_is_for_the_two_top_roles(self):
        src = (ROOT / "app/api/routes/ai_reader.py").read_text(encoding="utf-8")
        assert '_role_dep("root", "super_admin")' in src and src.count("_super_admin") >= 4


# ── the scripts and the panel ────────────────────────────────────────────────

def _load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestTheLabelSheet:

    def test_the_table_is_markdown_a_person_can_correct(self):
        sheet = _load_script("ai_label_sheet")
        rows = [SimpleNamespace(id=1, serial_no=1042, title="مغازه | بر خیابان", description="x" * 300, ai_facts=SHOP),
                SimpleNamespace(id=2, serial_no=None, title="خانه", description=None, ai_facts=None)]
        table = sheet.build_table(rows)
        lines = table.splitlines()
        assert lines[0] == "| id | serial | title | description | facts |" and lines[1].startswith("|---")
        assert "مغازه ／ بر خیابان" in lines[2], "a pipe in the title would break the table"
        assert "x" * 200 in lines[2] and "x" * 201 not in lines[2]
        assert 'kind="shop"' in lines[2] and 'convertible=true' in lines[2] and 'suitable_for=["کافه"]' in lines[2]
        assert "document=" not in lines[2], "only the fields that were set"
        assert lines[3] == "| 2 |  | خانه |  | — |"

    def test_the_bake_off_scores_labels_like_a_person_would(self):
        bake = _load_script("ai_bakeoff")
        labels = {"kind": "shop", "floor": 1, "document": None, "suitable_for": ["کافه"], "district": "خیابان کاشانی"}
        assert bake.score(labels, SHOP) == {"kind": True, "floor": True, "document": True,
                                            "suitable_for": True, "district": True}
        guessed = {**SHOP, "document": "تک‌برگ", "suitable_for": ["رستوران", "کافه"]}
        s = bake.score(labels, guessed)
        assert s["document"] is False, "a guess where the text says nothing is wrong"
        assert s["suitable_for"] is False, "lists compare as sets"


class TestItIsWiredIn:

    def test_the_columns_exist_and_reach_the_panel(self):
        assert hasattr(Property, "ai_facts") and hasattr(Property, "ai_read_at")
        d = _prop(1).to_dict()
        assert "ai_facts" in d and "ai_read_at" in d
        src = (ROOT / "app/models/property.py").read_text(encoding="utf-8")
        assert "# ── AI (app/ai/listing_reader.py) ──" in src

    def test_the_panel_renders_chips_without_native_dialogs(self):
        js = (ROOT / "frontend/js/ai/reader.js").read_text(encoding="utf-8")
        assert "function aiRenderFacts(container, facts)" in js and "async function aiReread(id)" in js
        assert "apiCall('/ai/reader/' + id, { method: 'POST' })" in js
        assert "برداشت هوش مصنوعی" in js and "بازخوانی" in js and "اطمینان" in js
        for cls in ("is-deal", "is-warn", "is-use", "is-amenity"):
            assert cls in js
        for bad in ("alert(", "confirm(", "prompt("):
            assert bad not in js
        assert "esc(" in js and "showToast(" in js and "formatNumber(" in js

    def test_the_single_change_to_the_gateway_is_the_override(self):
        src = (ROOT / "app/services/llm.py").read_text(encoding="utf-8")
        assert src.count("model_override") == 3, "the kwarg, its docstring line, and the one place it is used"
        assert 'model = model_override or cfg["models"].get(job) or cfg["models"]["write"]' in src

    def test_the_integration_notes_exist(self):
        notes = (ROOT / "app/ai/READER.INTEGRATION.md").read_text(encoding="utf-8")
        for hook in ("reader_loop", 'prefix="/ai/reader"', "js/ai/reader.js", 'id="ai-facts"',
                     "0005", "effective(", "ai_bakeoff.py", "PropertyResponse"):
            assert hook in notes, hook
