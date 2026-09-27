"""
_guard() in app/database.py bounds how long a startup migration waits for a
lock. It used SET LOCAL unconditionally, which sqlite does not have, so on
sqlite it raised into its caller and the step it guarded was skipped.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_startup_guard.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from app.database import _guard  # noqa: E402


async def test_on_sqlite_it_is_a_no_op_not_an_error(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/g.db")
    try:
        async with eng.begin() as conn:
            await _guard(conn)
            await conn.execute(text("CREATE TABLE t (x INTEGER)"))   # the guarded step still runs
            assert (await conn.execute(text("SELECT count(*) FROM t"))).scalar() == 0
    finally:
        await eng.dispose()


async def test_on_postgres_it_bounds_this_transaction_only():
    url = os.environ["DATABASE_URL"]
    if not url.startswith("postgresql"):
        pytest.skip("needs Postgres — SET LOCAL is what is being checked")
    eng = create_async_engine(url)
    try:
        async with eng.begin() as conn:
            await _guard(conn)
            assert (await conn.execute(text("SHOW lock_timeout"))).scalar() == "5s"
            assert (await conn.execute(text("SHOW statement_timeout"))).scalar() == "2min"
        async with eng.connect() as conn:          # the next transaction is not bound by it
            assert (await conn.execute(text("SHOW lock_timeout"))).scalar() == "0"
    finally:
        await eng.dispose()
