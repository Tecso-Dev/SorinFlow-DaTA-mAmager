"""
The schema step of a rollout, on its own: `python -m app.migrate`.

A pod started with DB_MIGRATE_ON_BOOT=false only checks that the database is
not behind its image's Alembic head (assert_schema_current) and refuses to
start if it is. The migration Job is what gets it there — the same init_db a boot
runs, but strict: an Alembic failure fails the Job instead of being printed.
"""
import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_migrate.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app import database  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ON_POSTGRES = str(database.engine.url).startswith("postgresql")


class TestTheSchemaCheck:

    async def test_behind_or_empty_refuses_ahead_or_at_head_passes(self, tmp_path):
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine
        head = database._script_head(database._alembic_config())
        eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/schema.db")
        try:
            with pytest.raises(RuntimeError, match="nothing"):
                await database.assert_schema_current(eng)
            async with eng.begin() as c:
                await c.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
                await c.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
                await c.execute(text("INSERT INTO alembic_version VALUES ('0001')"))
            with pytest.raises(RuntimeError) as behind:
                await database.assert_schema_current(eng)
            assert "0001" in str(behind.value) and head in str(behind.value), "both revisions, named"
            # Ahead: a revision this image has never heard of is a newer
            # release's schema — a rollback onto it must still start, since
            # every migration is additive.
            async with eng.begin() as c:
                await c.execute(text("UPDATE alembic_version SET version_num = '9999'"))
            await database.assert_schema_current(eng)
            async with eng.begin() as c:
                await c.execute(text("UPDATE alembic_version SET version_num = :h"), {"h": head})
            await database.assert_schema_current(eng)
        finally:
            await eng.dispose()


def _python(*args, env=None, timeout=300):
    return subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=True, text=True,
                          timeout=timeout, env={**os.environ, **(env or {})})


class TestPythonMAppMigrate:

    def test_a_published_secret_key_fails_it_before_any_database_work(self):
        r = _python("-m", "app.migrate", env={
            "ENVIRONMENT": "production", "SECRET_KEY": "your-super-secret-key-change-in-production",
            "DATABASE_URL": "postgresql+asyncpg://nobody@127.0.0.1:1/none"})
        out = r.stdout + r.stderr
        assert r.returncode == 1, out[-2000:]
        assert "Refusing to start in production: SECRET_KEY" in out and "migration failed" in out

    @pytest.mark.skipif(not ON_POSTGRES, reason="needs Postgres — the boot steps and Alembic are Postgres DDL")
    def test_strict_where_a_boot_is_forgiving(self):
        """Exit 0 at head; exit 0, warned but not upgraded, when the database
        is AHEAD of this image (a rollback onto a newer, additive schema) —
        the Job agrees with the forgiving path DB_MIGRATE_ON_BOOT=true takes,
        and with assert_schema_current, which already let a running pod start
        on one. Exit 1 only for a real failure: a revision this image knows
        about whose upgrade raises."""
        from sqlalchemy import text
        from sqlalchemy.engine import make_url
        from sqlalchemy.ext.asyncio import create_async_engine

        url = make_url(os.environ["DATABASE_URL"])
        scratch = url.set(database=f"{url.database}_migrate")
        scratch_env = {"DATABASE_URL": scratch.render_as_string(hide_password=False),
                       "ENVIRONMENT": "test"}

        async def _sql(target, *statements, autocommit=False):
            eng = create_async_engine(target, **({"isolation_level": "AUTOCOMMIT"} if autocommit else {}))
            try:
                async with eng.connect() as c:
                    for s in statements:
                        await c.execute(text(s))
                    await c.commit()
            finally:
                await eng.dispose()

        head = database._script_head(database._alembic_config())
        # A revision chained onto the real head whose upgrade() always
        # raises — a genuine failure that must still fail the Job, without
        # touching any revision this repo actually ships. Removed in finally.
        broken_rev = ROOT / "migrations" / "versions" / "test_only_broken_revision.py"

        admin = url.set(database="postgres")
        asyncio.run(_sql(admin, f'DROP DATABASE IF EXISTS "{scratch.database}" WITH (FORCE)',
                         f'CREATE DATABASE "{scratch.database}"', autocommit=True))
        try:
            r = _python("-m", "app.migrate", env=scratch_env)
            assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
            assert "migration complete" in r.stdout + r.stderr

            async def _check():
                eng = create_async_engine(scratch)
                try:
                    await database.assert_schema_current(eng)
                finally:
                    await eng.dispose()
            asyncio.run(_check())

            # Ahead of the image: '9999' is a revision this image's own
            # script directory has never heard of — the shape a rollback onto
            # a newer release's schema leaves behind. Migrations are
            # additive, so the Job must not fail trying to "upgrade" a schema
            # that is already ahead of it; that would turn a routine rollback
            # into an outage.
            asyncio.run(_sql(scratch, "UPDATE alembic_version SET version_num = '9999'"))
            r = _python("-m", "app.migrate", env=scratch_env)
            assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
            assert "migration complete" in r.stdout + r.stderr
            assert "ahead of it" in r.stdout + r.stderr

            # the same database, booted the ordinary way: agrees, unraised
            r = _python("-c", "import asyncio; from app.database import init_db; asyncio.run(init_db())",
                        env=scratch_env)
            assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
            assert "ahead of it" in r.stdout + r.stderr
            # neither call touched a schema/version already ahead of them
            asyncio.run(_check())

            # A real failure must still fail the Job: put the database back
            # at its real, known head, then give it one more revision whose
            # upgrade() always raises.
            asyncio.run(_sql(scratch, f"UPDATE alembic_version SET version_num = '{head}'"))
            broken_rev.write_text(
                '"""a revision that always fails — test_strict_where_a_boot_is_forgiving only.\n\n'
                f'Revision ID: test_only\nRevises: {head}\n"""\n'
                'revision = "test_only"\n'
                f'down_revision = "{head}"\n'
                'branch_labels = None\n'
                'depends_on = None\n\n\n'
                'def upgrade() -> None:\n'
                '    raise RuntimeError("intentional failure — test_strict_where_a_boot_is_forgiving")\n\n\n'
                'def downgrade() -> None:\n'
                '    pass\n'
            )
            r = _python("-m", "app.migrate", env=scratch_env)
            assert r.returncode == 1, (r.stdout + r.stderr)[-3000:]
            assert "migration failed" in r.stdout + r.stderr
        finally:
            broken_rev.unlink(missing_ok=True)
            asyncio.run(_sql(admin, f'DROP DATABASE IF EXISTS "{scratch.database}" WITH (FORCE)',
                             autocommit=True))
