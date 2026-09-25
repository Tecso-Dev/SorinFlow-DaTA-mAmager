"""
The new panel's login: the token lives in an httpOnly cookie, a request
authenticated by that cookie must prove with the CSRF header that it came from
our own page, and the old panel's bearer token keeps working untouched.

Through the real ASGI app, on a sqlite database and a fake Redis of this file's
own.
"""
import asyncio
import os
import sys

import bcrypt
import fakeredis
import fakeredis.aioredis
import httpx
import pyotp
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_session_cookie.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.database import Base  # noqa: E402
from app.models.audit_event import AuditEvent  # noqa: E402
from app.models.user import User  # noqa: E402

PW = "right-password-1"
TOTP_SECRET = pyotp.random_base32()


def _hash(pw):
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt(4)).decode()


@pytest.fixture
def office(tmp_path, monkeypatch):
    from fastapi import FastAPI
    import app.database as database
    import app.services.audit as audit
    import app.services.verification as verification
    from app.api.routes import router as api_router

    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/session.db", poolclass=NullPool)
    maker = async_sessionmaker(eng, expire_on_commit=False)

    async def build():
        async with eng.begin() as c:
            await c.run_sync(lambda sc: Base.metadata.create_all(sc, tables=[User.__table__, AuditEvent.__table__]))
        async with maker() as s:
            h = _hash(PW)
            s.add_all([
                User(username="boss", full_name="مدیر", role="super_admin", hashed_password=h),
                User(username="agent", full_name="مشاور", role="admin", permissions=["crm"], hashed_password=h),
                User(username="guarded", role="admin", permissions=["crm"], hashed_password=h,
                     totp_enabled=True, totp_secret=TOTP_SECRET),
                User(username="customer", role="visitor", hashed_password=h),
            ])
            await s.commit()
    asyncio.run(build())

    server = fakeredis.FakeServer()

    async def get_redis():
        return fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    monkeypatch.setattr(database, "get_redis", get_redis)
    monkeypatch.setattr(verification, "get_redis", get_redis)
    monkeypatch.setattr(audit, "async_session_maker", maker)

    api = FastAPI()
    api.include_router(api_router, prefix="/api")

    async def session():
        async with maker() as s:
            yield s
            await s.commit()
    api.dependency_overrides[database.get_db] = session

    def run(steps, base="http://testserver"):
        async def go():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url=base) as c:
                return await steps(c)
        return asyncio.run(go())

    yield run
    asyncio.run(eng.dispose())


def test_login_sets_an_httponly_session_and_never_returns_the_token(office):
    async def steps(c):
        r = await c.post("/api/session/login", json={"username": "agent", "password": PW})
        assert r.status_code == 200, r.text
        body = r.json()
        assert "access_token" not in r.text
        assert body["user"]["username"] == "agent"
        assert body["user"]["permissions"] == ["crm"]
        cookies = r.headers.get_list("set-cookie")
        session = next(h for h in cookies if h.startswith("sf_session="))
        csrf = next(h for h in cookies if h.startswith("sf_csrf="))
        assert "HttpOnly" in session and "HttpOnly" not in csrf
        assert "samesite=lax" in session.lower()
        assert "Max-Age" not in session, "without «remember me» it must be a browser-session cookie"
        assert body["csrf_token"] == c.cookies["sf_csrf"]

        me = await c.get("/api/session")
        assert me.status_code == 200 and me.json()["user"]["username"] == "agent"
        assert (await c.get("/api/users/me")).json()["username"] == "agent"
    office(steps)


def test_remember_me_keeps_the_cookie_for_the_token_lifetime(office):
    async def steps(c):
        r = await c.post("/api/session/login", json={"username": "agent", "password": PW, "remember": True})
        session = next(h for h in r.headers.get_list("set-cookie") if h.startswith("sf_session="))
        assert "Max-Age=86400" in session
    office(steps)


def test_a_cookie_request_that_changes_state_needs_the_csrf_header(office):
    async def steps(c):
        r = await c.post("/api/session/login", json={"username": "agent", "password": PW})
        csrf = r.json()["csrf_token"]
        body = {"current_password": PW, "new_password": "another-password-9"}
        # a page on another origin rides the cookie but cannot read the token
        forged = await c.post("/api/users/me/password", json=body)
        assert forged.status_code == 403 and forged.json()["detail"]["code"] == "csrf"
        wrong = await c.post("/api/users/me/password", json=body, headers={"X-CSRF-Token": "0" * 64})
        assert wrong.status_code == 403
        ok = await c.patch("/api/users/me", json={"presence": "busy"}, headers={"X-CSRF-Token": csrf})
        assert ok.status_code == 200, ok.text
        assert ok.json()["user"]["presence"] == "busy"
        # reading needs no header
        assert (await c.get("/api/users/me")).status_code == 200
    office(steps)


