"""
Wrong guesses sent in parallel are held to the same cap as ones sent one
after another — on the portal login and on «تغییر رمز» as on the panel's
own login: each attempt is counted first, in one Redis transaction, and
decided on the count that transaction returned (take_login_attempt).

A sqlite database and a fake Redis of this file's own; the requests go
through the ASGI app together, with asyncio.gather.
"""
import asyncio
import os
import sys

import bcrypt
import fakeredis
import fakeredis.aioredis
import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_login_attempt_races.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.database import Base  # noqa: E402
from app.models.audit_event import AuditEvent  # noqa: E402
from app.models.user import User  # noqa: E402

RIGHT = "right-password-1"
PHONE, EMAIL = "09121230001", "visitor.race@example.com"
EXTRA = 6          # sent beyond the cap


def _hash(pw):
    # the real function, at the lowest cost: the race is in the counting, not bcrypt
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt(4)).decode()


@pytest.fixture
def office(tmp_path, monkeypatch):
    from fastapi import FastAPI
    import app.database as database
    import app.services.audit as audit
    import app.services.verification as verification
    from app.api.routes import router as api_router
    from app.auth.jwt import access_claims, create_access_token

    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/races.db", poolclass=NullPool)
    maker = async_sessionmaker(eng, expire_on_commit=False)
    people = {
        "visitor": User(username=PHONE, phone=PHONE, email=EMAIL, full_name="بازدیدکننده", role="visitor",
                        phone_verified=True),
        "admin": User(username="race_admin", full_name="همکار", role="admin", permissions=["crm"]),
    }

    async def build():
        async with eng.begin() as c:
            await c.run_sync(lambda sc: Base.metadata.create_all(sc, tables=[User.__table__, AuditEvent.__table__]))
        async with maker() as s:
            for u in people.values():
                u.hashed_password, u.is_active = _hash(RIGHT), True
            s.add_all(people.values())
            await s.commit()
    asyncio.run(build())

    server = fakeredis.FakeServer()

    async def get_redis():
        return fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    monkeypatch.setattr(database, "get_redis", get_redis)
    monkeypatch.setattr(verification, "get_redis", get_redis)
    monkeypatch.setattr(audit, "async_session_maker", maker)
    monkeypatch.setattr(get_settings(), "public_auth_enabled", True)

    api = FastAPI()
    api.include_router(api_router, prefix="/api")

    async def session():
        async with maker() as s:
            yield s
            await s.commit()
    api.dependency_overrides[database.get_db] = session

    def burst(method, path, n, who=None, **kw):
        """n identical requests in flight at once; their status codes."""
        headers = {"Authorization": f"Bearer {create_access_token(access_claims(people[who]))}"} if who else {}

        async def go():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as c:
                rs = await asyncio.gather(*(c.request(method, f"/api{path}", headers=headers, **kw)
                                            for _ in range(n)))
                return [r.status_code for r in rs]
        return asyncio.run(go())

    yield {"burst": burst, "cap": get_settings().auth_login_max_attempts}
    asyncio.run(eng.dispose())


def test_parallel_wrong_portal_logins_never_exceed_the_cap(office):
    cap, burst = office["cap"], office["burst"]
    codes = burst("POST", "/public/auth/login", cap + EXTRA, json={"identifier": PHONE, "password": "wrong"})
    assert codes.count(401) == cap and codes.count(429) == EXTRA, codes
    # the budget is the account's, whichever spelling is typed — and while it
    # lasts even the right password waits
    assert burst("POST", "/public/auth/login", 1, json={"identifier": EMAIL, "password": RIGHT}) == [429]


def test_a_name_nobody_has_runs_out_the_same_way(office):
    cap, burst = office["cap"], office["burst"]
    codes = burst("POST", "/public/auth/login", cap + EXTRA, json={"identifier": "09129999999", "password": "x"})
    assert codes.count(401) == cap and codes.count(429) == EXTRA, codes


def test_parallel_wrong_current_passwords_never_exceed_the_cap(office):
    cap, burst = office["cap"], office["burst"]
    codes = burst("POST", "/users/me/password", cap + EXTRA, who="admin",
                  json={"current_password": "wrong", "new_password": "a-new-password-1"})
    assert codes.count(400) == cap and codes.count(429) == EXTRA, codes
    assert burst("POST", "/users/me/password", 1, who="admin",
                 json={"current_password": RIGHT, "new_password": "a-new-password-1"}) == [429]


def test_the_right_password_still_works_under_the_cap(office):
    burst = office["burst"]
    assert burst("POST", "/public/auth/login", 3, json={"identifier": PHONE, "password": "wrong"}) == [401] * 3
    assert burst("POST", "/public/auth/login", 1, json={"identifier": PHONE, "password": RIGHT}) == [200]
    assert burst("POST", "/users/me/password", 1, who="admin",
                 json={"current_password": RIGHT, "new_password": "a-new-password-1"}) == [200]
