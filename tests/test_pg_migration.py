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

    return _run(_go())


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
