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


def test_0011_creates_audit_events_matching_the_model():
    """0011 is a plain op.create_table, so on any database this suite's other
    fixtures already touch, create_all (via init_db, or via 0001's own
    create_all-from-current-models — see its docstring) has already built
    audit_events and 0011's own body never runs, guard included. That would
    make this test tautological: the schema would just be the model, compared
    to itself. So this starts a scratch schema already stamped at 0010 —
    where audit_events genuinely does not exist yet — the shape an
    established production database is actually in, and upgrades it to head
    for real, so op.create_table's own column types, nullability and index
    names are what gets checked against AuditEvent.
    """
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from app.models.audit_event import AuditEvent

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = Config(os.path.join(root, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(root, "migrations"))
    SCHEMA = "sf_audit_mig"

    async def _go():
        eng = create_async_engine(PG_URL)
        async with eng.begin() as c:
            await c.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
            await c.execute(text(f"CREATE SCHEMA {SCHEMA}"))

        eng2 = create_async_engine(
            PG_URL, connect_args={"server_settings": {"search_path": SCHEMA}})
        async with eng2.begin() as c:
            await c.execute(text(
                "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
            await c.execute(text("INSERT INTO alembic_version VALUES ('0010')"))

        def _upgrade(sync_conn):
            cfg.attributes["connection"] = sync_conn
            # 0011 itself, not head: the revisions after it alter tables
            # (properties, leads, …) this scratch schema was never given
            command.upgrade(cfg, "0011")

        async with eng2.begin() as c:
            await c.run_sync(_upgrade)

        async with eng2.begin() as c:
            cols = {r[0]: (r[1], r[2] == "YES") for r in (await c.execute(text(
                "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
                f"WHERE table_schema='{SCHEMA}' AND table_name='audit_events'"))).all()}
            idx = {r[0] for r in (await c.execute(text(
                "SELECT indexname FROM pg_indexes "
                f"WHERE schemaname='{SCHEMA}' AND tablename='audit_events'"))).all()}
        await eng2.dispose()
        async with eng.begin() as c:
            await c.execute(text(f"DROP SCHEMA {SCHEMA} CASCADE"))
        await eng.dispose()
        return cols, idx

    cols, idx = _run(_go())

    model_cols = {c.name: c.nullable for c in AuditEvent.__table__.columns}
    assert set(model_cols) == set(cols), \
        f"columns differ: model={set(model_cols)} schema={set(cols)}"
    for name, model_nullable in model_cols.items():
        assert cols[name][1] == model_nullable, \
            f"{name}: model nullable={model_nullable}, migration built {cols[name][1]}"
    assert cols["id"] == ("bigint", False)
    assert cols["created_at"][0] == "timestamp with time zone"
    assert cols["detail"][0] == "json"

    for name in ("ix_audit_events_created_at", "ix_audit_events_actor_created",
                 "ix_audit_events_action_created"):
        assert name in idx, f"{name} missing from what 0011 built"


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


def test_migrate_steps_skip_any_stamped_database_but_run_on_an_unversioned_one():
    """Every _migrate_* step is pre-Alembic DDL — `ALTER TABLE ... ADD COLUMN
    IF NOT EXISTS` still takes ACCESS EXCLUSIVE even though nothing changes —
    and app/migrate.py runs init_db() against the live database on every
    deploy. A database Alembic has stamped, at head or behind it (every deploy
    that brings a migration), must not pay those locks. The one exception is
    _migrate_cookie_is_enabled, which exists because a stamp can be wrong
    (see test_a_second_0009... above): it still runs. A database Alembic has
    never stamped still needs them all."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    import app.database as db

    SCHEMA = "sf_migrate_gate"
    saved_engine, saved_maker = db.engine, db.async_session_maker
    db.engine = create_async_engine(
        PG_URL, connect_args={"server_settings": {"search_path": SCHEMA}})
    db.async_session_maker = async_sessionmaker(
        db.engine, expire_on_commit=False, autocommit=False, autoflush=False)

    async def _column_exists(column):
        async with db.engine.begin() as c:
            return bool((await c.execute(text(
                "SELECT 1 FROM information_schema.columns WHERE table_name='cookies' "
                "AND column_name=:c AND table_schema=current_schema()"), {"c": column})).first())

    async def _drop(column):
        async with db.engine.begin() as c:
            await c.execute(text(f"ALTER TABLE cookies DROP COLUMN {column}"))

    async def _go():
        eng = create_async_engine(PG_URL)
        async with eng.begin() as c:
            await c.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
            await c.execute(text(f"CREATE SCHEMA {SCHEMA}"))
        await eng.dispose()

        await db.init_db()                                    # fresh -> stamped at head
        assert await _column_exists("challenged_at"), "create_all did not build the column"
        seen = {}

        await _drop("challenged_at")                          # _migrate_cookie_challenged_at's
        await _drop("is_enabled")                             # _migrate_cookie_is_enabled's
        await db.init_db()                                    # at head
        seen["skipped_at_head"] = not await _column_exists("challenged_at")
        seen["guard_ran_at_head"] = await _column_exists("is_enabled")

        async with db.engine.begin() as c:                    # behind head: a deploy with a migration
            await c.execute(text("UPDATE alembic_version SET version_num = '0015'"))
        await db.init_db()
        seen["skipped_behind_head"] = not await _column_exists("challenged_at")

        async with db.engine.begin() as c:
            await c.execute(text("DROP TABLE alembic_version"))   # unversioned again
        await db.init_db()
        seen["ran_when_unversioned"] = await _column_exists("challenged_at")
        return seen

    try:
        seen = _run(_go())
    finally:
        async def _drop_schema():
            eng = create_async_engine(PG_URL)
            async with eng.begin() as c:
                await c.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
            await eng.dispose()
        _run(db.engine.dispose())
        _run(_drop_schema())
        db.engine, db.async_session_maker = saved_engine, saved_maker

    assert seen["skipped_at_head"], "a _migrate_* step ran on a database at this image's head"
    assert seen["guard_ran_at_head"], "_migrate_cookie_is_enabled must run on every boot"
    assert seen["skipped_behind_head"], "a _migrate_* step ran on a stamped database behind head"
    assert seen["ran_when_unversioned"], "an unversioned database's _migrate_* steps did not run"


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


def test_0016_gives_owned_rows_their_account_and_comes_back_off():
    """0016 on a schema as 0015 left it: the six owned tables gain the
    account column (FK ON DELETE SET NULL, indexed) and owner_resolved_from,
    each row gets the one staff account its name means, the boot step
    resolves what an older release writes afterwards, and the downgrade
    takes it all away again. The scratch schema is built from the models
    and put back to 0015 by dropping the new columns — the shape an
    established production database is in."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    import app.models  # noqa: F401 — every table, for create_all
    from app.database import Base, _backfill_owner_ids
    from app.auth.visibility import OWNERSHIP

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = Config(os.path.join(root, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(root, "migrations"))
    SCHEMA = "sf_owner_mig"

    def _alembic(target, fn):
        def _go(sync_conn):
            cfg.attributes["connection"] = sync_conn
            fn(cfg, target)
        return _go

    async def _cols(c):
        return {(r[0], r[1]): (r[2], r[3], r[4]) for r in (await c.execute(text(
            "SELECT table_name, column_name, data_type, character_maximum_length, is_nullable "
            f"FROM information_schema.columns WHERE table_schema='{SCHEMA}'"))).all()}

    async def _go():
        eng = create_async_engine(PG_URL)
        async with eng.begin() as c:
            await c.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
            await c.execute(text(f"CREATE SCHEMA {SCHEMA}"))
        await eng.dispose()

        eng = create_async_engine(PG_URL, connect_args={"server_settings": {"search_path": SCHEMA}})
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
            for table, (_name, id_col) in OWNERSHIP.items():
                await c.execute(text(f"ALTER TABLE {table} DROP COLUMN {id_col}, DROP COLUMN owner_resolved_from"))
            await c.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
            await c.execute(text("INSERT INTO alembic_version VALUES ('0015')"))
            await c.execute(text(
                "INSERT INTO users (id, username, full_name, hashed_password, role, is_active, totp_enabled, "
                "email_2fa_enabled, phone_verified, email_verified, marketing_opt_in, presence, token_version) VALUES "
                "(1, 'mina', 'مینا رضایی', 'x', 'admin', true, false, false, false, false, false, 'available', 0),"
                "(2, 'twin1', 'دوقلو', 'x', 'admin', true, false, false, false, false, false, 'available', 0),"
                "(3, 'twin2', 'دوقلو', 'x', 'super_admin', true, false, false, false, false, false, 'available', 0),"
                "(4, 'visitor', 'مینا رضایی', 'x', 'visitor', true, false, false, false, false, false, 'available', 0),"
                "(5, 'reza', NULL, 'x', 'admin', true, false, false, false, false, false, 'available', 0)"))
            await c.execute(text("INSERT INTO properties (id, tag_number, divar_id, title, url) "
                                 "VALUES (1, 'b', 'b', 'x', 'u')"))
            await c.execute(text("INSERT INTO crm_customers (id, full_name) VALUES (1, 'x')"))
            for i, name in ((10, "مینا رضایی"), (11, "دوقلو"), (12, "reza"), (13, None)):
                await c.execute(text("INSERT INTO properties (id, tag_number, divar_id, title, url, created_by) "
                                     "VALUES (:i, :t, :t, 'x', 'u', :n)"), {"i": i, "t": f"p{i}", "n": name})
                await c.execute(text("INSERT INTO crm_cabinets (id, name, owner) VALUES (:i, 'c', :n)"),
                                {"i": i, "n": name})
                await c.execute(text("INSERT INTO crm_customer_matches (id, property_id, customer_id, score, "
                                     "consultant) VALUES (:i, :i, 1, 60, :n)"), {"i": i, "n": name})
                await c.execute(text("INSERT INTO crm_tasks (id, title, assigned_to) VALUES (:i, 't', :n)"),
                                {"i": i, "n": name})
                await c.execute(text("INSERT INTO leads (id, property_id, status, call_attempts, assigned_to) "
                                     "VALUES (:i, 1, 'new', 0, :n)"), {"i": i, "n": name})
                await c.execute(text("INSERT INTO crm_customers (id, full_name, consultant_name) "
                                     "VALUES (:i, 'x', :n)"), {"i": i, "n": name})

        async with eng.begin() as c:
            await c.run_sync(_alembic("0016", command.upgrade))
        async with eng.begin() as c:
            cols = await _cols(c)
            fks = {r[0]: r[1] for r in (await c.execute(text(
                "SELECT constraint_name, delete_rule FROM information_schema.referential_constraints "
                f"WHERE constraint_schema='{SCHEMA}' AND constraint_name LIKE 'fk_%_user'"))).all()}
            idx = {r[0] for r in (await c.execute(text(
                f"SELECT indexname FROM pg_indexes WHERE schemaname='{SCHEMA}'"))).all()}
            owners = {}
            for table, (_name, id_col) in OWNERSHIP.items():
                owners[table] = {r[0]: (r[1], r[2]) for r in (await c.execute(text(
                    f"SELECT id, {id_col}, owner_resolved_from FROM {table} WHERE id >= 10"))).all()}
            # an older release reassigns by name after the upgrade; the boot step catches it
            await c.execute(text("UPDATE crm_tasks SET assigned_to = 'reza' WHERE id = 10"))
        async with eng.begin() as c:
            await _backfill_owner_ids(c)
        async with eng.begin() as c:
            after_boot = (await c.execute(text("SELECT assigned_to_user_id FROM crm_tasks WHERE id = 10"))).scalar()
            await c.execute(text("DELETE FROM users WHERE id = 5"))
            after_delete = (await c.execute(text("SELECT assigned_to_user_id FROM crm_tasks WHERE id = 10"))).scalar()
            version = (await c.execute(text("SELECT version_num FROM alembic_version"))).scalar()

        async with eng.begin() as c:
            await c.run_sync(_alembic("0015", command.downgrade))
        async with eng.begin() as c:
            down = await _cols(c)
        async with eng.begin() as c:
            await c.run_sync(_alembic("0016", command.upgrade))     # up again, over rows it resolved before
        async with eng.begin() as c:
            again = (await c.execute(text("SELECT assigned_to_user_id FROM crm_tasks WHERE id = 11"))).scalar()
            twins_gone = (await c.execute(text("SELECT count(*) FROM users WHERE username LIKE 'twin%'"))).scalar()
        await eng.dispose()

        eng = create_async_engine(PG_URL)
        async with eng.begin() as c:
            await c.execute(text(f"DROP SCHEMA {SCHEMA} CASCADE"))
        await eng.dispose()
        return cols, fks, idx, owners, after_boot, after_delete, version, down, again, twins_gone

    cols, fks, idx, owners, after_boot, after_delete, version, down, again, twins = _run(_go())

    assert version == "0016"
    for table, (_name, id_col) in OWNERSHIP.items():
        # the migration built exactly what the model declares
        model_col = Base.metadata.tables[table].c[id_col]
        assert cols[(table, id_col)] == ("integer", None, "YES") and model_col.nullable
        assert cols[(table, "owner_resolved_from")] == ("character varying", 200, "YES")
        fk = next(iter(model_col.foreign_keys))
        assert fks.get(fk.name) == "SET NULL", (table, fks)
        assert f"ix_{table}_{id_col}" in idx
        assert (table, id_col) not in down and (table, "owner_resolved_from") not in down
        # mina's own (not the visitor's), the twins nobody's, reza by his
        # username, no name no owner
        assert owners[table] == {10: (1, "مینا رضایی"), 11: (None, "دوقلو"), 12: (5, "reza"), 13: (None, None)}, table
    assert after_boot == 5, "the boot step resolved a reassignment written by name"
    assert after_delete is None, "a deleted account leaves its rows to nobody"
    assert twins == 2 and again is None, "a re-run upgrade decides from scratch, the same way"
