"""
`python -m app.migrate` — a rollout's schema step, on its own.

App pods started with DB_MIGRATE_ON_BOOT=false only check that the database
is at the Alembic head their image carries (app.database.assert_schema_current).
This is what gets it there, run once as a Kubernetes Job before any new pod
starts: the same init_db() a boot runs — the boot steps, Alembic, the seeds —
but strict, so a migration that fails fails the Job (exit 1) and the rollout
never begins, instead of a pod coming up green on a schema it cannot use.
"""
import asyncio
import sys

from loguru import logger


async def main() -> int:
    # app.main, for the same logging setup (redaction included) and the same
    # refusal of a published SECRET_KEY a boot makes. Importing it builds the
    # app object and nothing more; nothing is served and no loop starts.
    from app.main import _refuse_default_secrets
    from app.database import init_db, close_db

    try:
        await _refuse_default_secrets()
        await init_db(strict=True)
    except Exception as e:
        logger.opt(exception=e).critical(f"migration failed: {type(e).__name__}: {e}")
        return 1
    finally:
        await close_db()
    logger.info("migration complete — the schema is at this image's Alembic head")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
