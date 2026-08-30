"""
Auth rework — the properties that must hold, exercised against the real app.

These are written as attacks rather than as feature demos: the interesting
question is not "can a super_admin approve a ticket" but "can anyone who should
not, do it anyway".
"""
import os
import sys
import asyncio
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Only the values that must be right when Settings is first constructed go in
# the environment — the engine is built at import time from database_url.
# Everything else is set on the settings singleton inside the fixture, because
# get_settings() is lru_cached: whichever test module imports app.config first
# decides the values for the whole session, and mutating os.environ here would
# leak "ENVIRONMENT=test" into tests that assert on the real defaults.
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_auth.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module")
def client():
    import fakeredis.aioredis
    import app.database as db
    from app.config import get_settings

    # These tests build the whole schema, and scraping_jobs.job_id is a
    # postgresql.UUID column the pinned SQLAlchemy cannot render on SQLite.
    # Without this the run is twenty pages of UnsupportedCompilationError with
    # nothing saying what to do about it; one skip line says it plainly.
    if not str(db.engine.url).startswith("postgresql"):
        pytest.skip(
            "needs Postgres (scraping_jobs.job_id is a postgresql UUID column) — "
            "run with DATABASE_URL=postgresql+asyncpg://user@host/db",
            allow_module_level=True)

    # One cached Settings instance is shared by every module that did
    # `settings = get_settings()` at import, so overriding it here reaches all
    # of them. Restored afterwards so the rest of the suite sees the real
    # defaults.
    cfg = get_settings()
    saved = (cfg.public_auth_enabled, cfg.environment, cfg.api_key)
    cfg.public_auth_enabled = True
    cfg.environment = "test"      # lets the register response echo debug_code
    cfg.api_key = ""              # otherwise the api-key middleware answers first

    # The verification service is the only thing that needs Redis; a fake keeps
    # the test hermetic without weakening what it proves (TTL, counters and
    # single-use all behave the same).
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

    (cfg.public_auth_enabled, cfg.environment, cfg.api_key) = saved


def _db_url():
    return os.environ["DATABASE_URL"]


def _in_fresh_loop(coro_factory):
    """Run a coroutine on its own loop with its own engine.

    asyncio.run() creates a new event loop every call. Handing it the app's
    shared engine works under aiosqlite but asyncpg refuses outright — its
    connection pool is pinned to the loop that made it. Each helper therefore
    builds and disposes an engine inside the loop that uses it.
    """
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

    async def _go():
        eng = create_async_engine(_db_url())
        maker = async_sessionmaker(eng, expire_on_commit=False)
        try:
            return await coro_factory(maker)
        finally:
            await eng.dispose()
    return asyncio.run(_go())


def _mk_user(username, role, permissions=None, password="pw123456", **kw):
    """Insert a user straight into the DB and return its id."""
    from app.models.user import User
    from app.auth.jwt import get_password_hash

    async def _go(maker):
        async with maker() as s:
            u = User(username=username, full_name=username, role=role,
                     hashed_password=get_password_hash(password),
                     permissions=permissions or [], is_active=True, **kw)
            s.add(u)
            await s.commit()
            await s.refresh(u)
            return u.id
    return _in_fresh_loop(_go)


