"""
Login hardening, tried the way an attacker would try it — and the way the
operator would get locked out if it were wrong.

  * users.totp_last_step: Alembic 0010 and the boot-time ALTER, on Postgres.
  * a TOTP code is accepted once — also when the same code arrives twice at
    the same moment.
  * failed logins are limited per account and per address, the right
    password is refused while locked, a burst of parallel guesses gets no
    more than the limit, a forged X-Forwarded-For neither dodges the address
    limit nor spends somebody else's, and an address that is not a real
    client's (production's 10.42.x.x) is never locked at all.
  * a name that does not exist costs one bcrypt round, like one that does.
  * production refuses to start on a published SECRET_KEY, or on the
    placeholder seed password where it would actually seed — and the live
    pod's configuration starts.
  * /me/email-2fa reads a typed body.
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

    # head, not "0010": later revisions stack on top, and a downgrade to 0009
    # runs each of their downgrades on the way — the path this test walks
    from alembic.script import ScriptDirectory
    head = ScriptDirectory.from_config(cfg).get_current_head()
    assert fresh == (("bigint", "YES"), head)
    assert down == (None, "0009")
    assert up == (("bigint", "YES"), head)
    assert booted == (("bigint", "YES"), head)


# ── the app, on Postgres, with a fake Redis ───────────────────────────────────

@pytest.fixture(scope="module")
def server():
    import fakeredis
    return fakeredis.FakeServer()


@pytest.fixture(scope="module")
def redis_view(server):
    """The app's fake Redis, read from this thread: a synchronous client on the
    same server, rather than the async one pinned to the app's event loop."""
    import fakeredis
    return fakeredis.FakeRedis(server=server, decode_responses=True)


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
                u = User(**{"username": username, "full_name": username, "role": "admin",
                            "hashed_password": get_password_hash(password),
                            "permissions": [], "is_active": True, **kw})
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


# ── failed logins: per account, per address ───────────────────────────────────

def _max():
    from app.config import get_settings
    return get_settings().auth_login_max_attempts


def test_an_account_locks_and_the_right_password_waits_out_the_window(client, redis_view):
    uid = _mk_user("lh_lock")
    for _ in range(_max()):
        assert _login(client, "lh_lock", "wrong-guess").status_code == 401

    r = _login(client, "lh_lock")                  # the right one, too early
    assert r.status_code == 429
    assert 0 < int(r.headers["Retry-After"]) <= 900
    assert "تلاش" in r.json()["detail"]
    # the account, not the spelling: its email would have been a second budget
    assert redis_view.get(f"sf:auth:login:uid:{uid}") == str(_max())

    redis_view.expire(f"sf:auth:login:uid:{uid}", 1)   # the window passes
    time.sleep(1.2)
    assert _login(client, "lh_lock").status_code == 200


@pytest.fixture
def real_redis(monkeypatch):
    """The Redis the suite runs against, for what the fake cannot show: its
    calls never yield to the event loop, so requests never interleave there."""
    import redis
    import redis.asyncio as aioredis
    import app.services.verification as v
    url = os.environ.get("REDIS_URL", "redis://localhost:6379/9")
    made = {}

    async def _get():
        # built on first use, inside the app's own event loop
        if "r" not in made:
            made["r"] = aioredis.from_url(url, decode_responses=True)
        return made["r"]
    monkeypatch.setattr(v, "get_redis", _get)
    yield redis.Redis.from_url(url, decode_responses=True)


def test_a_burst_of_parallel_guesses_gets_no_more_than_the_limit(client, real_redis):
    """Checked-then-recorded, every request in a burst reads «under the limit»
    before the first failure is written: thirty at once were thirty guesses."""
    uid = _mk_user("lh_burst")
    real_redis.delete(f"sf:auth:login:uid:{uid}")
    n = 3 * _max()
    gate = threading.Barrier(n)

    def guess(i):
        gate.wait()
        return _login(client, "lh_burst", f"guess-{i}").status_code

    with ThreadPoolExecutor(n) as pool:
        codes = list(pool.map(guess, range(n)))
    real_redis.delete(f"sf:auth:login:uid:{uid}")
    assert codes.count(401) == _max() and codes.count(429) == n - _max(), codes


