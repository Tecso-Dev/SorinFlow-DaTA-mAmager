"""
درخواست‌های پرتال → موتور تطبیق.

A visitor's request was a row in its own table that the engine never read.
Now it becomes a CRM customer the moment it is filed — source «پرتال», no
consultant, the form's fields mapped onto the intake form's — and the
request's status follows what the office does with that customer.
"""
import os
import sys
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_portal_bridge.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.crm import portal_bridge as pb   # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
JS = (ROOT / "frontend/js/app.js").read_text(encoding="utf-8")


def _req(**kw):
    base = dict(id=7, deal_type="buy", property_kind=None, city=None, districts=None, budget_min=None, budget_max=None,
                deposit_max=None, rent_max=None, area_min=None, area_max=None, rooms_min=None, year_built_min=None,
                needs_elevator=False, needs_parking=False, needs_storage=False, description=None,
                contact_name=None, contact_phone=None, customer_id=None, status="new")
    base.update(kw)
    return SimpleNamespace(**base)


class TestTheTranslation:

    def test_a_purchase_request_reads_like_an_intake_form(self):
        c = pb.criteria_of(_req(property_kind="villa", city="ارومیه", districts="خیابان گلها، بلوار سعدی",
                                budget_max=5_000_000_000, area_min=80, area_max=120, rooms_min=2,
                                needs_parking=True, needs_storage=True, year_built_min=1395,
                                description="طبقهٔ بالا نباشد", contact_name="سارا امیری", contact_phone="09121234567"))
        assert c["full_name"] == "سارا امیری" and c["mobile1"] == "09121234567"
        assert c["source"] == "portal" and c["consultant_name"] is None and c["temperature"] == "warm"
        assert c["desired_city"] == "ارومیه" and c["desired_district"] == "خیابان گلها، بلوار سعدی"
        assert c["desired_type"] == "house" and c["deal_type"] == "buy" and c["budget_max"] == 5_000_000_000
        assert c["desired_specs"] == "100 متر / 2 خواب", "the middle of the range, the way the scorer wants one number"
        assert "درخواست پرتال #7" in c["notes"] and "نیاز دارد: پارکینگ، انباری" in c["notes"]
        assert "ساخت از 1395" in c["notes"] and "طبقهٔ بالا نباشد" in c["notes"]

    def test_a_rental_is_judged_on_the_deposit(self):
        # a rent-only ceiling is converted the way the price watcher converts it
        c = pb.criteria_of(_req(deal_type="rent", property_kind="apartment", rent_max=10_000_000))
        assert c["budget_max"] == 300_000_000 and c["deal_type"] == "rent" and c["desired_type"] == "apartment"
        c = pb.criteria_of(_req(deal_type="rent", deposit_max=400_000_000, rent_max=10_000_000))
        assert c["budget_max"] == 400_000_000, "a stated deposit ceiling wins"
        assert pb.criteria_of(_req(property_kind="store"))["desired_type"] == "shop"

    def test_a_nameless_request_still_becomes_somebody(self):
        c = pb.criteria_of(_req(), user=SimpleNamespace(full_name="کاربر ثبت‌نامی", phone="09120000000"))
        assert c["full_name"] == "کاربر ثبت‌نامی" and c["mobile1"] == "09120000000"
        assert pb.criteria_of(_req())["full_name"] == "کاربر پرتال"
        assert pb.criteria_of(_req(area_min=70))["desired_specs"] == "70 متر"
        assert pb.criteria_of(_req())["desired_specs"] is None


