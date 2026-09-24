"""
هشدار کاهش قیمت — the trail the scraper keeps, read back.

Every listing has carried a price trail since August and nothing read it:
a cut was a log line. The watcher walks the moves since the last pass,
keeps the real cuts, files one alert per move, re-matches the listing
against the customers (it may fit a budget now), and tells the Telegram chat.
"""
import os
import sys
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_price_watch.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.crm import price_watch as pw   # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def _p(listing_type="buy", trail=None, **kw):
    base = dict(listing_type=listing_type, total_price=None, price=None, deposit=None, rent_price=None, price_history=trail or [])
    base.update(kw)
    return SimpleNamespace(**base)


class TestReadingTheTrail:

    def test_a_sale_cut_reads_off_the_headline_figure(self):
        p = _p(trail=[{"at": "x", "total_price": 4_400_000_000, "from": {"total_price": 5_000_000_000}}], total_price=4_400_000_000)
        assert pw.latest_move(p) == (5_000_000_000, 4_400_000_000, "buy")
        assert pw.drop_pct(5_000_000_000, 4_400_000_000) == -12

    def test_a_rental_is_judged_on_deposit_plus_converted_rent(self):
        # deposit down 100M, rent unchanged: a real cut
        p = _p("rent", trail=[{"at": "x", "deposit": 400_000_000, "from": {"deposit": 500_000_000}}], deposit=400_000_000, rent_price=10_000_000)
        before, after, kind = pw.latest_move(p)
        assert (before, after, kind) == (800_000_000, 700_000_000, "rent") and pw.drop_pct(before, after) == -12
        # deposit down 300M but rent up 10M: a swap, not a cut
        p = _p("rent", trail=[{"at": "x", "deposit": 300_000_000, "rent_price": 15_000_000, "from": {"deposit": 600_000_000, "rent_price": 5_000_000}}],
               deposit=300_000_000, rent_price=15_000_000)
        before, after, _ = pw.latest_move(p)
        assert pw.drop_pct(before, after) == 0

    def test_an_empty_or_unreadable_trail_is_no_move(self):
        assert pw.latest_move(_p()) is None
        assert pw.latest_move(_p(trail=[{"at": "x", "total_price": 1}])) is None, "no «from», nothing to compare"

    def test_the_bar_and_the_cadence(self):
        assert pw.MIN_DROP_PCT == 3 and pw.TICK_SECONDS == 300
        src = (ROOT / "app/crm/price_watch.py").read_text(encoding="utf-8")
        assert "ZoneInfo" not in src
        import app.main as m
        loops = {name: (fn, roles) for name, fn, _stall, roles in m._loops()}
        assert loops["price_watch"][0] is pw.watch_loop
        assert set(loops["price_watch"][1]) == {"all", "scheduler"}

    def test_the_panel_has_the_card(self):
        html = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
        js = (ROOT / "frontend/js/app.js").read_text(encoding="utf-8")
        assert 'id="drops-card"' in html and 'onclick="runPriceWatchNow()"' in html
        assert "loadCalls(); loadMatches(); loadPriceDrops(); break;" in js
        fn = js[js.index("function _dropCard"):js.index("async function pdDecide")]
        assert "pd-from" in fn and "pd-to" in fn and "pdDecide(${a.id}, 'seen')" in fn


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
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.models.user import User
    from app.models.property import Property
    from app.models.crm_models import Customer
    from app.auth.jwt import get_password_hash

    async def _go():
        eng = create_async_engine(os.environ["DATABASE_URL"])
        maker = async_sessionmaker(eng, expire_on_commit=False)
        now = datetime.now(timezone.utc)
        try:
            async with maker() as s:
                s.add(User(username="pw_boss", full_name="مدیر", role="super_admin", permissions=["crm"],
                           hashed_password=get_password_hash("pw123456"), is_active=True))
                # wanted گلها under 4.5B — the flat was 5B last week
                s.add(Customer(full_name="خریدار صبور", mobile1="09121110009", temperature="warm", consultant_name="مدیر",
                               desired_city="ارومیه", desired_district="خیابان گلها", desired_type="apartment",
                               deal_type="buy", budget_max=4_500_000_000))
                cut = Property(title="آپارتمان ۱۱۰ متری خیابان گلها", tag_number="pw-1", divar_id="pw-1", url="https://divar.ir/v/pw-1",
                               city_name="ارومیه", district="خیابان گلها", area=110, rooms=2, property_type="آپارتمان", listing_type="buy",
                               total_price=4_400_000_000, previous_price=5_000_000_000, price_changed_at=now,
                               price_history=[{"at": now.isoformat(), "total_price": 4_400_000_000, "from": {"total_price": 5_000_000_000}}], is_active=True)
                tiny = Property(title="آپارتمان ۸۰ متری", tag_number="pw-2", divar_id="pw-2", url="https://divar.ir/v/pw-2",
                                city_name="ارومیه", district="بلوار سعدی", property_type="آپارتمان", listing_type="buy",
                                total_price=3_960_000_000, price_changed_at=now,
                                price_history=[{"at": now.isoformat(), "total_price": 3_960_000_000, "from": {"total_price": 4_000_000_000}}], is_active=True)
                up = Property(title="ویلایی ۲۰۰ متری", tag_number="pw-3", divar_id="pw-3", url="https://divar.ir/v/pw-3",
                              city_name="ارومیه", district="بلوار امین", property_type="ویلایی", listing_type="buy",
                              total_price=9_000_000_000, price_changed_at=now,
                              price_history=[{"at": now.isoformat(), "total_price": 9_000_000_000, "from": {"total_price": 8_000_000_000}}], is_active=True)
                s.add_all([cut, tiny, up])
                await s.commit()
                return {"cut": cut.id, "tiny": tiny.id, "up": up.id}
        finally:
            await eng.dispose()
    return asyncio.run(_go())