def _token(client, username, password="pw123456"):
    r = client.post("/api/users/token",
                    data={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _auth(tok):
    return {"Authorization": f"Bearer {tok}"}


# ── the critical one ──────────────────────────────────────────────────────────

def test_totp_pending_token_is_not_an_access_token(client):
    """The audit's critical finding: the half-authenticated token handed out
    between password and TOTP used to satisfy get_current_user, so 2FA could be
    skipped entirely by anyone holding just the password."""
    import pyotp
    from app.database import async_session_maker
    from app.models.user import User
    from sqlalchemy import select

    secret = pyotp.random_base32()
    _mk_user("totpuser", "super_admin", totp_secret=secret, totp_enabled=True)

    r = client.post("/api/users/token",
                    data={"username": "totpuser", "password": "pw123456"})
    assert r.status_code == 200
    body = r.json()
    assert body["requires_totp"] is True
    half = body["totp_session"]
    assert half and not body.get("access_token")

    # The whole point: this credential must open nothing.
    me = client.get("/api/users/me", headers=_auth(half))
    assert me.status_code == 401, "totp_pending token was accepted as a full login"

    # And it must not reach a privileged route either.
    users = client.get("/api/users", headers=_auth(half))
    assert users.status_code == 401

    # Finishing the second factor does produce a working token.
    code = pyotp.TOTP(secret).now()
    r2 = client.post("/api/users/token/verify-totp",
                     json={"totp_session": half, "code": code})
    assert r2.status_code == 200, r2.text
    good = r2.json()["access_token"]
    assert client.get("/api/users/me", headers=_auth(good)).status_code == 200


# ── visitors must not reach the dashboard ─────────────────────────────────────

@pytest.fixture(scope="module")
def visitor_token(client):
    _mk_user("vis1", "visitor", phone="09120000001", phone_verified=True)
    return _token(client, "vis1")


@pytest.mark.parametrize("path", [
    "/api/properties", "/api/crm/leads", "/api/scraper/jobs",
    "/api/proxies", "/api/stats/dashboard", "/api/auth/cookies",
])
def test_visitor_cannot_reach_dashboard_routers(client, visitor_token, path):
    """A public sign-up holds a valid token. Before this change 'valid token'
    was the only gate on these routers."""
    tok = visitor_token
    r = client.get(path, headers=_auth(tok))
    assert r.status_code == 403, f"{path} let a visitor in ({r.status_code})"


def test_admin_without_permission_is_refused_but_with_it_is_allowed(client):
    _mk_user("adm_crm", "admin", permissions=["crm"])
    tok = _token(client, "adm_crm")
    assert client.get("/api/crm/leads", headers=_auth(tok)).status_code == 200
    # not granted 'proxies'
    assert client.get("/api/proxies", headers=_auth(tok)).status_code == 403


def test_super_admin_bypasses_permission_list(client):
    _mk_user("sa1", "super_admin", permissions=[])
    tok = _token(client, "sa1")
    assert client.get("/api/proxies", headers=_auth(tok)).status_code == 200


# ── root protection ───────────────────────────────────────────────────────────

def test_super_admin_cannot_create_or_become_root(client):
    _mk_user("sa2", "super_admin")
    _mk_user("rootuser", "root")
    tok = _token(client, "sa2")

    # cannot mint a root
    r = client.post("/api/users", headers=_auth(tok), json={
        "username": "newroot", "password": "pw123456", "role": "root"})
    assert r.status_code == 403, "super_admin was allowed to create a root account"

    # cannot promote self to root
    me = client.get("/api/users/me", headers=_auth(tok)).json()
    r2 = client.patch(f"/api/users/{me['id']}", headers=_auth(tok), json={"role": "root"})
    assert r2.status_code == 403

    # cannot edit / delete / reset / disable-2FA on an existing root.
    # The id is taken from the DB, not from the listing — the listing hides root,
    # so reading it from there would have made these assertions vacuous.
    from app.models.user import User as U
    from sqlalchemy import select as sel

    async def _root_id(maker):
        async with maker() as db:
            r = await db.execute(sel(U).where(U.username == "rootuser"))
            return r.scalars().first().id
    rid = _in_fresh_loop(_root_id)

    assert client.patch(f"/api/users/{rid}", headers=_auth(tok),
                        json={"full_name": "hijacked"}).status_code == 403
    assert client.post(f"/api/users/{rid}/password", headers=_auth(tok),
                       json={"new_password": "newpw123"}).status_code == 403
    assert client.post(f"/api/users/{rid}/totp/disable", headers=_auth(tok)).status_code == 403
    assert client.delete(f"/api/users/{rid}", headers=_auth(tok)).status_code == 403


def test_root_is_hidden_from_super_admin_listing(client):
    _mk_user("sa3", "super_admin")
    _mk_user("root2", "root")
    tok = _token(client, "sa3")
    names = [u["username"] for u in client.get("/api/users", headers=_auth(tok)).json()["items"]]
    assert "root2" not in names


# ── portal ownership ──────────────────────────────────────────────────────────

def test_visitor_cannot_read_or_delete_another_visitors_request(client):
    _mk_user("visA", "visitor", phone="09120000010", phone_verified=True)
    _mk_user("visB", "visitor", phone="09120000011", phone_verified=True)
    ta, tb = _token(client, "visA"), _token(client, "visB")

    r = client.post("/api/portal/requests", headers=_auth(ta),
                    json={"deal_type": "buy", "city": "ارومیه"})
    assert r.status_code == 201, r.text
    rid = r.json()["id"]

    # B must not see A's request in their own list
    mine = client.get("/api/portal/requests/mine", headers=_auth(tb)).json()
    assert all(i["id"] != rid for i in mine["items"])

    # nor delete it — 404, not 403, so the id is not confirmed
    assert client.delete(f"/api/portal/requests/{rid}", headers=_auth(tb)).status_code == 404


def test_visitor_cannot_use_staff_portal_routes(client):
    _mk_user("visC", "visitor", phone="09120000012", phone_verified=True)
    tok = _token(client, "visC")
    assert client.get("/api/portal/admin/requests", headers=_auth(tok)).status_code == 403
    assert client.get("/api/portal/admin/tickets", headers=_auth(tok)).status_code == 403


def test_unverified_visitor_is_blocked_from_portal(client):
    _mk_user("visD", "visitor", phone="09120000013", phone_verified=False)
    tok = _token(client, "visD")
    assert client.get("/api/portal/me", headers=_auth(tok)).status_code == 403


def test_only_super_admin_decides_tickets_and_approval_grants_admin(client):
    _mk_user("visE", "visitor", phone="09120000014", phone_verified=True)
    _mk_user("adm_portal", "admin", permissions=["portal"])
    _mk_user("sa4", "super_admin")
    tv, ta, ts = _token(client, "visE"), _token(client, "adm_portal"), _token(client, "sa4")

    t = client.post("/api/portal/tickets", headers=_auth(tv), json={"message": "سلام"})
    assert t.status_code == 201, t.text
    tid = t.json()["id"]

    # an admin with the portal permission still may not decide tickets
    r = client.post(f"/api/portal/admin/tickets/{tid}/decide", headers=_auth(ta),
                    json={"approve": True})
    assert r.status_code == 403

    # a crafted permission list cannot invent a key
    r2 = client.post(f"/api/portal/admin/tickets/{tid}/decide", headers=_auth(ts),
                     json={"approve": True, "permissions": ["crm", "not_a_real_perm"]})
    assert r2.status_code == 200, r2.text
    assert r2.json()["granted_permissions"] == ["crm"]

    # the visitor is now an admin, limited to what was granted
    tv2 = _token(client, "visE")
    assert client.get("/api/crm/leads", headers=_auth(tv2)).status_code == 200
    assert client.get("/api/proxies", headers=_auth(tv2)).status_code == 403

    # and the ticket cannot be decided twice
    assert client.post(f"/api/portal/admin/tickets/{tid}/decide", headers=_auth(ts),
                       json={"approve": False}).status_code == 400


# ── public sign-up ────────────────────────────────────────────────────────────

def test_signup_requires_the_sms_code_and_burns_it(client, monkeypatch):
    sent = {}

    async def fake_sms(to, msg, provider="kavenegar", **kw):
        sent["to"], sent["msg"] = to, msg
        return {"success": True, "provider": "test", "response": "ok"}

    import app.services.verification as v
    monkeypatch.setattr(v, "send_sms", fake_sms)

    r = client.post("/api/public/auth/register", json={
        "full_name": "علی محمدی", "phone": "09121112233",
        "password": "pw123456", "email": "a@b.com", "marketing_opt_in": True})
    assert r.status_code == 200, r.text
    assert r.json()["pending"] is True
    code = r.json()["debug_code"]           # only exposed off-production
    assert code and sent["to"] == "09121112233"

    # wrong code is refused
    assert client.post("/api/public/auth/verify",
                       json={"phone": "09121112233", "code": "00000"}).status_code == 400

    # right code logs in
    ok = client.post("/api/public/auth/verify",
                     json={"phone": "09121112233", "code": code})
    assert ok.status_code == 200, ok.text
    tok = ok.json()["access_token"]
    assert ok.json()["role"] == "visitor"

    # code is single-use
    assert client.post("/api/public/auth/verify",
                       json={"phone": "09121112233", "code": code}).status_code == 400

    # a fresh visitor still cannot reach the panel
    assert client.get("/api/crm/leads", headers=_auth(tok)).status_code == 403
    # marketing consent was recorded as given
    assert client.get("/api/users/me", headers=_auth(tok)).json()["marketing_opt_in"] is True


def test_public_login_refuses_staff_accounts(client):
    """Staff must not have a second login path that skips their second factor."""
    _mk_user("staff_pub", "admin", permissions=["crm"], phone="09129998877")
    r = client.post("/api/public/auth/login",
                    json={"identifier": "09129998877", "password": "pw123456"})
    assert r.status_code == 403


def test_api_config_no_longer_leaks_the_api_key(client):
    assert client.get("/api/config").status_code == 404


# ── migration backfill ────────────────────────────────────────────────────────

def test_backfill_preserves_existing_access():
    """The rollout must not take access away from anyone who has it today.

    Runs the three backfill statements in the same order as _migrate_auth_v2
    against a stand-in table. The order is the whole point: legacy rows have to
    be tagged while they still say 'user', otherwise step two renames them to
    'admin' and step three hands them the full set — quietly promoting a
    limited account to everything.
    """
    import json
    import sqlite3
    from app.auth.permissions import ALL_PERMISSIONS, LEGACY_USER_PERMISSIONS

    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, role TEXT, permissions TEXT)")
    db.executemany("INSERT INTO users (id, role, permissions) VALUES (?,?,?)", [
        (1, "super_admin", None),
        (2, "admin", None),       # predates the column — had everything in practice
        (3, "user", None),        # limited account
        (4, "user", None),
    ])

    legacy, full = json.dumps(LEGACY_USER_PERMISSIONS), json.dumps(ALL_PERMISSIONS)
    db.execute("UPDATE users SET permissions = ? WHERE role = 'user' AND permissions IS NULL", (legacy,))
    db.execute("UPDATE users SET role = 'admin' WHERE role = 'user'")
    db.execute("UPDATE users SET permissions = ? WHERE role = 'admin' AND permissions IS NULL", (full,))
    db.execute("UPDATE users SET permissions = '[]' WHERE permissions IS NULL")

    rows = {r[0]: (r[1], json.loads(r[2])) for r in
            db.execute("SELECT id, role, permissions FROM users")}

    assert rows[1] == ("super_admin", [])                    # bypasses anyway
    assert rows[2] == ("admin", ALL_PERMISSIONS)             # kept what it had
    assert rows[3] == ("admin", LEGACY_USER_PERMISSIONS)     # not widened
    assert rows[4] == ("admin", LEGACY_USER_PERMISSIONS)
    assert "crm" not in rows[3][1], "a limited legacy account was handed CRM access"
    assert all(p is not None for _, p in rows.values())


def test_boot_verification_rejects_a_half_applied_migration():
    """_verify_auth_v2 must raise when a column is missing, so the pod dies at
    startup and the previous one keeps serving."""
    import asyncio
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from app.database import _verify_auth_v2

    async def _go():
        eng = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with eng.begin() as conn:
            # users table without the new columns — a silently-skipped ALTER
            await conn.execute(text(
                "CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT)"))
            with pytest.raises(RuntimeError, match="missing"):
                await _verify_auth_v2(conn)
            # and passes once they are present
            for c in ("phone TEXT", "phone_verified BOOLEAN",
                      "marketing_opt_in BOOLEAN", "permissions TEXT"):
                await conn.execute(text(f"ALTER TABLE users ADD COLUMN {c}"))
            await _verify_auth_v2(conn)
        await eng.dispose()

    asyncio.run(_go())


# ── findings from the adversarial review ──────────────────────────────────────

def test_signup_name_cannot_smuggle_html_into_the_staff_user_table():
    """A visitor picks their own full_name and it lands in the super_admin user
    table. The panel must escape it at the sink; this asserts the sink exists
    and is escaped, because the payload itself is legitimately storable."""
    import re
    js = open("frontend/js/app.js", encoding="utf-8").read()
    body = js[js.index("async function loadUsers()"):js.index("async function openPermsEditor")]

    # every interpolation of a server field inside the row template must be esc()'d
    raw = re.findall(r"\$\{(u\.[a-z_]+)[^}]*\}", body)
    unescaped = [r for r in raw if r not in ("u.is_active", "u.role", "u.permissions")]
    assert not unescaped, f"unescaped server fields in the users table: {unescaped}"
    assert "esc(u.full_name" in body and "esc(u.username)" in body
    # and the phone must not be interpolated into an onclick attribute any more
    assert "'${u.divar_phone" not in body


def test_portal_email_must_look_like_an_email():
    """email='admin' would otherwise be stored and then collide with a staff
    username in the login lookup."""
    from app.schemas import PortalRegisterRequest
    from pydantic import ValidationError

    ok = PortalRegisterRequest(full_name="علی", phone="09121110000",
                               password="pw123456", email="a@b.com")
    assert ok.email == "a@b.com"
    for bad in ("admin", "root", "no-at-sign", "a@b"):
        with pytest.raises(ValidationError):
            PortalRegisterRequest(full_name="علی", phone="09121110000",
                                  password="pw123456", email=bad)


def test_portal_login_does_not_match_on_username(client):
    """Only phone and email resolve an account here — never username, so a
    visitor cannot aim the portal login at a staff row."""
    _mk_user("staffname", "admin", permissions=["crm"])
    r = client.post("/api/public/auth/login",
                    json={"identifier": "staffname", "password": "pw123456"})
    # no row matches by username -> generic 401, not the staff-account 403
    assert r.status_code == 401



def test_pending_response_carries_the_phone_for_email_login(client, monkeypatch):
    """Someone who signs in with their email must still be able to verify: the
    code goes to their phone, and /verify keys on the phone, so the response has
    to tell the page which number to use.

    The account is created directly rather than through /register — registering
    would start the resend cooldown and this test would measure the throttle
    (covered separately) instead of the email-login path.
    """
    async def fake_sms(to, msg, provider="kavenegar", **kw):
        return {"success": True, "provider": "test", "response": "ok"}
    import app.services.verification as v
    monkeypatch.setattr(v, "send_sms", fake_sms)

    _mk_user("09125554433", "visitor", password="pw123456",
             phone="09125554433", phone_verified=False, email="maryam@example.com")

    lr = client.post("/api/public/auth/login",
                     json={"identifier": "maryam@example.com", "password": "pw123456"})
    assert lr.status_code == 200, lr.text
    body = lr.json()
    assert body["pending"] is True
    assert body["phone"] == "09125554433", "page has no phone to send to /verify"

    ok = client.post("/api/public/auth/verify",
                     json={"phone": body["phone"], "code": body["debug_code"]})
    assert ok.status_code == 200, ok.text
    assert ok.json()["role"] == "visitor"

def test_root_is_not_excluded_by_any_role_literal():
    """root outranks super_admin, so no check may list the lower roles and omit
    it. This caught filing.py and scraper.py after the fourth role landed."""
    import re, pathlib
    offenders = []
    for f in pathlib.Path("app").rglob("*.py"):
        if "__pycache__" in str(f):
            continue
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if ".role" not in line or "root" in line:
                continue
            # a comparison naming super_admin but not root
            if re.search(r'\.role\s+(==|!=|in|not in)', line) and "super_admin" in line:
                offenders.append(f"{f}:{i}: {line.strip()}")
    assert not offenders, "role checks that silently exclude root:\n" + "\n".join(offenders)


def test_portal_resend_button_is_restored_on_success():
    """A successful resend must put the button label back; leaving it to the
    catch block left it spinning 'در حال ارسال…' forever."""
    js = open("frontend/js/portal.js", encoding="utf-8").read()
    body = js[js.index("async function doPortalResend"):js.index("async function doPortalVerify")]
    success = body[:body.index("} catch")]
    assert "withSpinner(btn, false" in success, "resend never restores the button on success"
