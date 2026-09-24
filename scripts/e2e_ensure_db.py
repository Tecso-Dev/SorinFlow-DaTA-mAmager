"""Create the Postgres database DATABASE_URL points at, if it isn't there yet.

Used only by scripts/e2e_up.sh (the Playwright webServer command). Idempotent
on purpose: the e2e database is not dropped between runs (scripts/seed_local.py
is itself idempotent), so the three-runs-in-a-row flakiness check reuses one
database instead of paying migration + seed cost three times.

asyncpg directly, not `createdb`/`psql`: asyncpg is already a project
dependency, and the CI postgres:16-alpine service has no client tools
installed for a shell step to call.
"""
import asyncio
import os
import sys
from urllib.parse import urlsplit, urlunsplit

import asyncpg


async def main() -> None:
    raw = os.environ["DATABASE_URL"]
    # asyncpg speaks plain postgresql://, not SQLAlchemy's +asyncpg dialect suffix.
    dsn = raw.replace("postgresql+asyncpg://", "postgresql://", 1)
    parts = urlsplit(dsn)
    dbname = parts.path.lstrip("/")
    if not dbname:
        sys.exit(f"DATABASE_URL has no database name: {raw!r}")
    admin_dsn = urlunsplit(parts._replace(path="/postgres"))

    conn = await asyncpg.connect(admin_dsn)
    try:
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", dbname)
        if exists:
            print(f"[e2e] database {dbname!r} already exists")
            return
        # Database names can't be bind parameters in CREATE DATABASE; dbname
        # came from our own DATABASE_URL, not from a request or a user.
        await conn.execute(f'CREATE DATABASE "{dbname}"')
        print(f"[e2e] created database {dbname!r}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
