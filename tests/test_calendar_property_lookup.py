"""
GET /api/crm/calendar/property-lookup/{serial} — the event dialog's live
preview while a کد ملک is being typed.

The dialog needs to show the address before the event is saved (unlike
_resolve_property_serial, which only runs on submit), and the only routers a
plain «crm» permission can reach are /crm/* — /properties and /filing both
sit behind their own separate permissions a calendar-only user need not have.
"""
import asyncio
import os
import sys

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_cal_lookup.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402


@pytest.fixture
def office(tmp_path, monkeypatch):
    from fastapi import FastAPI
    import app.database as database
    from app.api.routes import router as api_router
    from app.auth.jwt import access_claims, create_access_token
    from app.database import Base
    from app.models.property import Property
    from app.models.user import User

    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/cl.db", poolclass=NullPool)
    maker = async_sessionmaker(eng, expire_on_commit=False)
    tables = [User.__table__, Property.__table__]
    agent = User(username="agent1", full_name="مشاور", role="admin", permissions=["crm"])

    async def build():
        async with eng.begin() as c:
            await c.run_sync(lambda sc: Base.metadata.create_all(sc, tables=tables))
        async with maker() as s:
            agent.hashed_password, agent.is_active = "x", True
            s.add(agent)
            s.add_all([
                Property(tag_number="T1", serial_no=1001, divar_id="d1", title="آپارتمان ونک",
                         url="https://divar.ir/v/1", city_name="تهران", district="ونک",
                         neighborhood="جردن", address="خیابان ولیعصر"),
                Property(tag_number="T2", serial_no=1002, divar_id="d2", title="ویلای لواسان",
                         url="https://divar.ir/v/2", city_name="لواسان", district="کیان",
                         neighborhood=None, address=None),
            ])
            await s.commit()
    asyncio.run(build())

    api = FastAPI()
    api.include_router(api_router, prefix="/api")

    async def session():
        async with maker() as s:
            yield s
            await s.commit()
    api.dependency_overrides[database.get_db] = session

    def call(path):
        token = create_access_token(access_claims(agent))

        async def go():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://t") as c:
                return await c.request("GET", path, headers={"Authorization": f"Bearer {token}"})
        return asyncio.run(go())

    yield call
    asyncio.run(eng.dispose())


def test_a_known_serial_returns_its_address(office):
    r = office("/api/crm/calendar/property-lookup/1001")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["serial_no"] == 1001
    assert d["title"] == "آپارتمان ونک"
    assert d["location"] == "خیابان ولیعصر"


def test_a_property_with_no_address_falls_back_to_city_and_district(office):
    """Same rule create_event uses, so the dialog's preview matches what a
    save with an empty location field would actually store."""
    r = office("/api/crm/calendar/property-lookup/1002")
    assert r.status_code == 200, r.text
    assert r.json()["location"] == "لواسان کیان"


def test_an_unknown_serial_is_a_404_not_a_500(office):
    r = office("/api/crm/calendar/property-lookup/9999")
    assert r.status_code == 404


def test_a_plain_crm_permission_can_reach_it(office):
    """The dialog is on the calendar tab, which only requires «crm» — not
    «properties» or «filing», which the agent in this test does not have."""
    r = office("/api/crm/calendar/property-lookup/1001")
    assert r.status_code == 200
