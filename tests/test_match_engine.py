"""
موتور تطبیق خودکار — who was looking for the listing that just arrived.

The matcher could answer for one listing when somebody pressed the button.
Nobody presses a button for every listing, so the engine asks for each one
as it arrives: a cursor over properties.id, the same scorer, a threshold,
one row per (listing, customer), and the fits on the call queue.
"""
import os
import sys
import asyncio
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_match_engine.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

ROOT = Path(__file__).resolve().parent.parent
JS = (ROOT / "frontend/js/app.js").read_text(encoding="utf-8")
HTML = (ROOT / "frontend/index.html").read_text(encoding="utf-8")


class TestTheShape:

    def test_one_row_per_listing_and_customer(self):
        from app.models.crm_models import CustomerMatch
        names = {c.name for c in CustomerMatch.__table__.constraints}
        assert "uq_customer_match" in names
        assert CustomerMatch.STATUSES == ("new", "contacted", "dismissed")

    def test_it_runs_off_the_request_path_and_can_be_switched_off(self):
        main = (ROOT / "app/main.py").read_text(encoding="utf-8")
        assert "match_task = asyncio.create_task(_match_loop())" in main and "match_task.cancel()" in main
        src = (ROOT / "app/crm/match_engine.py").read_text(encoding="utf-8")
        assert 'getattr(settings, "match_engine", True)' in src
        assert "MIN_SCORE = 55" in src and "TICK_SECONDS = 300" in src

    def test_no_system_tzdata_is_needed(self):
        src = (ROOT / "app/crm/match_engine.py").read_text(encoding="utf-8")
        assert "ZoneInfo" not in src

    def test_the_announcement_uses_the_backups_bot_and_proxy(self):
        src = (ROOT / "app/crm/match_engine.py").read_text(encoding="utf-8")
        fn = src[src.index("async def _announce"):src.index("async def tick")]
        assert "resolve_telegram(db)" in fn and "resolve_route(db)" in fn and "tg_request(cfg[\"token\"], \"sendMessage\", route" in fn
        assert "except Exception" in fn, "losing the message must not lose the matches"

    def test_the_panel_puts_the_fits_on_the_call_queue(self):
        assert 'id="matches-card"' in HTML and 'id="matches-list"' in HTML
        tabs = HTML[HTML.index('id="crm-main-tabs"'):HTML.index('id="crm-tabs-content"')]
        assert 'id="matches-due-badge"' in tabs
        assert "case 'crm':        _applyCrmRoleVisibility(); loadCalls(); loadMatches(); loadPriceDrops(); break;" in JS
        fn = JS[JS.index("function _matchQueueCard"):JS.index("async function mqDecide")]
        assert "mqDecide(${m.id}, 'contacted')" in fn and "mqDecide(${m.id}, 'dismissed')" in fn
        assert "viewProperty(${p.id})" in fn and "shareFile(${p.id})" in fn
        blk = JS[JS.index("// ── تطبیق خودکار: the engine's fits"):JS.index("// ═══ End call queue")]
        for bad in ("prompt(", "confirm(", "alert("):
            assert bad not in blk.replace("askText(", "")

    def test_the_card_can_text_the_customer(self):
        """«پیامک به مشتری»: preview first, editable, then one POST; the older
        share-modal path finally reads the panel-saved key too."""
        fn = JS[JS.index("function _matchQueueCard"):JS.index("async function mqDecide")]
        assert "mqSms(${m.id})" in fn
        sms = JS[JS.index("async function mqSms"):JS.index("async function runMatchesNow")]
        assert "apiCall(`/crm/matches/${id}/sms`)" in sms and "multiline: true" in sms
        assert "method: 'POST', body: JSON.stringify({ message: text })" in sms
        ask = JS[JS.index("function _askOpen"):JS.index("function askConfirm")]
        assert "field.multiline" in ask and "<textarea" in ask and "e.ctrlKey || e.metaKey" in ask
        src = (ROOT / "app/api/routes/crm.py").read_text(encoding="utf-8")
        old = src[src.index('@router.post("/sms/send")'):src.index('@router.get("/sms/logs")')]
        assert "send_sms(to_number, message, provider, db=db)" in old and "normalize_mobile" in old

    def test_the_run_button_is_for_super_admins(self):
        src = (ROOT / "app/api/routes/crm.py").read_text(encoding="utf-8")
        fn = src[src.index('@router.post("/matches/run")'):src.index('@router.post("/leads/{lead_id}/notify")')]
        assert "require_super_admin" in fn
        assert 'onclick="runMatchesNow()"' in HTML and "crm-superadmin-only" in HTML[HTML.index('onclick="runMatchesNow()"') - 120:HTML.index('onclick="runMatchesNow()"')]