def test_a_name_that_does_not_exist_locks_the_same_way(client):
    """Otherwise the 429 itself would say which names are real."""
    codes = [_login(client, "lh_nobody_at_all", "x").status_code for _ in range(_max() + 1)]
    assert codes == [401] * _max() + [429]


def test_typing_an_accounts_internal_key_does_not_spend_its_budget(client):
    """Accounts are counted under uid:<id>. A made-up name spelled the same
    must not land on that counter, or every account could be locked by
    walking the ids without knowing one username."""
    uid = _mk_user("lh_by_id")
    for _ in range(_max() + 1):
        _login(client, f"uid:{uid}", "x")
    assert _login(client, "lh_by_id").status_code == 200


def test_a_finished_login_clears_the_account_count(client):
    _mk_user("lh_clears")
    for _ in range(2):
        assert [_login(client, "lh_clears", "typo").status_code
                for _ in range(_max() - 1)] == [401] * (_max() - 1)
        assert _login(client, "lh_clears").status_code == 200


def test_totp_guesses_count_and_the_right_code_waits_too(client):
    """The password is not the only thing guessed: a five-minute session used
    to take any number of codes. The password step itself stays counted until
    the code is paid, so it is one of the ten."""
    totp = _totp_user("lh_totp_guess")
    half = _half(client, "lh_totp_guess")
    right = totp.now()
    wrong = f"{(int(right) + 1) % 1_000_000:06d}"
    for _ in range(_max() - 1):
        r = client.post("/api/users/token/verify-totp", json={"totp_session": half, "code": wrong})
        assert r.status_code == 401
    r = client.post("/api/users/token/verify-totp", json={"totp_session": half, "code": right})
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0


def test_one_address_spraying_many_names_is_stopped(client):
    from app.services.verification import LOGIN_IP_LIMIT
    _mk_user("lh_spray_target")
    for i in range(LOGIN_IP_LIMIT):
        assert _login(client, f"lh_spray_{i}", "Password1", xff="9.9.9.9").status_code == 401

    # a real account, the right password, from the same host: refused
    r = _login(client, "lh_spray_target", xff="9.9.9.9")
    assert r.status_code == 429
    assert 0 < int(r.headers["Retry-After"]) <= 900
    # the same account from anywhere else is untouched
    assert _login(client, "lh_spray_target", xff="149.154.167.99").status_code == 200


def test_an_office_behind_one_address_is_not_locked_by_logging_in(client):
    """A right password hands the address its attempt back: the address
    budget is for failures, and twenty people start work at nine."""
    from app.services.verification import LOGIN_IP_LIMIT
    _mk_user("lh_office_nat")
    for _ in range(LOGIN_IP_LIMIT + 5):
        assert _login(client, "lh_office_nat", xff="31.56.10.20").status_code == 200


def test_a_forged_forwarded_for_neither_dodges_nor_frames(client):
    """Traefik appends the peer it saw, so the rightmost entry is ours and the
    rest is whatever the caller typed. Rotating the typed part must not reset
    the budget, and typing the victim's address must not spend theirs."""
    from app.services.verification import LOGIN_IP_LIMIT
    victim, attacker = "185.143.232.10", "5.200.14.77"
    _mk_user("lh_victim")
    for i in range(LOGIN_IP_LIMIT):
        forged = f"{victim}, 1.2.3.{i}"                 # a fresh lie every time
        r = _login(client, f"lh_forge_{i}", "x", xff=f"{forged}, {attacker}")
        assert r.status_code == 401
    assert _login(client, "lh_forge_new", "x",
                  xff=f"8.8.8.8, {attacker}").status_code == 429
    assert _login(client, "lh_victim", xff=victim).status_code == 200


