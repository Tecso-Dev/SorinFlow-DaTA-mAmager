"""
GET /api/public/site and /api/settings/site: the brand and contact details
every page shows, editable by root and super_admin only, never hard-coded.
Through the ASGI app on a sqlite database of this file's own.
"""
import asyncio
import os
import sys

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_site_settings.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402


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
    from app.models.user import User

    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/site.db", poolclass=NullPool)
    maker = async_sessionmaker(eng, expire_on_commit=False)
    people = {"boss": User(username="boss", role="super_admin"), "agent": User(username="agent", role="admin")}

    async def build():
        async with eng.begin() as c:
            await c.run_sync(lambda sc: Base.metadata.create_all(
                sc, tables=[User.__table__, AppSetting.__table__, AuditEvent.__table__]))
        async with maker() as s:
            for u in people.values():
                u.hashed_password, u.is_active = "x", True
            s.add_all(people.values())
            await s.commit()
    asyncio.run(build())
    monkeypatch.setattr(audit, "async_session_maker", maker)
    monkeypatch.setenv("SITE_BRAND_NAME", "دفتر نمونه")

    api = FastAPI()
    api.include_router(api_router, prefix="/api")

    async def session():
        async with maker() as s:
            yield s
            await s.commit()
    api.dependency_overrides[database.get_db] = session

    def call(method, path, who=None, **kw):
        headers = {"Authorization": f"Bearer {create_access_token(access_claims(people[who]))}"} if who else {}

        async def go():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://t") as c:
                return await c.request(method, path, headers=headers, **kw)
        return asyncio.run(go())

    yield call
    asyncio.run(eng.dispose())


def test_the_public_site_needs_no_login_and_starts_from_the_environment(office):
    r = office("GET", "/api/public/site")
    assert r.status_code == 200
    assert r.json()["brandName"] == "دفتر نمونه"
    assert set(r.json()) >= {"brandName", "brandNameLatin", "domain", "phone", "email"}


def test_a_super_admin_saves_it_and_every_page_sees_it(office):
    r = office("PUT", "/api/settings/site", "boss", json={"agencyName": "املاک نمونه", "phone": "021-000"})
    assert r.status_code == 200, r.text
    site = office("GET", "/api/public/site").json()
    assert (site["agencyName"], site["phone"]) == ("املاک نمونه", "021-000")
    assert site["brandName"] == "دفتر نمونه", "a field not sent is left as it was"


def test_an_agent_cannot_change_or_read_the_editor(office):
    assert office("PUT", "/api/settings/site", "agent", json={"brandName": "x"}).status_code == 403
    assert office("GET", "/api/settings/site", "agent").status_code == 403
    assert office("PUT", "/api/settings/site", json={"brandName": "x"}).status_code == 401


def test_an_emptied_brand_falls_back_and_bad_values_are_refused(office):
    office("PUT", "/api/settings/site", "boss", json={"brandName": "  "})
    assert office("GET", "/api/public/site").json()["brandName"] == "دفتر نمونه"
    assert office("PUT", "/api/settings/site", "boss", json={"email": "not-an-email"}).status_code == 422
    assert office("PUT", "/api/settings/site", "boss", json={"domain": "http://x"}).status_code == 422