# ── through the real app (Postgres) ──────────────────────────────────────────

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
    cfg.cookies_path = "/tmp/sorinflow-test-cookies"
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
    """Two consultants, three customers, three listings — one fits, one is a
    shop (wrong family), one is over budget."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.models.user import User
    from app.models.property import Property
    from app.models.crm_models import Customer
    from app.auth.jwt import get_password_hash

    async def _go():
        eng = create_async_engine(os.environ["DATABASE_URL"])
        maker = async_sessionmaker(eng, expire_on_commit=False)
        out = {}
        try:
            async with maker() as s:
                for u, name, role in (("me_mina", "مینا رضایی", "admin"), ("me_boss", "مدیر", "super_admin")):
                    s.add(User(username=u, full_name=name, role=role, permissions=["crm"],
                               hashed_password=get_password_hash("pw123456"), is_active=True))
                s.add(Customer(full_name="خریدار گلها", mobile1="09121110000", temperature="hot", consultant_name="مینا رضایی",
                               desired_city="ارومیه", desired_district="خیابان گلها", desired_type="apartment",
                               deal_type="buy", budget_max=5_000_000_000, desired_specs="۱۰۰ متر / ۲ خواب"))
                s.add(Customer(full_name="مشتری همکار", mobile1="09121110001", temperature="warm", consultant_name="کس دیگر",
                               desired_city="ارومیه", desired_district="خیابان گلها", desired_type="apartment",
                               deal_type="buy", budget_max=5_000_000_000))
                s.add(Customer(full_name="بی‌مشاور", mobile1="09121110002", temperature="cold", consultant_name=None,
                               desired_city="ارومیه", desired_district="بلوار سعدی", desired_type="apartment",
                               deal_type="buy", budget_max=1_000_000_000))
                await s.flush()
                p1 = Property(title="آپارتمان ۱۰۵ متری خیابان گلها", tag_number="me-1", divar_id="me-1", url="https://divar.ir/v/me-1",
                              city_name="ارومیه", district="خیابان گلها", area=105, rooms=2, property_type="آپارتمان",
                              listing_type="buy", total_price=4_500_000_000, is_active=True)
                p2 = Property(title="مغازه ۴۰ متری خیابان گلها", tag_number="me-2", divar_id="me-2", url="https://divar.ir/v/me-2",
                              city_name="ارومیه", district="خیابان گلها", area=40, property_type="مغازه",
                              listing_type="buy", total_price=3_000_000_000, is_active=True)
                p3 = Property(title="آپارتمان ۹۰ متری بلوار سعدی", tag_number="me-3", divar_id="me-3", url="https://divar.ir/v/me-3",
                              city_name="ارومیه", district="بلوار سعدی", area=90, rooms=2, property_type="آپارتمان",
                              listing_type="buy", total_price=4_000_000_000, is_active=True)
                s.add_all([p1, p2, p3])
                await s.commit()
                out = {"fit": p1.id, "shop": p2.id, "dear": p3.id}
        finally:
            await eng.dispose()
        return out
    return asyncio.run(_go())


def _tok(client, username):
    r = client.post("/api/users/token", data={"username": username, "password": "pw123456"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


class TestThroughTheApp:

    def test_a_listing_arrives_and_the_right_people_are_on_the_queue(self, client):
        ids = _seed()
        boss, mina = _tok(client, "me_boss"), _tok(client, "me_mina")

        r = client.post("/api/crm/matches/run", headers=boss)
        assert r.status_code == 200, r.text
        assert r.json()["scanned"] >= 3 and r.json()["matched"] >= 2
        assert client.post("/api/crm/matches/run", headers=mina).status_code == 403, "the button is the manager's"

        # the boss sees everybody's; the apartment fits both گلها customers, the shop nobody,
        # the سعدی flat is four times the budget of the one who wanted سعدی
        allm = client.get("/api/crm/matches?status=new&limit=50", headers=boss).json()["items"]
        by_prop = {}
        for m in allm:
            by_prop.setdefault(m["property_id"], set()).add(m["customer"]["full_name"])
        # other suites share the database in CI, so containment, not equality
        assert {"خریدار گلها", "مشتری همکار"} <= by_prop.get(ids["fit"], set())
        assert ids["shop"] not in by_prop and ids["dear"] not in by_prop
        one = next(m for m in allm if m["property_id"] == ids["fit"] and m["customer"]["full_name"] == "خریدار گلها")
        assert one["score"] >= 55 and "داخل بودجه" in one["reasons"] and one["consultant"] == "مینا رضایی"
        assert one["property"]["serial_no"] is not None or one["property"]["title"].startswith("آپارتمان")

        # مینا sees her own customer's match, not her colleague's
        mine = [m for m in client.get("/api/crm/matches?status=new&limit=50", headers=mina).json()["items"]
                if m["property_id"] == ids["fit"]]
        assert {m["customer"]["full_name"] for m in mine} == {"خریدار گلها"}
        before = client.get("/api/crm/matches/summary", headers=mina).json()["new"]
        assert before >= 1

        # a second pass finds nothing new — the cursor moved
        again = client.post("/api/crm/matches/run", headers=boss).json()
        assert again["scanned"] == 0 and again["matched"] == 0

        # «تماس گرفتم» takes it off the queue and lands on the customer's timeline
        r = client.post(f"/api/crm/matches/{one['id']}/decide", headers=mina, json={"status": "contacted", "note": "بازدید فردا"})
        assert r.status_code == 200 and r.json()["status"] == "contacted" and r.json()["decided_by"] == "مینا رضایی"
        assert client.get("/api/crm/matches/summary", headers=mina).json()["new"] == before - 1
        tl = client.get(f"/api/crm/activity/customer/{one['customer_id']}", headers=mina).json()
        acts = tl if isinstance(tl, list) else tl.get("items", [])
        assert any(a["action"] == "match_call" and "بازدید فردا" in a["detail"] for a in acts)
        # and a colleague's match is not hers to decide
        other = next(m for m in allm if m["customer"]["full_name"] == "مشتری همکار")
        assert client.post(f"/api/crm/matches/{other['id']}/decide", headers=mina, json={"status": "dismissed"}).status_code == 404


class TestTheScorerBugTheEngineFound:
    """score_for_customer chained three district overlaps with `or`: a zero
    overlap on the district fell through to the empty neighbourhood and
    address and came back None, so a listing in the wrong district scored
    as if the district were unknown."""

    def _c(self, **kw):
        from types import SimpleNamespace
        base = dict(budget_max=5_000_000_000, desired_district="خیابان گلها", desired_specs="", red_lines="",
                    desired_city="ارومیه", desired_type="apartment", deal_type="buy", notes="")
        base.update(kw)
        return SimpleNamespace(**base)

    def _p(self, district, price=4_000_000_000):
        from types import SimpleNamespace
        return SimpleNamespace(listing_type="buy", total_price=price, price=None, deposit=None, rent_price=None,
                               district=district, neighborhood=None, address=None, area=None, rooms=None,
                               title="آپارتمان", description="", unit_status=None, property_type="آپارتمان",
                               category_name=None)

    def test_the_wrong_district_costs_the_listing_not_the_criterion(self):
        from app.services.match_service import score_for_customer
        right = score_for_customer(self._c(), self._p("خیابان گلها"))
        wrong = score_for_customer(self._c(), self._p("بلوار سعدی"))
        assert right["score"] > 80 and "منطقه درخواستی" in right["reasons"]
        assert wrong["score"] < 55 and "منطقهٔ دیگر" in wrong["reasons"]

    def test_an_unknown_district_is_still_not_a_penalty(self):
        from app.services.match_service import score_for_customer
        unknown = score_for_customer(self._c(), self._p(None))
        assert "منطقهٔ دیگر" not in unknown["reasons"] and unknown["score"] > 55

    def test_a_shared_road_word_is_not_a_shared_district(self):
        """score_for_customer compared the raw district text: «خیابان
        والفجر» and «خیابان دانشکده» share the word «خیابان» and
        _text_overlap alone scored that as a real overlap, so two
        plainly-wrong-district customers still cleared MIN_SCORE (55).
        rank_similar and find_duplicates already ran both sides through
        district_key() first; score_for_customer did not."""
        from app.services.match_service import score_for_customer
        different = score_for_customer(self._c(desired_district="خیابان والفجر"), self._p("خیابان دانشکده"))
        assert different["score"] < 55 and "منطقهٔ دیگر" in different["reasons"]

        same_written_two_ways = score_for_customer(self._c(desired_district="خ گلها"), self._p("خیابان گلها"))
        assert same_written_two_ways["score"] > 80 and "منطقه درخواستی" in same_written_two_ways["reasons"]


# ── «پیامک به مشتری» through the app ──────────────────────────────────────────

def _seed_for_sms():
    """One manager, two customers (one without a usable number), one listing
    with an owner's number on it, and a match for each."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.models.user import User
    from app.models.property import Property
    from app.models.crm_models import Customer, CustomerMatch
    from app.auth.jwt import get_password_hash

    async def _go():
        eng = create_async_engine(os.environ["DATABASE_URL"])
        maker = async_sessionmaker(eng, expire_on_commit=False)
        try:
            async with maker() as s:
                s.add(User(username="ms_boss", full_name="مدیر پیامک", role="super_admin", permissions=["crm"],
                           hashed_password=get_password_hash("pw123456"), is_active=True))
                c1 = Customer(full_name="سارا امیری", mobile1="۰۹۱۲۱۲۳۰۰۰۰", temperature="hot", consultant_name="مدیر پیامک",
                              desired_city="ارومیه", desired_district="خیابان گلها", desired_type="apartment",
                              deal_type="buy", budget_max=5_000_000_000)
                c2 = Customer(full_name="بی‌شماره", mobile1="تلفن ندارد", consultant_name="مدیر پیامک",
                              desired_city="ارومیه", desired_district="خیابان گلها", desired_type="apartment",
                              deal_type="buy", budget_max=5_000_000_000)
                p = Property(title="آپارتمان ۱۱۰ متری خیابان گلها، نوساز", tag_number="ms-1", divar_id="ms-1",
                             url="https://divar.ir/v/ms-1", serial_no=990001, city_name="ارومیه", district="خیابان گلها",
                             area=110, rooms=2, year_built=1403, property_type="آپارتمان", listing_type="buy",
                             total_price=4_800_000_000, has_elevator=True, has_parking=True,
                             phone_number="09149990000", seller_name="مالک محترم", is_active=True)
                s.add_all([c1, c2, p])
                await s.flush()
                m1 = CustomerMatch(property_id=p.id, customer_id=c1.id, score=82, reasons=["قیمت کم شد", "داخل بودجه"],
                                   consultant="مدیر پیامک", status="new")
                m2 = CustomerMatch(property_id=p.id, customer_id=c2.id, score=70, reasons=["داخل بودجه"],
                                   consultant="مدیر پیامک", status="new")
                s.add_all([m1, m2])
                await s.commit()
                return {"ok": m1.id, "no_number": m2.id, "customer": c1.id}
        finally:
            await eng.dispose()
    return asyncio.run(_go())


