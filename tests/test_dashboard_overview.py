"""
GET /api/stats/overview — the new dashboard's numbers — and PUT /api/stats/target.

Through the real ASGI app on a sqlite database of this file's own. What is
checked is what a person would see: an agent's calls, call hours and team
row are their own; root and super_admin see the office; the funnel is
cumulative; the monthly target is set by a super_admin only.
"""
import asyncio
import os
import sys
from datetime import date, datetime, timedelta, timezone

import fakeredis
import fakeredis.aioredis
import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_dashboard_overview.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.services import dashboard_overview as ov  # noqa: E402

NOW = datetime.now(timezone.utc)


def _naive(ts):
    return ts.astimezone(timezone.utc).replace(tzinfo=None)


@pytest.fixture
def office(tmp_path, monkeypatch):
    from fastapi import FastAPI
    import app.database as database
    import app.services.audit as audit
    from app.api.routes import router as api_router
    from app.auth.jwt import access_claims, create_access_token
    from app.database import Base
    from app.models.app_setting import AppSetting
    from app.models.audit_event import AuditEvent
    from app.models.crm_models import ActivityLog, CalendarEvent, Customer, CustomerMatch, Deal
    from app.models.lead import Lead
    from app.models.property import Category, City, Property
    from app.models.user import User

    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/ov.db", poolclass=NullPool)
    maker = async_sessionmaker(eng, expire_on_commit=False)
    tables = [User.__table__, AuditEvent.__table__, AppSetting.__table__, City.__table__, Category.__table__,
              Property.__table__, Lead.__table__, ActivityLog.__table__, CalendarEvent.__table__,
              Customer.__table__, CustomerMatch.__table__, Deal.__table__]
    people = {
        "boss": User(username="boss", full_name="مدیر دفتر", role="super_admin"),
        "ali": User(username="ali", full_name="علی مشاور", role="admin", permissions=["stats", "crm"]),
        "sara": User(username="sara", full_name="سارا مشاور", role="admin", permissions=["stats", "crm"]),
        "nostats": User(username="nostats", full_name="بی‌آمار", role="admin", permissions=["crm"]),
    }

    async def build():
        async with eng.begin() as c:
            await c.run_sync(lambda sc: Base.metadata.create_all(sc, tables=tables))
        async with maker() as s:
            for u in people.values():
                u.hashed_password, u.is_active = "x", True
            s.add_all(people.values())
            props = [Property(tag_number=f"T{i}", serial_no=i + 1, divar_id=f"p{i}", title="t", url=f"https://divar.ir/v/p{i}", city_name="تهران",
                              district=["ونک", "پونک"][i % 2], price_per_meter=100 + i,
                              created_at=_naive(NOW - timedelta(hours=1))) for i in range(4)]
            s.add_all(props)
            await s.flush()
            statuses = ["new", "new", "contacted", "visit", "contract_meeting", "closed", "rented", "rejected"]
            s.add_all([Lead(property_id=props[0].id, phone_number="0912", status=st,
                            created_at=_naive(NOW - timedelta(days=2))) for st in statuses])
            # a call on Saturday 10:00 Tehran by ali, two by sara, all inside the window
            sat = NOW.astimezone(ov.TEHRAN)
            sat = (sat - timedelta(days=(ov.weekday_sat0(sat.date())))).replace(hour=10, minute=5)
            if sat > NOW:
                sat -= timedelta(days=7)
            s.add_all([
                ActivityLog(entity_type="lead", entity_id=1, action="call", detail="پاسخ داد — ok",
                            actor="علی مشاور", created_at=_naive(sat)),
                ActivityLog(entity_type="lead", entity_id=2, action="call", detail="پاسخ نداد",
                            actor="سارا مشاور", created_at=_naive(sat)),
                ActivityLog(entity_type="lead", entity_id=3, action="call", detail="پاسخ نداد",
                            actor="سارا مشاور", created_at=_naive(sat)),
                ActivityLog(entity_type="lead", entity_id=6, action="status_change",
                            detail="وضعیت به «closed» تغییر کرد", actor="سارا مشاور",
                            created_at=_naive(NOW - timedelta(days=1))),
                Deal(title="d1", deal_type="rent", status="closed", commission=5_000_000,
                     contract_date=_naive(NOW - timedelta(hours=2))),
                Deal(title="d2", deal_type="buy", status="negotiating",
                     contract_date=_naive(NOW - timedelta(hours=2))),
                Customer(full_name="c1", source="divar", temperature="hot"),
                Customer(full_name="c2", source="divar", temperature="warm"),
                Customer(full_name="c3", source="referral", temperature="cold"),
            ])
            await s.commit()
    asyncio.run(build())

    server = fakeredis.FakeServer()

    async def get_redis():
        return fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    monkeypatch.setattr(database, "get_redis", get_redis)
    monkeypatch.setattr(audit, "async_session_maker", maker)

    api = FastAPI()
    api.include_router(api_router, prefix="/api")

    async def session():
        async with maker() as s:
            yield s
            await s.commit()
    api.dependency_overrides[database.get_db] = session

    def call(who, method="GET", path="/api/stats/overview", **kw):
        token = create_access_token(access_claims(people[who]))

        async def go():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://t") as c:
                return await c.request(method, path, headers={"Authorization": f"Bearer {token}"}, **kw)
        return asyncio.run(go())

    yield call
    asyncio.run(eng.dispose())


