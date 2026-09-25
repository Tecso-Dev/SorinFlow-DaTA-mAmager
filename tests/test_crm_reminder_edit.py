"""
PATCH /api/crm/reminders/{id} — the old panel could only delete and recreate
a reminder; the new panel edits it in place.

Through the real ASGI app on a sqlite database of this file's own.
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

import fakeredis
import fakeredis.aioredis
import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_crm_reminder_edit.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

NOW = datetime.now(timezone.utc)


@pytest.fixture
def office(tmp_path, monkeypatch):
    from fastapi import FastAPI
    import app.database as database
    import app.services.audit as audit
    from app.api.routes import router as api_router
    from app.auth.jwt import access_claims, create_access_token
    from app.database import Base
    from app.models.audit_event import AuditEvent
    from app.models.crm_models import Contact, Reminder
    from app.models.user import User

    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/reminders.db", poolclass=NullPool)
    maker = async_sessionmaker(eng, expire_on_commit=False)
    tables = [User.__table__, AuditEvent.__table__, Contact.__table__, Reminder.__table__]

    agent = User(username="agent", full_name="مشاور", role="admin", permissions=["crm"],
                 hashed_password="x", is_active=True)
    contact = Contact(name="مشتری", phone="09120000000")

    async def build():
        async with eng.begin() as c:
            await c.run_sync(lambda sc: Base.metadata.create_all(sc, tables=tables))
        async with maker() as s:
            s.add_all([agent, contact])
            await s.flush()
            s.add(Reminder(title="پیگیری قرارداد", remind_at=(NOW + timedelta(days=1)).replace(tzinfo=None),
                            repeat="none", channel="in_app"))
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

    def call(method, path, **kw):
        token = create_access_token(access_claims(agent))

        async def go():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://t") as c:
                return await c.request(method, path, headers={"Authorization": f"Bearer {token}"}, **kw)
        return asyncio.run(go())

    call.contact_id = contact.id  # expire_on_commit=False keeps it readable

    yield call
    asyncio.run(eng.dispose())


def _the_reminder(office):
    r = office("GET", "/api/crm/reminders")
    assert r.status_code == 200, r.text
    return r.json()["items"][0]


def test_title_and_channel_can_be_edited_without_touching_remind_at(office):
    before = _the_reminder(office)
    r = office("PATCH", f"/api/crm/reminders/{before['id']}", json={"title": "پیگیری تازه"})
    assert r.status_code == 200, r.text
    after = r.json()
    assert after["title"] == "پیگیری تازه"
    assert after["remind_at"] == before["remind_at"]
    assert after["channel"] == "in_app"


def test_switching_to_sms_saves_the_number(office):
    before = _the_reminder(office)
    r = office("PATCH", f"/api/crm/reminders/{before['id']}",
               json={"channel": "sms", "sms_to": "09121234567"})
    assert r.status_code == 200, r.text
    after = r.json()
    assert after["channel"] == "sms"
    assert after["sms_to"] == "09121234567"


def test_remind_at_can_be_moved_and_is_validated_like_create(office):
    before = _the_reminder(office)
    moved = (NOW + timedelta(days=3)).replace(microsecond=0).isoformat()
    ok = office("PATCH", f"/api/crm/reminders/{before['id']}", json={"remind_at": moved})
    assert ok.status_code == 200, ok.text
    assert ok.json()["remind_at"].startswith(moved[:16])

    bad = office("PATCH", f"/api/crm/reminders/{before['id']}", json={"remind_at": "not-a-date"})
    assert bad.status_code == 400

    empty = office("PATCH", f"/api/crm/reminders/{before['id']}", json={"remind_at": ""})
    assert empty.status_code == 400


def test_contact_can_be_attached(office):
    before = _the_reminder(office)
    r = office("PATCH", f"/api/crm/reminders/{before['id']}", json={"contact_id": office.contact_id})
    assert r.status_code == 200, r.text
    assert r.json()["contact_id"] == office.contact_id


def test_missing_reminder_is_404(office):
    r = office("PATCH", "/api/crm/reminders/999999", json={"title": "x"})
    assert r.status_code == 404


def test_repeat_can_be_set_to_a_recurring_value(office):
    before = _the_reminder(office)
    r = office("PATCH", f"/api/crm/reminders/{before['id']}", json={"repeat": "weekly"})
    assert r.status_code == 200, r.text
    assert r.json()["repeat"] == "weekly"