class TestSmsToTheCustomer:

    def test_preview_send_and_what_it_leaves_behind(self, client, monkeypatch):
        ids = _seed_for_sms()
        boss = _tok(client, "ms_boss")

        # the preview: the customer's number normalised, the safe card, a greeting that
        # knows this match came from a price cut — and nothing of the owner's
        r = client.get(f"/api/crm/matches/{ids['ok']}/sms", headers=boss)
        assert r.status_code == 200, r.text
        pv = r.json()
        assert pv["to"] == "09121230000" and pv["customer"] == "سارا امیری" and pv["serial_no"] == 990001
        assert pv["text"].startswith("سلام سارا امیری عزیز،\nقیمت ملکی که با درخواست شما هم‌خوانی دارد کم شده است:")
        assert "کد ملک: 990001" in pv["text"] and "متراژ 110 متر" in pv["text"] and "آسانسور" in pv["text"]
        for secret in ("09149990000", "مالک محترم", "divar.ir"):
            assert secret not in pv["text"]
        assert pv["segments"] >= 2 and pv["text"].rstrip().endswith("املاک سورین")

        # with a signature configured, it replaces the hard-coded brand line
        assert client.put("/api/sms/settings", headers=boss, json={"signature": "املاک گلها — ۰۴۴۳۳۴۴۵۵۶۶"}).status_code == 200
        txt = client.get(f"/api/crm/matches/{ids['ok']}/sms", headers=boss).json()["text"]
        assert txt.rstrip().endswith("املاک گلها — ۰۴۴۳۳۴۴۵۵۶۶") and "املاک سورین" not in txt
        client.put("/api/sms/settings", headers=boss, json={"signature": ""})

        # the customer with no usable number cannot be texted
        assert client.get(f"/api/crm/matches/{ids['no_number']}/sms", headers=boss).status_code == 400

        # a refused send changes nothing
        import app.api.routes.crm as crm
        sent = []

        async def refuse(to, text, provider="kavenegar", db=None):
            return {"success": False, "provider": "kavenegar", "response": "اعتبار کافی نیست"}
        monkeypatch.setattr(crm, "send_sms", refuse)
        r = client.post(f"/api/crm/matches/{ids['ok']}/sms", headers=boss, json={"message": pv["text"]})
        assert r.status_code == 502 and "اعتبار کافی نیست" in r.json()["detail"]
        still = [m for m in client.get("/api/crm/matches?status=new&limit=200", headers=boss).json()["items"] if m["id"] == ids["ok"]]
        assert still and still[0]["status"] == "new"

        # the send: the edited text goes as edited, the match is contacted, the log and the
        # timeline say so
        async def accept(to, text, provider="kavenegar", db=None):
            sent.append((to, text, db is not None))
            return {"success": True, "provider": "kavenegar", "messageid": 4242, "cost": 3,
                    "response": "{}"}
        monkeypatch.setattr(crm, "send_sms", accept)
        edited = pv["text"] + "\nبازدید فردا ساعت ۱۰ ممکن است."
        r = client.post(f"/api/crm/matches/{ids['ok']}/sms", headers=boss, json={"message": edited})
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["ok"] and out["to"] == "09121230000" and out["message_id"] == 4242
        assert out["match"]["status"] == "contacted" and out["match"]["decided_by"] == "مدیر پیامک"
        assert sent == [("09121230000", edited, True)], "the panel-saved key needs the session"
        assert not [m for m in client.get("/api/crm/matches?status=new&limit=200", headers=boss).json()["items"] if m["id"] == ids["ok"]]
        logs = client.get("/api/sms/messages?limit=50", headers=boss).json()
        rows = logs if isinstance(logs, list) else logs.get("items", [])
        mine = [x for x in rows if x.get("to_number") == "09121230000" and x.get("kind") == "match"]
        assert mine and mine[0]["campaign"] == "تطبیق خودکار" and mine[0]["status"] == "sent"
        tl = client.get(f"/api/crm/activity/customer/{ids['customer']}", headers=boss).json()
        acts = tl if isinstance(tl, list) else tl.get("items", [])
        assert any(a["action"] == "match_sms" and "990001" in a["detail"] and "09121230000" in a["detail"] for a in acts)