def _tok(client, username):
    r = client.post("/api/users/token", data={"username": username, "password": "pw123456"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


class TestThroughTheApp:

    def test_a_cut_is_announced_once_and_rematched(self, client):
        ids = _seed()
        boss = _tok(client, "pw_boss")
        r = client.post("/api/crm/price-drops/run", headers=boss)
        assert r.status_code == 200, r.text
        assert r.json()["drops"] == 1 and r.json()["matches"] >= 1, r.json()   # other suites' customers may fit too

        items = client.get("/api/crm/price-drops?status=new", headers=boss).json()["items"]
        # other suites (the digest's) leave alerts of their own in a shared database
        got = {i["property_id"] for i in items} & set(ids.values())
        assert got == {ids["cut"]}, "the 1% move and the rise are not cuts"
        a = next(i for i in items if i["property_id"] == ids["cut"])
        assert (a["from_amount"], a["to_amount"], a["delta_pct"]) == (5_000_000_000, 4_400_000_000, -12) and a["matches_created"] >= 1
        assert a["property"]["title"].startswith("آپارتمان ۱۱۰")

        # the customer who could not afford it last week is on the match queue now, and knows why
        m = [x for x in client.get("/api/crm/matches?status=new&limit=50", headers=boss).json()["items"]
             if x["property_id"] == ids["cut"] and x["customer"]["full_name"] == "خریدار صبور"]
        assert len(m) == 1 and m[0]["reasons"][0] == "قیمت کم شد"

        # a second pass is idle; «دیدم» takes it off the list
        assert client.post("/api/crm/price-drops/run", headers=boss).json()["drops"] == 0
        before = client.get("/api/crm/price-drops/summary", headers=boss).json()["new"]
        assert client.post(f"/api/crm/price-drops/{a['id']}/decide", headers=boss, json={"status": "seen"}).json()["seen_by"] == "مدیر"
        assert client.get("/api/crm/price-drops/summary", headers=boss).json()["new"] == before - 1
