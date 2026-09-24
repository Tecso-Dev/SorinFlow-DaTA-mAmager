"""
NullPool opened a fresh DBAPI connection per checkout and dropped it right
after — never reusing one, and so never able to hand a pooled asyncpg
connection to a different event loop than the one that opened it. A real
pool reuses connections on purpose, which is the whole performance win, and
also the whole risk: this suite's own DB_POOL_SIZE=0 default (tests/
conftest.py) exists because this codebase genuinely mixes loops in a test or
two. These tests cover the selection logic in isolation — see
tests/conftest.py and tests/test_stats_trends.py's `boss` fixture for the
real cross-loop scenarios this was built against.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_dbpool.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

import pytest  # noqa: E402
from sqlalchemy.pool import NullPool, AsyncAdaptedQueuePool  # noqa: E402

import app.database as dbmod  # noqa: E402
from app.config import Settings  # noqa: E402


class TestSettingsFields:

    def test_defaults_match_the_phase_spec(self, monkeypatch):
        # tests/conftest.py sets DB_POOL_SIZE=0 for the whole suite (see
        # there for why) — cleared here so this checks the field's own
        # default, not the suite's override of it.
        monkeypatch.delenv("DB_POOL_SIZE", raising=False)
        monkeypatch.delenv("DB_MAX_OVERFLOW", raising=False)
        s = Settings(_env_file=None)
        assert s.db_pool_size == 5
        assert s.db_max_overflow == 10

    def test_env_vars_override_them(self, monkeypatch):
        monkeypatch.setenv("DB_POOL_SIZE", "3")
        monkeypatch.setenv("DB_MAX_OVERFLOW", "7")
        s = Settings(_env_file=None)
        assert s.db_pool_size == 3
        assert s.db_max_overflow == 7


class TestPoolKwargsSelection:
    """The exact function app/database.py builds its engine with."""

    def test_zero_means_nullpool(self):
        kw = dbmod._pool_kwargs_for(0, 10)
        assert kw == {"poolclass": NullPool}

    def test_a_positive_size_means_a_real_pool_with_the_given_numbers(self):
        kw = dbmod._pool_kwargs_for(5, 10)
        assert kw["poolclass"] is AsyncAdaptedQueuePool
        assert kw["pool_size"] == 5
        assert kw["max_overflow"] == 10

    def test_pre_ping_recycle_and_timeout_are_set(self):
        """pre_ping: a connection Postgres closed while idle fails fast, not
        mid-query. recycle: stays under any load balancer / firewall idle
        window. timeout: a checkout does not hang forever under load."""
        kw = dbmod._pool_kwargs_for(5, 10)
        assert kw["pool_pre_ping"] is True
        assert kw["pool_recycle"] == 1800
        assert kw["pool_timeout"] == 30

    def test_a_real_pool_never_carries_nullpool_only_kwargs(self):
        """The bug this guards: passing pool_size to an engine whose dialect
        would default to NullPool on its own (a file-backed sqlite URL does)
        raises TypeError at import time — this broke collection for every
        test module the moment DB_POOL_SIZE was not 0."""
        kw = dbmod._pool_kwargs_for(5, 10)
        assert kw["poolclass"] is not NullPool


class TestASharedEngineCanBeBuiltEitherWay:
    """Not app.database.engine itself — that is already constructed at
    import time from whatever DB_POOL_SIZE this process started with (0,
    per tests/conftest.py). A throwaway engine built the exact same way
    proves both branches actually work end to end, sqlite included."""

    @pytest.mark.asyncio
    async def test_nullpool_engine_connects_and_disposes(self):
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy import text

        eng = create_async_engine("sqlite+aiosqlite:///:memory:",
                                   **dbmod._pool_kwargs_for(0, 10))
        assert isinstance(eng.pool, NullPool)
        async with eng.connect() as conn:
            assert (await conn.execute(text("SELECT 1"))).scalar() == 1
        await eng.dispose()

    @pytest.mark.asyncio
    async def test_pooled_engine_connects_and_disposes(self):
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy import text

        eng = create_async_engine("sqlite+aiosqlite:///./_dbpool_pooled.db",
                                   **dbmod._pool_kwargs_for(3, 5))
        assert isinstance(eng.pool, AsyncAdaptedQueuePool)
        async with eng.connect() as conn:
            assert (await conn.execute(text("SELECT 1"))).scalar() == 1
        await eng.dispose()
        try:
            os.remove("./_dbpool_pooled.db")
        except OSError:
            pass


class TestCloseDbDisposesTheEngine:

    def test_close_db_calls_dispose(self):
        import inspect
        src = inspect.getsource(dbmod.close_db)
        assert "engine.dispose()" in src

    @pytest.mark.asyncio
    async def test_it_actually_calls_dispose(self, monkeypatch):
        calls = []

        async def _fake_dispose(self, *a, **kw):
            calls.append((a, kw))
        monkeypatch.setattr(type(dbmod.engine), "dispose", _fake_dispose)
        await dbmod.close_db()
        assert calls, "close_db must dispose the shared engine, or shutdown leaks connections"
