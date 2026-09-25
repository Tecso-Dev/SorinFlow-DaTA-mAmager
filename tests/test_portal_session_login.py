"""
POST /api/public/auth/session/login — the portal's own login, mirroring
app/api/routes/session.py for staff: on success the token goes into the same
httpOnly cookie set_session() uses for the panel, never into the response
body. A staff account is refused (that role belongs on /api/session/login,
which has the TOTP step this route does not), and an unverified visitor gets
the same fresh-code response the bearer /login already returns — no cookie
before the account is proven.

Through the real ASGI app, on Postgres, with a fake Redis — the pattern
tests/test_login_hardening.py's `client` fixture already uses.
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_portal_session_login.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

PW = "right-password-1"


@pytest.fixture(scope="module")
def server():
    import fakeredis
    return fakeredis.FakeServer()


@pytest.fixture(scope="module")
def client(server):
    import fakeredis.aioredis
    fake = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    import app.database as db
    import app.services.verification as v
    from app.config import get_settings
    if not str(db.engine.url).startswith("postgresql"):
        pytest.skip("needs Postgres — see test_auth_roles.py", allow_module_level=True)
    cfg = get_settings()
    saved = (cfg.environment, cfg.api_key, cfg.public_auth_enabled, cfg.scrape_scheduler,
             cfg.match_engine, cfg.auth_sms_provider, db.get_redis, v.get_redis)
    cfg.environment, cfg.api_key = "test", ""
    cfg.public_auth_enabled = True
    cfg.scrape_scheduler = False
    cfg.match_engine = False
    # so issue_code (the unverified-account "here's a fresh code" path) has a
    # channel that succeeds with no real Kavenegar credentials in the suite
    cfg.auth_sms_provider = "console"

    async def _get_redis():
        return fake
    db.get_redis = v.get_redis = _get_redis
    from fastapi.testclient import TestClient
    import app.main as m
    with TestClient(m.app) as c:
        yield c
    (cfg.environment, cfg.api_key, cfg.public_auth_enabled, cfg.scrape_scheduler,
     cfg.match_engine, cfg.auth_sms_provider, db.get_redis, v.get_redis) = saved


def _mk_user(phone, password=PW, **kw):
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.models.user import User
    from app.auth.jwt import get_password_hash

    async def _go():
        eng = create_async_engine(os.environ["DATABASE_URL"])
        try:
            async with async_sessionmaker(eng, expire_on_commit=False)() as s:
                u = User(**{
                    "username": phone, "full_name": "بازدیدکننده", "role": "visitor",
                    "phone": phone, "hashed_password": get_password_hash(password),
                    "permissions": [], "is_active": True, "phone_verified": True,
                    **kw,
                })
                s.add(u)
                await s.commit()
                return u.id
        finally:
            await eng.dispose()
    return asyncio.run(_go())


def _login(client, identifier, password=PW):
    return client.post("/api/public/auth/session/login",
                       json={"identifier": identifier, "password": password})


class TestASuccessfulVisitorLoginSetsTheCookie:

    def test_the_cookie_and_csrf_cookie_are_set_and_no_token_in_the_body(self, client):
        phone = "09121110001"
        _mk_user(phone)
        r = _login(client, phone)
        assert r.status_code == 200, r.text
        body = r.json()
        assert "access_token" not in r.text
        assert body["user"]["phone"] == phone
        assert body["user"]["role"] == "visitor"
        cookies = r.headers.get_list("set-cookie")
        session = next(h for h in cookies if h.startswith("sf_session="))
        csrf = next(h for h in cookies if h.startswith("sf_csrf="))
        assert "HttpOnly" in session and "HttpOnly" not in csrf
        assert body["csrf_token"] == client.cookies.get("sf_csrf")

        # the cookie actually works against the shared session-read route
        me = client.get("/api/session")
        assert me.status_code == 200 and me.json()["user"]["phone"] == phone
        # and against a real visitor route, over the same cookie
        mine = client.get("/api/portal/me")
        assert mine.status_code == 200 and mine.json()["phone"] == phone
        client.cookies.clear()

    def test_remember_keeps_the_cookie_for_the_token_lifetime(self, client):
        phone = "09121110005"
        _mk_user(phone)
        r = client.post("/api/public/auth/session/login",
                        json={"identifier": phone, "password": PW, "remember": True})
        assert r.status_code == 200, r.text
        session = next(h for h in r.headers.get_list("set-cookie") if h.startswith("sf_session="))
        assert "Max-Age=" in session
        client.cookies.clear()

    def test_email_identifier_works_too(self, client):
        phone = "09121110002"
        _mk_user(phone, email="visitor2@example.com")
        r = _login(client, "visitor2@example.com")
        assert r.status_code == 200, r.text
        client.cookies.clear()


class TestAStaffAccountIsRefused:

    def test_a_staff_account_gets_no_cookie_and_a_403(self, client):
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        from app.models.user import User
        from app.auth.jwt import get_password_hash

        phone = "09121110009"

        async def _go():
            eng = create_async_engine(os.environ["DATABASE_URL"])
            try:
                async with async_sessionmaker(eng, expire_on_commit=False)() as s:
                    s.add(User(username="staffer_psl", full_name="مدیر", role="admin",
                               permissions=["crm"], phone=phone,
                               hashed_password=get_password_hash(PW), is_active=True))
                    await s.commit()
            finally:
                await eng.dispose()
        asyncio.run(_go())

        # matched on phone, exactly like the visitor rows above — a staff
        # account's username is never looked at here (see _authenticate_visitor)
        r = _login(client, phone)
        assert r.status_code == 403, r.text
        assert not r.headers.get_list("set-cookie")
        assert client.get("/api/session").status_code == 401


class TestAnUnverifiedVisitorStillGetsAFreshCode:

    def test_the_same_pending_response_the_bearer_login_gives_no_cookie_set(self, client):
        phone = "09121110003"
        _mk_user(phone, phone_verified=False, email_verified=False)
        r = _login(client, phone)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["pending"] is True
        assert body["phone"] == phone
        assert not r.headers.get_list("set-cookie")
        assert client.get("/api/session").status_code == 401


class TestWrongPasswordSetsNothing:

    def test_wrong_password_is_401_with_no_cookie(self, client):
        phone = "09121110004"
        _mk_user(phone)
        r = _login(client, phone, password="totally-wrong")
        assert r.status_code == 401
        assert not r.headers.get_list("set-cookie")
