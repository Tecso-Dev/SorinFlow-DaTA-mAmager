"""
The auth-v2 migration against a REAL Postgres.

Skipped unless PG_TEST_URL is set, because CI runs on SQLite — and SQLite is
exactly why this file exists: it silently ignores `ALTER TABLE ... ADD COLUMN
IF NOT EXISTS`, so the rest of the suite proves the backfill logic but never
proves the DDL that production actually executes.

Run it against a throwaway database before a release:

    createdb sfmig
    PG_TEST_URL=postgresql+asyncpg://user@127.0.0.1:5432/sfmig \\
        pytest tests/test_pg_migration.py -v
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PG_URL = os.environ.get("PG_TEST_URL")

pytestmark = pytest.mark.skipif(
    not PG_URL, reason="set PG_TEST_URL to run the Postgres migration test")

# The users table exactly as production has it today, before this change.
OLD_SCHEMA = """
DROP TABLE IF EXISTS users CASCADE;
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(100) UNIQUE NOT NULL,
    email VARCHAR(200) UNIQUE,
    full_name VARCHAR(200),
    hashed_password VARCHAR(500) NOT NULL,
    role VARCHAR(50) NOT NULL DEFAULT 'user',
    is_active BOOLEAN DEFAULT TRUE,
    last_login TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    divar_phone VARCHAR(20),
    totp_secret VARCHAR(64),
    totp_enabled BOOLEAN NOT NULL DEFAULT FALSE
);
INSERT INTO users (username, full_name, hashed_password, role) VALUES
    ('owner',   'Owner',    'x', 'super_admin'),
    ('agent1',  'Agent 1',  'x', 'admin'),
    ('viewer1', 'Viewer 1', 'x', 'user'),
    ('viewer2', 'Viewer 2', 'x', 'user');
"""


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(scope="module")
def migrated():
    """Apply the migration to a pre-migration table and hand back the rows."""
    saved_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = PG_URL
    os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
    os.environ.setdefault("LOGS_PATH", "/tmp")
    os.environ.setdefault("IMAGES_PATH", "/tmp")

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from app.database import _migrate_auth_v2

    async def _go():
        eng = create_async_engine(PG_URL)
        async with eng.begin() as c:
            for stmt in OLD_SCHEMA.strip().split(";"):
                if stmt.strip():
                    await c.execute(text(stmt))
        async with eng.begin() as c:
            await _migrate_auth_v2(c)
        # a second pass must change nothing — pods restart
        async with eng.begin() as c:
            await _migrate_auth_v2(c)
        async with eng.begin() as c:
            rows = {r[0]: (r[1], r[2]) for r in (await c.execute(text(
                "SELECT username, role, permissions FROM users"))).all()}
            cols = {r[0] for r in (await c.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='users' AND table_schema=current_schema()"))).all()}
            idx = [r[0] for r in (await c.execute(text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE indexname='ix_users_phone_unique'"))).all()]
            nulls = (await c.execute(text(
                "SELECT count(*) FROM users WHERE phone_verified IS NULL "
                "OR marketing_opt_in IS NULL"))).scalar()
        await eng.dispose()
        return rows, cols, idx, nulls

    try:
        return _run(_go())
    finally:
        # Later modules build their own engines from DATABASE_URL. Left
        # pointing here, they seeded their users into this database while the
        # app looked for them in the real one: 46 logins answered 401.
        if saved_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = saved_url


def test_columns_are_added(migrated):
    _rows, cols, _idx, _nulls = migrated
    for need in ("phone", "phone_verified", "marketing_opt_in", "permissions"):
        assert need in cols, f"{need} was not added"


def test_not_null_defaults_reach_existing_rows(migrated):
    _rows, _cols, _idx, nulls = migrated
    assert nulls == 0, "existing rows kept NULL in a NOT NULL column"


def test_phone_index_is_partial(migrated):
    _rows, _cols, idx, _nulls = migrated
    assert idx and "WHERE" in idx[0], "phone index missing or not partial"


def test_nobody_loses_or_gains_access(migrated):
    """The whole point of the backfill: a rollout must not change what any
    existing account can reach."""
    from app.auth.permissions import ALL_PERMISSIONS, LEGACY_USER_PERMISSIONS
    rows, _cols, _idx, _nulls = migrated

    assert rows["owner"][0] == "super_admin"
    # an admin from before the permission column had everything in practice
    assert rows["agent1"][0] == "admin"
    assert sorted(rows["agent1"][1]) == sorted(ALL_PERMISSIONS)
    # the retired 'user' role becomes admin but keeps its two areas
    for legacy in ("viewer1", "viewer2"):
        assert rows[legacy][0] == "admin"
        assert sorted(rows[legacy][1]) == sorted(LEGACY_USER_PERMISSIONS)
        assert "crm" not in rows[legacy][1], "a limited account was widened"
    assert not any(v[0] == "user" for v in rows.values())
    assert all(v[1] is not None for v in rows.values())


def test_boot_guard_raises_on_a_half_applied_schema():
    """A silently-skipped ALTER must stop the pod, not serve a broken app."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from app.database import _verify_auth_v2

    async def _go():
        eng = create_async_engine(PG_URL)
        try:
            async with eng.begin() as c:
                await c.execute(text("DROP TABLE IF EXISTS users CASCADE"))
                await c.execute(text(
                    "CREATE TABLE users (id SERIAL PRIMARY KEY, username VARCHAR(100))"))
                with pytest.raises(RuntimeError, match="missing"):
                    await _verify_auth_v2(c)
        finally:
            # Put the table back before leaving. This test deliberately leaves a
            # users table with no `role` column, and anything that runs
            # init_db() against this database afterwards — the rest of the
            # suite, or simply the next run — hits
            # `UPDATE users ... WHERE role = 'user'`, which aborts the
            # transaction and fails every test after it. CI gets a fresh
            # service container and never noticed; a developer running the
            # suite twice locally would.
            async with eng.begin() as c:
                for stmt in OLD_SCHEMA.strip().split(";"):
                    if stmt.strip():
                        await c.execute(text(stmt))
            await eng.dispose()

    _run(_go())