class TestTheShape:

    def test_every_door_is_wired(self):
        portal = (ROOT / "app/api/routes/portal.py").read_text(encoding="utf-8")
        create = portal[portal.index('@router.post("/requests", status_code=201)'):portal.index('@router.get("/requests/mine")')]
        assert "portal_bridge.customer_for(db, req, current_user)" in create
        delete = portal[portal.index('@router.delete("/requests/{request_id}")'):portal.index('@router.post("/tickets"')]
        assert 'cust.source == "portal"' in delete and "await db.delete(cust)" in delete
        engine = (ROOT / "app/crm/match_engine.py").read_text(encoding="utf-8")
        assert "portal_bridge.sync_open(db)" in engine and "portal_bridge.note_matches(db," in engine
        crm = (ROOT / "app/api/routes/crm.py").read_text(encoding="utf-8")
        assert crm.count("portal_bridge.note_contact(db, row.customer_id)") == 2, "a call and a text both count"

    def test_the_link_is_a_migration_not_a_boot_step(self):
        model = (ROOT / "app/models/portal.py").read_text(encoding="utf-8")
        assert 'ForeignKey("crm_customers.id", ondelete="SET NULL", name="fk_portal_requests_customer")' in model
        rev = (ROOT / "migrations/versions/0002_portal_request_customer.py").read_text(encoding="utf-8")
        assert 'revision = "0002"' in rev and 'down_revision = "0001"' in rev
        assert '"fk_portal_requests_customer"' in rev and "ix_portal_property_requests_customer_id" in rev
        assert "def downgrade" in rev and "op.drop_column" in rev
        db = (ROOT / "app/database.py").read_text(encoding="utf-8")
        assert "customer_id" not in db[db.index("for step in ("):db.index("_seed_reference_data):")]

    def test_the_panel_knows(self):
        fn = JS[JS.index("async function loadPortalRequests"):JS.index("async function updatePortalRequest")]
        assert "showMatchesForCustomer(${r.customer_id})" in fn and "در موتور تطبیق" in fn
        assert "portal: 'پرتال'" in JS


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
    """A manager, a verified visitor, a listing that fits what the visitor is
    about to ask for, and an old request from before the bridge existed."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.models.user import User
    from app.models.property import Property
    from app.models.portal import PropertyRequest
    from app.auth.jwt import get_password_hash

    async def _go():
        eng = create_async_engine(os.environ["DATABASE_URL"])
        maker = async_sessionmaker(eng, expire_on_commit=False)
        try:
            async with maker() as s:
                s.add(User(username="pb_boss", full_name="مدیر پرتال", role="super_admin", permissions=["crm", "portal"],
                           hashed_password=get_password_hash("pw123456"), is_active=True))
                vis = User(username="pb_vis", full_name="نازنین کریمی", role="visitor", permissions=[],
                           hashed_password=get_password_hash("pw123456"), is_active=True,
                           phone="09129990001", phone_verified=True)
                s.add(vis)
                fit = Property(title="آپارتمان ۱۰۰ متری خیابان گلها، دو خواب", tag_number="pb-1", divar_id="pb-1", url="https://divar.ir/v/pb-1",
                               city_name="ارومیه", district="خیابان گلها", area=100, rooms=2, property_type="آپارتمان",
                               listing_type="buy", total_price=4_500_000_000, is_active=True, phone_number="09141110000")
                s.add(fit)
                await s.flush()
                old = PropertyRequest(user_id=vis.id, deal_type="rent", property_kind="apartment", city="ارومیه",
                                      districts="دانشکده", deposit_max=300_000_000, contact_name="نازنین کریمی",
                                      contact_phone="09129990001", status="new")
                s.add(old)
                await s.commit()
                return {"fit": fit.id, "old": old.id}
        finally:
            await eng.dispose()
    return asyncio.run(_go())


def _tok(client, username):
    r = client.post("/api/users/token", data={"username": username, "password": "pw123456"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


class TestThroughTheApp:

    def test_a_request_is_a_customer_the_engine_rings_and_the_request_follows(self, client):
        ids = _seed()
        boss, vis = _tok(client, "pb_boss"), _tok(client, "pb_vis")

        # filed by the visitor → a customer of the office, at once
        r = client.post("/api/portal/requests", headers=vis, json={
            "deal_type": "buy", "property_kind": "apartment", "city": "ارومیه", "districts": "خیابان گلها",
            "budget_max": 5_000_000_000, "area_min": 90, "area_max": 110, "rooms_min": 2, "needs_parking": True})
        assert r.status_code == 201, r.text
        req = r.json()
        assert req["customer_id"], "the request must know the customer it became"
        cust = client.get(f"/api/crm/customers/{req['customer_id']}", headers=boss).json()
        cust = cust.get("customer", cust)
        assert cust["source"] == "portal" and cust["full_name"] == "نازنین کریمی" and cust["mobile1"] == "09129990001"
        assert cust["desired_district"] == "خیابان گلها" and cust["desired_type"] == "apartment"
        assert cust["budget_max"] == 5_000_000_000 and cust["desired_specs"] == "100 متر / 2 خواب"
        assert cust["consultant_name"] is None, "nobody's yet — every consultant sees the match"

        # the engine's next pass: the fitting listing lands on the queue for that customer,
        # and the request reads «مورد پیدا شد» with the listing on it
        run = client.post("/api/crm/matches/run", headers=boss).json()
        assert run["matched"] >= 1, run
        theirs = [m for m in client.get("/api/crm/matches?status=new&limit=200", headers=boss).json()["items"]
                  if m["customer_id"] == req["customer_id"]]
        mine = [m for m in theirs if m["property_id"] == ids["fit"]]
        assert len(mine) == 1 and mine[0]["score"] >= 55 and "منطقه درخواستی" in mine[0]["reasons"]
        admin = {x["id"]: x for x in client.get("/api/portal/admin/requests?limit=200", headers=boss).json()["items"]}
        assert admin[req["id"]]["status"] == "matched"
        # other suites' listings share this database; whichever fit came first is on the request
        assert admin[req["id"]]["matched_property_id"] in {m["property_id"] for m in theirs}
        assert admin[req["id"]]["customer_id"] == req["customer_id"]

        # the old request from before the bridge was picked up by the same pass
        assert admin[ids["old"]]["customer_id"], "open requests without a customer are handed over"
        old_cust = client.get(f"/api/crm/customers/{admin[ids['old']]['customer_id']}", headers=boss).json()
        old_cust = old_cust.get("customer", old_cust)
        assert old_cust["deal_type"] == "rent" and old_cust["budget_max"] == 300_000_000

        # «تماس گرفتم» on the match: the request is «تماس گرفته شد», and the visitor sees it
        assert client.post(f"/api/crm/matches/{mine[0]['id']}/decide", headers=boss,
                           json={"status": "contacted", "note": "بازدید فردا"}).status_code == 200
        admin = {x["id"]: x for x in client.get("/api/portal/admin/requests?limit=200", headers=boss).json()["items"]}
        assert admin[req["id"]]["status"] == "contacted"
        own = {x["id"]: x for x in client.get("/api/portal/requests/mine", headers=vis).json()["items"]}
        assert own[req["id"]]["status"] == "contacted"

        # withdrawn: the customer and their matches go with it
        assert client.delete(f"/api/portal/requests/{req['id']}", headers=vis).status_code == 200
        assert client.get(f"/api/crm/customers/{req['customer_id']}", headers=boss).status_code == 404
        assert not [m for m in client.get("/api/crm/matches?status=all&limit=200", headers=boss).json()["items"]
                    if m["customer_id"] == req["customer_id"]]