def test_an_address_that_is_not_a_clients_is_never_locked(client):
    """Production sees every request as 10.42.x.x (klipper-lb masquerades the
    client before Traefik). Locking that would lock the whole office."""
    from app.services.verification import LOGIN_IP_LIMIT
    _mk_user("lh_office")
    for i in range(LOGIN_IP_LIMIT + 5):
        _login(client, f"lh_cluster_{i}", "x", xff="10.42.0.7")
    assert _login(client, "lh_office", xff="10.42.0.7").status_code == 200


# ── a name that does not exist costs the same bcrypt round ────────────────────

def test_a_missing_name_still_costs_exactly_one_bcrypt_round(client, monkeypatch):
    import app.api.routes.users as users
    from app.auth.jwt import DUMMY_PASSWORD_HASH, get_password_hash, verify_password
    calls = []

    def counting(plain, hashed):
        calls.append(hashed)
        return verify_password(plain, hashed)
    monkeypatch.setattr(users, "verify_password", counting)

    assert _login(client, "lh_no_such_person", "whatever").status_code == 401
    assert calls == [DUMMY_PASSWORD_HASH]
    # and the dummy costs what a real hash costs
    assert DUMMY_PASSWORD_HASH[:7] == get_password_hash("x")[:7] == "$2b$12$"

    calls.clear()
    _mk_user("lh_timing_real")
    assert _login(client, "lh_timing_real", "whatever").status_code == 401
    assert len(calls) == 1 and calls[0] != DUMMY_PASSWORD_HASH


def test_an_inactive_account_is_checked_in_full_before_it_is_named(client, monkeypatch):
    import app.api.routes.users as users
    from app.auth.jwt import verify_password
    calls = []
    monkeypatch.setattr(users, "verify_password",
                        lambda p, h: calls.append(h) or verify_password(p, h))
    _mk_user("lh_inactive", is_active=False)
    assert _login(client, "lh_inactive", "whatever").status_code == 401
    assert _login(client, "lh_inactive").status_code == 403
    assert len(calls) == 2


def test_a_stolen_session_cannot_guess_the_password_at_totp_disable(client):
    """«change password» was throttled for exactly this; switching TOTP off,
    which asks the same password, was not."""
    totp = _totp_user("lh_totp_off")
    half = _half(client, "lh_totp_off")
    tok = client.post("/api/users/token/verify-totp",
                      json={"totp_session": half, "code": totp.now()}).json()["access_token"]
    auth = {"Authorization": f"Bearer {tok}"}
    off = lambda pw: client.post("/api/users/me/totp/disable", headers=auth, json={"password": pw})
    assert [off("guess").status_code for _ in range(_max())] == [400] * _max()
    r = off("pw123456")
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0


# ── /me/email-2fa takes a model, not a dict ───────────────────────────────────

def test_email_2fa_switch_reads_a_typed_body(client):
    _mk_user("lh_mail2fa", email="lh_mail2fa@example.com")
    _mk_user("lh_mail2fa_none")
    auth = {"Authorization": f"Bearer {_login(client, 'lh_mail2fa').json()['access_token']}"}
    post = lambda body, h=auth: client.post("/api/users/me/email-2fa", headers=h, json=body)

    r = post({"enabled": True})
    assert r.status_code == 200 and r.json()["enabled"] is True
    assert post({"enabled": False}).json()["enabled"] is False
    assert post({}).json()["enabled"] is False              # as bool(dict.get) read it
    assert post({"enabled": "not-a-switch"}).status_code == 422
    assert post(["enabled"]).status_code == 422

    no_mail = {"Authorization":
               f"Bearer {_login(client, 'lh_mail2fa_none').json()['access_token']}"}
    assert post({"enabled": True}, no_mail).status_code == 400