def test_auth_migration_survives_an_earlier_migration_failing():
    """init_db() runs thirteen migrations. They used to share one transaction,
    and each swallows its own exception — which on Postgres is a trap: the first
    failed statement aborts the transaction, so every later migration silently
    becomes "current transaction is aborted".

    Here an earlier migration is made to fail for real (a duplicate serial_no
    makes _migrate_property_serial's UNIQUE index impossible). The auth columns
    must still land, because they now run in their own transaction.
    """
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    import app.database as db

    # init_db() uses the module-level engine, which was bound to DATABASE_URL at
    # import time — not to PG_TEST_URL. Point it at the test database for the
    # duration, and put it back afterwards so no other test inherits it.
    saved_engine, saved_maker = db.engine, db.async_session_maker
    db.engine = create_async_engine(PG_URL)
    db.async_session_maker = async_sessionmaker(
        db.engine, expire_on_commit=False, autocommit=False, autoflush=False)

    async def _go():
        eng = create_async_engine(PG_URL)
        async with eng.begin() as c:
            # a users table from before this change
            for stmt in OLD_SCHEMA.strip().split(";"):
                if stmt.strip():
                    await c.execute(text(stmt))
            # and a properties table that will break the serial migration
            await c.execute(text("DROP TABLE IF EXISTS properties CASCADE"))
            await c.execute(text(
                "CREATE TABLE properties (id SERIAL PRIMARY KEY, serial_no INTEGER, "
                "title VARCHAR(500), description TEXT, seller_name VARCHAR(200), "
                "advertiser_type VARCHAR(20))"))
            await c.execute(text(
                "INSERT INTO properties (serial_no, title) VALUES (1000,'a'),(1000,'b')"))
        await eng.dispose()

        # the real boot path, with a guaranteed failure part-way through
        await db.init_db()

        eng = create_async_engine(PG_URL)
        async with eng.begin() as c:
            cols = {r[0] for r in (await c.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='users' AND table_schema=current_schema()"))).all()}
            roles = {r[0]: r[1] for r in (await c.execute(text(
                "SELECT username, role FROM users"))).all()}
        await eng.dispose()
        return cols, roles

    try:
        cols, roles = _run(_go())
    finally:
        _run(db.engine.dispose())
        db.engine, db.async_session_maker = saved_engine, saved_maker

    for need in ("phone", "phone_verified", "marketing_opt_in", "permissions"):
        assert need in cols, (
            f"{need} missing — an unrelated migration's failure still poisons "
            "the auth migration")
    # and the backfill ran, so the boot was not merely 'not crashing'
    assert roles["viewer1"] == "admin"
    assert "user" not in roles.values()


def test_startup_never_waits_forever_for_a_lock():
    """Two deploys (65048fc, b491c0c) died here.

    An ALTER TABLE needs ACCESS EXCLUSIVE. During a rolling deploy the old pod
    is still serving, and one connection left idle in transaction holds a
    conflicting lock — so the new pod blocked, never opened its port, failed
    its liveness probe, and was SIGKILLed. Kubernetes then never terminated the
    old pod, because it was waiting for the new one to become ready.

    Every database statement executed before uvicorn starts listening must
    therefore have a lock timeout.
    """
    import inspect
    import app.database as db

    guard = inspect.getsource(db._guard)
    assert "lock_timeout" in guard

    # each migration in its own transaction, so one failure cannot roll back
    # create_all along with it
    init = inspect.getsource(db.init_db)
    assert "for step in (" in init, "migrations must not share one transaction"
    assert init.count("_guard(conn)") >= 3

    # the seeders open their own sessions and run after the migrations
    for fn in (db._seed_super_admin, db._seed_root):
        assert "lock_timeout" in inspect.getsource(fn), \
            f"{fn.__name__} can block startup indefinitely"

    # and a failed seed must not stop a pod that is otherwise able to serve
    assert "seed.__name__" in init and "skipped" in init


# ── the profile columns and the forwarder backfill ────────────────────────────

@pytest.fixture(scope="module")
def profile_migrated(migrated):
    """On top of the auth migration: the profile columns, and the one-time
    forwarder backfill — run twice, with a permission removed in between,
    because pods restart and a removal must stick."""
    import json
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from app.database import _migrate_profile, _backfill_forwarder_permission

    async def _go():
        eng = create_async_engine(PG_URL)
        async with eng.begin() as c:
            await c.execute(text(
                "CREATE TABLE IF NOT EXISTS app_settings (key VARCHAR(100) PRIMARY KEY, "
                "value TEXT, updated_by VARCHAR(200), updated_at TIMESTAMPTZ DEFAULT now())"))
            await c.execute(text("DELETE FROM app_settings WHERE key = 'migration:forwarder_permission'"))
            await c.execute(text("DELETE FROM users WHERE username IN ('mig_fw_yes', 'mig_fw_no')"))
            await c.execute(text(
                "INSERT INTO users (username, hashed_password, role, permissions) VALUES "
                "('mig_fw_yes', 'x', 'admin', :a), ('mig_fw_no', 'x', 'admin', :b)"),
                {"a": json.dumps(["divar_auth", "crm"]), "b": json.dumps(["crm"])})
        async with eng.begin() as c:
            await _migrate_profile(c)
            await _backfill_forwarder_permission(c)
        async with eng.begin() as c:
            first = {r[0]: r[1] for r in (await c.execute(text(
                "SELECT username, permissions::text FROM users WHERE username LIKE 'mig_fw_%'"))).all()}
            # a super_admin takes it away again …
            await c.execute(text(
                "UPDATE users SET permissions = :p WHERE username = 'mig_fw_yes'"),
                {"p": json.dumps(["divar_auth", "crm"])})
        # … and the next boot must not hand it back
        async with eng.begin() as c:
            await _migrate_profile(c)
            await _backfill_forwarder_permission(c)
        async with eng.begin() as c:
            second = {r[0]: r[1] for r in (await c.execute(text(
                "SELECT username, permissions::text FROM users WHERE username LIKE 'mig_fw_%'"))).all()}
            cols = {r[0] for r in (await c.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='users' AND table_schema=current_schema()"))).all()}
            nulls = (await c.execute(text(
                "SELECT count(*) FROM users WHERE presence IS NULL OR token_version IS NULL"))).scalar()
        await eng.dispose()
        return first, second, cols, nulls

    return _run(_go())


def test_profile_columns_are_added(profile_migrated):
    _f, _s, cols, nulls = profile_migrated
    for need in ("headline", "bio", "links", "presence", "avatar_token", "token_version"):
        assert need in cols, f"{need} was not added"
    assert nulls == 0


def test_forwarder_follows_divar_auth_once(profile_migrated):
    first, second, _c, _n = profile_migrated
    assert "forwarder" in first["mig_fw_yes"], "an admin who could open it yesterday lost it"
    assert "forwarder" not in first["mig_fw_no"], "an admin who never had divar_auth gained it"
    assert "forwarder" not in second["mig_fw_yes"], "a removed permission came back on the next boot"


# ── Alembic: stamped at boot, and the models describe the real schema ─────────

def test_alembic_stamps_at_boot_and_models_match_the_schema():
    """Roadmap #20. Two things a green sqlite run cannot prove:

    * a fresh database is stamped at head, and an established, pre-Alembic
      one (no alembic_version table) is stamped at the baseline and brought
      to head by init_db() — production's first boot after this change;
    * `alembic check` finds no drift between the models and the schema the
      boot path produces. Without that, every autogenerated revision would
      carry spurious drops (an FK, two indexes) for whoever trusted it to
      apply to production.

    The tests above deliberately wreck this database's tables, so the boot
    runs in its own schema, created here and dropped at the end.
    """
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    import app.database as db

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = Config(os.path.join(root, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(root, "migrations"))
    head = ScriptDirectory.from_config(cfg).get_current_head()

    saved_engine, saved_maker = db.engine, db.async_session_maker
    db.engine = create_async_engine(
        PG_URL, connect_args={"server_settings": {"search_path": "sf_alembic"}})
    db.async_session_maker = async_sessionmaker(
        db.engine, expire_on_commit=False, autocommit=False, autoflush=False)

    async def _version():
        async with db.engine.begin() as c:
            return (await c.execute(
                text("SELECT version_num FROM alembic_version"))).scalars().all()

    async def _go():
        async with db.engine.begin() as c:
            await c.execute(text("DROP SCHEMA IF EXISTS sf_alembic CASCADE"))
            await c.execute(text("CREATE SCHEMA sf_alembic"))
        await db.init_db()                     # fresh: create_all, stamp head
        fresh = await _version()
        async with db.engine.begin() as c:
            await c.execute(text("DROP TABLE alembic_version"))
        await db.init_db()                     # pre-Alembic: stamp baseline, upgrade
        established = await _version()
        async with db.engine.begin() as c:
            # raises AutogenerateDiffsDetected when the models and the schema differ
            await c.run_sync(lambda sc: (cfg.attributes.__setitem__("connection", sc),
                                         command.check(cfg)))
            await c.execute(text("DROP SCHEMA sf_alembic CASCADE"))
        return fresh, established

    try:
        fresh, established = _run(_go())
    finally:
        _run(db.engine.dispose())
        db.engine, db.async_session_maker = saved_engine, saved_maker

    assert fresh == [head]
    assert established == [head]


def test_a_second_0009_that_added_cookies_enabled_does_not_strand_is_enabled():
    """Local main once held an unpushed revision «0009» that added
    cookies.enabled; sorinflow-v2's 0009 adds cookies.is_enabled. Had the
    first reached production, Alembic would read 0009 as applied and never
    add is_enabled — every cookies query failing, /health still green. The
    boot must build the column anyway."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    import app.database as db

    saved_engine, saved_maker = db.engine, db.async_session_maker
    db.engine = create_async_engine(PG_URL)
    db.async_session_maker = async_sessionmaker(
        db.engine, expire_on_commit=False, autocommit=False, autoflush=False)

    async def _cols(eng):
        async with eng.begin() as c:
            return {r[0] for r in (await c.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='cookies' AND table_schema=current_schema()"))).all()}

    async def _go():
        await db.init_db()                    # a database at head
        eng = create_async_engine(PG_URL)
        async with eng.begin() as c:          # ...as the other 0009 would have left it
            await c.execute(text("ALTER TABLE cookies DROP COLUMN IF EXISTS is_enabled"))
            await c.execute(text(
                "ALTER TABLE cookies ADD COLUMN IF NOT EXISTS enabled BOOLEAN NOT NULL DEFAULT TRUE"))
            await c.execute(text("UPDATE alembic_version SET version_num = '0009'"))
        assert "is_enabled" not in await _cols(eng)
        await eng.dispose()

        await db.init_db()                    # sorinflow-v2 boots on it

        eng = create_async_engine(PG_URL)
        cols = await _cols(eng)
        await eng.dispose()
        return cols

    try:
        cols = _run(_go())
    finally:
        _run(db.engine.dispose())
        db.engine, db.async_session_maker = saved_engine, saved_maker

    assert "is_enabled" in cols


def test_the_boot_refuses_a_users_table_without_totp_last_step():
    """Were Alembic 0010 and its boot ALTER both to lose the lock race, every
    user load would 500 on a pod reporting Ready. The boot check refuses, so
    the deploy rolls back instead."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from app.database import _migrate_auth_v2, _verify_auth_v2

    async def _go():
        eng = create_async_engine(PG_URL)
        try:
            async with eng.begin() as c:
                for stmt in OLD_SCHEMA.strip().split(";"):
                    if stmt.strip():
                        await c.execute(text(stmt))
                await _migrate_auth_v2(c)
                await c.execute(text("ALTER TABLE users DROP COLUMN IF EXISTS totp_last_step"))
                with pytest.raises(RuntimeError, match="totp_last_step"):
                    await _verify_auth_v2(c)
        finally:
            # leave a users table later boots can migrate, as the test above does
            async with eng.begin() as c:
                for stmt in OLD_SCHEMA.strip().split(";"):
                    if stmt.strip():
                        await c.execute(text(stmt))
            await eng.dispose()

    _run(_go())