def test_the_bearer_token_of_the_old_panel_still_works_without_csrf(office):
    async def steps(c):
        r = await c.post("/api/users/token", data={"username": "agent", "password": PW})
        token = r.json()["access_token"]
        c.cookies.clear()
        me = await c.get("/api/users/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200
        put = await c.patch("/api/users/me", json={"presence": "away"},
                            headers={"Authorization": f"Bearer {token}"})
        assert put.status_code == 200, put.text
    office(steps)


def test_a_portal_visitor_gets_no_panel_session(office):
    """A customer's portal account knows its own password; that must not buy it
    a panel cookie, even one every staff API would refuse."""
    async def steps(c):
        r = await c.post("/api/session/login", json={"username": "customer", "password": PW})
        assert r.status_code == 403, r.text
        assert not r.headers.get_list("set-cookie")
        assert (await c.get("/api/session")).status_code == 401
    office(steps)


def test_wrong_password_sets_nothing(office):
    async def steps(c):
        r = await c.post("/api/session/login", json={"username": "agent", "password": "nope"})
        assert r.status_code == 401
        assert not r.headers.get_list("set-cookie")
        assert (await c.get("/api/session")).status_code == 401
    office(steps)


def test_totp_step_returns_only_the_pending_session_then_the_cookie(office):
    async def steps(c):
        r = await c.post("/api/session/login", json={"username": "guarded", "password": PW})
        body = r.json()
        assert body == {"requires_totp": True, "totp_session": body["totp_session"]}
        assert not r.headers.get_list("set-cookie")
        # the pending session is not a login
        pending = await c.get("/api/users/me", headers={"Authorization": f"Bearer {body['totp_session']}"})
        assert pending.status_code == 401
        done = await c.post("/api/session/verify-totp", json={
            "totp_session": body["totp_session"], "code": pyotp.TOTP(TOTP_SECRET).now()})
        assert done.status_code == 200, done.text
        assert done.json()["user"]["username"] == "guarded"
        assert (await c.get("/api/session")).status_code == 200
    office(steps)


def test_logout_clears_the_cookies(office):
    async def steps(c):
        await c.post("/api/session/login", json={"username": "agent", "password": PW})
        out = await c.post("/api/session/logout")
        assert out.status_code == 200
        assert not c.cookies.get("sf_session")
        assert (await c.get("/api/session")).status_code == 401
    office(steps)


def test_over_https_the_cookies_are_host_prefixed_and_secure(office):
    async def steps(c):
        r = await c.post("/api/session/login", json={"username": "boss", "password": PW})
        cookies = r.headers.get_list("set-cookie")
        session = next(h for h in cookies if h.startswith("__Host-sf_session="))
        assert "Secure" in session and "Path=/" in session and "Domain" not in session
        assert (await c.get("/api/session")).json()["user"]["role"] == "super_admin"
        # a plain-named cookie planted by a sibling is not read over https
        c.cookies.clear()
        c.cookies.set("sf_session", r.cookies.get("__Host-sf_session") or "x")
        assert (await c.get("/api/session")).status_code == 401
    office(steps, base="https://testserver")


def test_a_password_change_moves_the_fresh_token_into_the_cookie_not_the_body(office):
    async def steps(c):
        r = await c.post("/api/session/login", json={"username": "agent", "password": PW, "remember": True})
        old = c.cookies["sf_session"]
        changed = await c.post("/api/users/me/password",
                               json={"current_password": PW, "new_password": "another-password-9"},
                               headers={"X-CSRF-Token": r.json()["csrf_token"]})
        assert changed.status_code == 200, changed.text
        assert changed.json()["access_token"] is None
        fresh = next(h for h in changed.headers.get_list("set-cookie") if h.startswith("sf_session="))
        assert "Max-Age=86400" in fresh, "«remember me» must survive the reissue"
        assert c.cookies["sf_session"] != old
        assert (await c.get("/api/session")).status_code == 200
        # the old cookie is somebody else's device now, and is signed out
        c.cookies.set("sf_session", old)
        assert (await c.get("/api/session")).status_code == 401
    office(steps)


def test_a_rename_keeps_the_cookie_session_alive(office):
    async def steps(c):
        r = await c.post("/api/session/login", json={"username": "agent", "password": PW})
        renamed = await c.patch("/api/users/me", json={"username": "agent.two"},
                                headers={"X-CSRF-Token": r.json()["csrf_token"]})
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["access_token"] is None
        assert (await c.get("/api/session")).json()["user"]["username"] == "agent.two"
    office(steps)
