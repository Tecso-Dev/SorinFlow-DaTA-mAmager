"""
Login hardening, tried the way an attacker would try it — and the way the
operator would get locked out if it were wrong.

  * users.totp_last_step: Alembic 0010 and the boot-time ALTER, on Postgres.
"""
import asyncio
import os
import sys

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