def test_an_agent_sees_their_own_calls_and_only_their_own_row(office):
    r = office("sara")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["scope"] == "self"
    assert [p["name"] for p in d["team"]] == ["سارا مشاور"]
    row = d["team"][0]
    assert (row["calls"], row["answered"], row["won"]) == (2, 0, 1)
    assert sum(map(sum, d["call_grid"]["rows"])) == 2
    # Saturday is the first row, 10:00 the third column (8, 9, 10)
    assert d["call_grid"]["rows"][0][2] == 2


def test_super_admin_sees_the_whole_office(office):
    d = office("boss").json()
    assert d["scope"] == "office"
    names = {p["name"] for p in d["team"]}
    assert {"علی مشاور", "سارا مشاور", "مدیر دفتر"} <= names
    assert sum(map(sum, d["call_grid"]["rows"])) == 3
    ali = next(p for p in d["team"] if p["name"] == "علی مشاور")
    assert ali["answer_rate"] == 100.0


def test_the_funnel_is_cumulative_and_leaves_rejected_out(office):
    d = office("ali").json()
    f = {s["key"]: s["count"] for s in d["funnel"]}
    assert f == {"new": 7, "contacted": 5, "visit": 4, "contract_meeting": 3, "won": 2}
    assert d["kpis"]["leads_open"] == 5
    assert d["kpis"]["conversion"] == round(2 / 8 * 100, 1)


def test_office_wide_numbers(office):
    d = office("ali").json()
    k = d["kpis"]
    assert k["listings_today"] + k["listings_yesterday"] == 4
    assert k["deals"] == 1 and k["commission"] == 5_000_000, "a negotiating deal is not done"
    assert d["sources"] == [{"key": "divar", "count": 2}, {"key": "referral", "count": 1}]
    assert d["districts"]["city"] == "تهران"
    assert {x["name"] for x in d["districts"]["items"]} == {"ونک", "پونک"}
    assert len(d["trend"]) == d["days"] == 30
    assert sum(t["listings"] for t in d["trend"]) == 4


def test_the_stats_permission_guards_it(office):
    assert office("nostats").status_code == 403


def test_only_a_super_admin_sets_the_monthly_target(office):
    assert office("ali", "PUT", "/api/stats/target", json={"deals": 5}).status_code == 403
    assert office("ali").json()["target"]["can_edit"] is False
    ok = office("boss", "PUT", "/api/stats/target", json={"deals": 12, "commission": 650_000_000})
    assert ok.status_code == 200, ok.text
    t = office("ali").json()["target"]
    assert (t["deals_target"], t["commission_target"]) == (12, 650_000_000)
    assert t["deals"] == 1


# ── the pure parts ───────────────────────────────────────────────────────────

def test_jalali_month_start():
    from app.services.dpa_service import to_jalali
    for d in (date(2026, 9, 25), date(2026, 3, 21), date(2026, 3, 20), date(2025, 12, 31)):
        start = ov.jalali_month_start(d)
        assert to_jalali(datetime.combine(start, datetime.min.time())).endswith("/01")
        assert 0 <= (d - start).days <= 30
    assert ov.jalali_month_start(date(2026, 9, 25)) == date(2026, 9, 23)   # 1 Mehr 1405


def test_a_naive_timestamp_is_utc_and_lands_on_the_tehran_day():
    late = datetime(2026, 9, 24, 21, 0)       # 00:30 on the 25th in Tehran
    assert ov.daily([late], 3, now=datetime(2026, 9, 25, 12, tzinfo=timezone.utc)) == {
        "2026-09-23": 0, "2026-09-24": 0, "2026-09-25": 1}


def test_status_reached_and_ratio():
    assert ov.status_reached("وضعیت به «rented» تغییر کرد") == "rented"
    assert ov.status_reached("یادداشت") is None
    assert ov.ratio(1, 0) is None and ov.pct_change(5, 0) is None and ov.pct_change(6, 4) == 50.0
    assert ov.parse_target("not json") == {"deals": None, "commission": None}
