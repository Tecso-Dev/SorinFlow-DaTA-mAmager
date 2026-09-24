"""
Alembic environment — async, and reusable from inside the running app.

Two ways in:
  * the CLI (`alembic upgrade head`, `alembic revision --autogenerate`):
    an async engine is built from DATABASE_URL via app.config;
  * the app at boot (app.database._alembic_sync): it hands over the
    connection it already holds in config.attributes["connection"], so the
    migration runs under the same lock_timeout guard as everything else.

target_metadata is every model, imported the same way init_db() imports
them, so autogenerate sees the whole schema.
"""
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.database import Base
# every model module, so Base.metadata is complete (mirrors init_db)
from app.models import (property, cookie, scraping_job, lead, user,   # noqa: F401
                        crm_models, app_setting, portal, email_log,
                        sms_log, forwarder, scrape_schedule, ai_usage, ai_chat)

config = context.config
if config.config_file_name is not None and not config.attributes.get("connection"):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _include_object(object, name, type_, reflected, compare_to):
    """GIN trigram indexes (migration 0012) exist only in Postgres and only
    through that migration — never on the models, because a fresh database
    builds from the models before Alembic ever runs, and that call is not
    guarded the way a migration step is (see 0012's docstring). Without this
    filter, autogenerate would see them in the migrated schema, find no
    model behind them, and flag every one as drift to be dropped.
    """
    if type_ == "index" and name and name.endswith("_trgm"):
        return False
    return True


def _configure(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # type changes are real changes on this project (JSON columns, wider
        # strings); server defaults are not worth a migration by themselves
        compare_type=True,
        compare_server_default=False,
        render_as_batch=connection.dialect.name == "sqlite",
        include_object=_include_object,
    )


def run_migrations_offline() -> None:
    context.configure(url=get_settings().database_url, target_metadata=target_metadata,
                      literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def _run_sync(connection) -> None:
    _configure(connection)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async() -> None:
    engine = create_async_engine(get_settings().database_url, poolclass=None)
    async with engine.connect() as conn:
        await conn.run_sync(_run_sync)
    await engine.dispose()


def run_migrations_online() -> None:
    conn = config.attributes.get("connection")
    if conn is not None:
        _run_sync(conn)           # the app's own connection
    else:
        asyncio.run(_run_async())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
