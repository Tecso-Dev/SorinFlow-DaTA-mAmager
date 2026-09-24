"""
Login hardening, tried the way an attacker would try it — and the way the
operator would get locked out if it were wrong.

  * users.totp_last_step: Alembic 0010 and the boot-time ALTER, on Postgres.
  * a TOTP code is accepted once — also when the same code arrives twice at
    the same moment.
"""
import asyncio
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_login_hardening.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

PG_URL = os.environ.get("PG_TEST_URL")


# ── users.totp_last_step: 0010 and the boot ALTER ─────────────────────────────

@pytest.mark.skipif(not PG_URL, reason="set PG_TEST_URL to run the Postgres migration test")
def test_0010_upgrades_downgrades_and_the_boot_builds_the_column_anyway():
    """Its own schema, like test_pg_migration's Alembic test, because that
    file's other tests wreck the public one on purpose."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    import app.database as db

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = Config(os.path.join(root, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(root, "migrations"))

    saved_engine, saved_maker = db.engine, db.async_session_maker
    db.engine = create_async_engine(
        PG_URL, connect_args={"server_settings": {"search_path": "sf_0010"}})
    db.async_session_maker = async_sessionmaker(
        db.engine, expire_on_commit=False, autocommit=False, autoflush=False)

    async def _state():
        async with db.engine.begin() as c:
            col = (await c.execute(text(
                "SELECT data_type, is_nullable FROM information_schema.columns "
                "WHERE table_name='users' AND column_name='totp_last_step' "
                "AND table_schema='sf_0010'"))).first()
            ver = (await c.execute(text("SELECT version_num FROM alembic_version"))).scalar()
        return (tuple(col) if col else None), ver

    async def _alembic(fn):
        async with db.engine.begin() as c:
            await c.run_sync(lambda sc: (cfg.attributes.__setitem__("connection", sc), fn()))

    async def _go():
        async with db.engine.begin() as c:
            await c.execute(text("DROP SCHEMA IF EXISTS sf_0010 CASCADE"))
            await c.execute(text("CREATE SCHEMA sf_0010"))
        await db.init_db()                         # fresh: create_all, stamp head
        fresh = await _state()
        await _alembic(lambda: command.downgrade(cfg, "0009"))
        down = await _state()
        await _alembic(lambda: command.upgrade(cfg, "head"))
        up = await _state()
        # Alembic believes 0010 is applied and the column is not there — what a
        # failed or clashing revision leaves. Only the boot ALTER can fix it.
        async with db.engine.begin() as c:
            await c.execute(text("ALTER TABLE users DROP COLUMN totp_last_step"))
        await db.init_db()
        booted = await _state()
        await _alembic(lambda: command.check(cfg))  # raises on model/schema drift
        async with db.engine.begin() as c:
            await c.execute(text("DROP SCHEMA sf_0010 CASCADE"))
        return fresh, down, up, booted

    try:
        fresh, down, up, booted = asyncio.run(_go())
    finally:
        asyncio.run(db.engine.dispose())
        db.engine, db.async_session_maker = saved_engine, saved_maker

    assert fresh == (("bigint", "YES"), "0010")
    assert down == (None, "0009")
    assert up == (("bigint", "YES"), "0010")
    assert booted == (("bigint", "YES"), "0010")


# ── the app, on Postgres, with a fake Redis ───────────────────────────────────

@pytest.fixture(scope="module")
def fake():
    import fakeredis.aioredis
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture(scope="module")
def client(fake):
    import app.database as db
    import app.services.verification as v
    from app.config import get_settings
    if not str(db.engine.url).startswith("postgresql"):
        pytest.skip("needs Postgres — see test_auth_roles.py", allow_module_level=True)
    cfg = get_settings()
    saved = (cfg.environment, cfg.api_key, cfg.cookies_path, cfg.scrape_scheduler,
             cfg.match_engine, db.get_redis, v.get_redis)
    cfg.environment, cfg.api_key = "test", ""
    cfg.cookies_path = "/tmp/sorinflow-test-cookies"
    cfg.scrape_scheduler = False
    cfg.match_engine = False

    async def _get_redis():
        return fake
    db.get_redis = v.get_redis = _get_redis
    from fastapi.testclient import TestClient
    import app.main as m
    with TestClient(m.app) as c:
        yield c
    (cfg.environment, cfg.api_key, cfg.cookies_path, cfg.scrape_scheduler,
     cfg.match_engine, db.get_redis, v.get_redis) = saved


def _mk_user(username, password="pw123456", **kw):
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.models.user import User
    from app.auth.jwt import get_password_hash

    async def _go():
        eng = create_async_engine(os.environ["DATABASE_URL"])
        try:
            async with async_sessionmaker(eng, expire_on_commit=False)() as s:
                u = User(username=username, full_name=username, role="admin",
                         hashed_password=get_password_hash(password),
                         permissions=[], is_active=True, **kw)
                s.add(u)
                await s.commit()
                return u.id
        finally:
            await eng.dispose()
    return asyncio.run(_go())


def _login(client, username, password="pw123456", xff=None):
    headers = {"X-Forwarded-For": xff} if xff else {}
    return client.post("/api/users/token", headers=headers,
                       data={"username": username, "password": password})


# ── a TOTP code is accepted once ──────────────────────────────────────────────

def _totp_user(name):
    import pyotp
    secret = pyotp.random_base32()
    _mk_user(name, totp_secret=secret, totp_enabled=True)
    return pyotp.TOTP(secret)


def _half(client, name):
    r = _login(client, name)
    assert r.status_code == 200 and r.json()["requires_totp"], r.text
    return r.json()["totp_session"]


def test_the_same_totp_code_logs_in_once(client):
    totp = _totp_user("lh_totp_once")
    code = totp.now()
    first = client.post("/api/users/token/verify-totp",
                        json={"totp_session": _half(client, "lh_totp_once"), "code": code})
    assert first.status_code == 200 and first.json()["access_token"], first.text

    # the password again, the same six digits again: someone who read them
    # over a shoulder, inside the minute and a half pyotp still honours them
    again = client.post("/api/users/token/verify-totp",
                        json={"totp_session": _half(client, "lh_totp_once"), "code": code})
    assert again.status_code == 401
    assert "قبلاً استفاده شده" in again.json()["detail"]

    # the next code is still good — a phone clock one step ahead included
    nxt = totp.at(int(time.time()) + totp.interval)
    ok = client.post("/api/users/token/verify-totp",
                     json={"totp_session": _half(client, "lh_totp_once"), "code": nxt})
    assert ok.status_code == 200, ok.text


def test_two_submissions_of_one_code_at_the_same_moment_let_one_in(client):
    """A double-click, or an attacker racing the owner: exactly one wins."""
    totp = _totp_user("lh_totp_race")
    code = totp.now()
    sessions = [_half(client, "lh_totp_race") for _ in range(2)]
    gate = threading.Barrier(2)

    def submit(session):
        gate.wait()
        return client.post("/api/users/token/verify-totp",
                           json={"totp_session": session, "code": code}).status_code

    with ThreadPoolExecutor(2) as pool:
        codes = sorted(pool.map(submit, sessions))
    assert codes == [200, 401]


def test_the_code_that_switched_totp_on_does_not_also_finish_a_login(client):
    import pyotp
    _mk_user("lh_totp_enable")
    tok = _login(client, "lh_totp_enable").json()["access_token"]
    auth = {"Authorization": f"Bearer {tok}"}
    secret = client.post("/api/users/me/totp/setup", headers=auth).json()["secret"]
    code = pyotp.TOTP(secret).now()
    assert client.post("/api/users/me/totp/enable", headers=auth,
                       json={"code": code}).status_code == 200

    r = client.post("/api/users/token/verify-totp",
                    json={"totp_session": _half(client, "lh_totp_enable"), "code": code})
    assert r.status_code == 401