# ── production does not start on a published secret ──────────────────────────

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_production_with_the_default_secret_key_exits_nonzero():
    """The real process: uvicorn and the real lifespan, stopped before any
    database work."""
    import subprocess
    env = {**os.environ, "ENVIRONMENT": "production",
           "SECRET_KEY": "your-super-secret-key-change-in-production"}
    p = subprocess.run([sys.executable, "-m", "uvicorn", "app.main:app", "--port", "0"],
                       cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
    out = p.stdout + p.stderr
    assert p.returncode != 0, out[-2000:]
    assert "Refusing to start in production: SECRET_KEY" in out


@pytest.mark.parametrize("key", [
    "your-super-secret-key-change-in-production-with-random-string",
    "replace-with-a-long-random-value",
    "ci-only-not-a-real-secret-0123456789",
    "0123456789abcdef0123456789abcdef",
    "test-secret-key-0123456789abcdef",
    "ai-eval-harness-fake-secret-0123456789",
    "a-31-character-key-is-too-shor",
    "",
])
def test_production_refuses_a_published_or_short_secret_key(monkeypatch, key):
    import app.main as m
    monkeypatch.setattr(m.settings, "environment", "production")
    monkeypatch.setattr(m.settings, "secret_key", key)
    monkeypatch.setattr(m.settings, "super_admin_password", "set-for-real-1234")
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        asyncio.run(m._refuse_default_secrets())


def test_every_secret_key_printed_in_the_repository_is_refused():
    """Workflows, docs, scripts and tests all print a SECRET_KEY somewhere.
    Any of them long enough to pass the length check must be on the refused
    list, or a copy-pasted command line boots production on a public key."""
    import re
    import app.main as m
    # KEY=value, KEY: value, "KEY": "value", ("KEY", "value"), environ["KEY"] = "value", ${KEY:-value}
    printed = re.compile(r"""SECRET_KEY["']?\]?\s*(?::-|[:=,])\s*["']?([^\s"'$)}]{32,})""")
    skip_dirs = {".git", "node_modules", "vendor", "venv", ".venv", "__pycache__", "graphify-out"}
    found = {}
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for name in files:
            if not name.endswith((".py", ".md", ".yml", ".yaml", ".sh", ".example", ".toml", ".ini",
                                  ".txt", ".cfg", ".json")) and not name.startswith((".env", "Dockerfile")):
                continue
            path = os.path.join(base, name)
            try:
                text = open(path, encoding="utf-8").read()
            except (UnicodeDecodeError, OSError):
                continue
            for key in printed.findall(text):
                found.setdefault(key, os.path.relpath(path, ROOT))
    assert found, "the scan found nothing — the pattern is broken"
    missing = {k: where for k, where in found.items() if k not in m._PUBLISHED_SECRET_KEYS}
    assert not missing, f"published SECRET_KEY values production would still accept: {missing}"


def test_the_live_pods_config_starts(client, monkeypatch):
    """What production has (k8s/04-backend.yaml and the Secret): ENVIRONMENT
    production, a 64-character SECRET_KEY, no SUPER_ADMIN_PASSWORD at all —
    so the placeholder — and a users table with rows. It must boot."""
    import secrets
    import app.main as m
    monkeypatch.setattr(m.settings, "environment", "production")
    monkeypatch.setattr(m.settings, "secret_key", secrets.token_hex(32))
    monkeypatch.setattr(m.settings, "super_admin_password", "CHANGE_ME")
    asyncio.run(m._refuse_default_secrets())


def test_the_placeholder_is_refused_only_where_it_would_seed(client, monkeypatch):
    """An empty database is where _seed_super_admin writes the placeholder in
    as a real password. Its own schema, so the table really is missing."""
    import secrets
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    import app.database as db
    import app.main as m

    async def _schema(*sql):
        eng = create_async_engine(os.environ["DATABASE_URL"])
        async with eng.begin() as c:
            for s in sql:
                await c.execute(text(s))
        await eng.dispose()

    asyncio.run(_schema("DROP SCHEMA IF EXISTS sf_no_users CASCADE", "CREATE SCHEMA sf_no_users"))
    monkeypatch.setattr(db, "engine", create_async_engine(
        os.environ["DATABASE_URL"],
        connect_args={"server_settings": {"search_path": "sf_no_users"}}))
    monkeypatch.setattr(m.settings, "environment", "production")
    monkeypatch.setattr(m.settings, "secret_key", secrets.token_hex(32))
    try:
        monkeypatch.setattr(m.settings, "super_admin_password", "CHANGE_ME")
        with pytest.raises(RuntimeError, match="SUPER_ADMIN_PASSWORD"):
            asyncio.run(m._refuse_default_secrets())
        monkeypatch.setattr(m.settings, "super_admin_password", "set-for-real-1234")
        asyncio.run(m._refuse_default_secrets())
        # and outside production the placeholder and the default key still boot
        monkeypatch.setattr(m.settings, "environment", "development")
        monkeypatch.setattr(m.settings, "super_admin_password", "CHANGE_ME")
        monkeypatch.setattr(m.settings, "secret_key", "your-super-secret-key-change-in-production")
        asyncio.run(m._refuse_default_secrets())
    finally:
        asyncio.run(db.engine.dispose())
        asyncio.run(_schema("DROP SCHEMA IF EXISTS sf_no_users CASCADE"))


def test_without_redis_login_still_works(client, monkeypatch):
    """Fail open: a Redis blip must not lock the office out of the panel.
    /ready reports Redis but no longer gates on it either (app/main.py) —
    the two together mean a Redis outage degrades rate limiting, not the
    whole API."""
    import app.services.verification as v

    async def _down():
        raise ConnectionError("redis is down")
    monkeypatch.setattr(v, "get_redis", _down)
    _mk_user("lh_no_redis")
    assert _login(client, "lh_no_redis", "wrong", xff="9.9.9.10").status_code == 401
    assert _login(client, "lh_no_redis", xff="9.9.9.10").status_code == 200


# ── what was typed never passes for an account, nor grows a key ──────────────

def _portal(client, identifier, password="x"):
    return client.post("/api/public/auth/login", json={"identifier": identifier, "password": password})


def test_the_portal_login_cannot_spend_a_staff_accounts_budget(client, monkeypatch):
    """The portal counted what was typed verbatim: «uid:1» there spent staff
    account 1's budget — every account lockable by walking the ids."""
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "public_auth_enabled", True)
    uid = _mk_user("lh_portal_by_id")
    codes = [_portal(client, f"uid:{uid}").status_code for _ in range(_max() + 1)]
    assert codes[0] == 401 and set(codes) <= {401, 429}, codes     # the portal is live
    assert _login(client, "lh_portal_by_id").status_code == 200


def test_a_huge_name_leaves_a_small_key(client, redis_view):
    """A megabyte username was a megabyte key; a couple of hundred filled
    Redis and every limiter failed open."""
    before = set(redis_view.keys("sf:auth:login:*"))
    _login(client, "x" * 200_000, "y")
    new = set(redis_view.keys("sf:auth:login:*")) - before
    assert new and max(len(k) for k in new) < 100


def test_the_portal_login_costs_one_bcrypt_round_for_anyone(client, monkeypatch):
    """It skipped bcrypt for a phone nobody has, so timing said who has one."""
    import app.api.routes.public_auth as pa
    from app.auth.jwt import DUMMY_PASSWORD_HASH, verify_password
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "public_auth_enabled", True)
    calls = []

    def counting(plain, hashed):
        calls.append(hashed)
        return verify_password(plain, hashed)
    monkeypatch.setattr(pa, "verify_password", counting)
    assert _portal(client, "09129999991").status_code == 401
    assert calls == [DUMMY_PASSWORD_HASH]
