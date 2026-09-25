"""
SorinFlow Divar Scraper - Database Connection
"""
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import inspect, text
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool, AsyncAdaptedQueuePool
import redis.asyncio as redis
from typing import AsyncGenerator
from app.config import get_settings

settings = get_settings()

# Create async engine.
#
# NullPool was the choice from the project's first commit, with no comment
# and no bug tied to it in history (git log -S NullPool) — the honest read is
# that it was never a deliberate fix, just the safe-by-construction option: it
# opens a fresh DBAPI connection per checkout and drops it right after, so it
# can never hand a pooled asyncpg connection to a different event loop than
# the one that opened it. asyncpg connections are loop-bound; using one from
# another loop fails with "attached to a different loop" or "another
# operation is in progress".
#
# In the running app that risk does not exist — one uvicorn worker
# (Dockerfile), one process, one event loop, and every background job runs as
# asyncio.create_task() on it (app/main.py), never asyncio.run() or a second
# loop. It only bites where a NEW loop can appear inside the SAME process:
# pytest-asyncio hands every test function its own loop, and at least one
# test mixes in a third loop of its own (asyncio.run() on top of a
# TestClient's loop) — see tests/conftest.py, which is why the suite defaults
# DB_POOL_SIZE to 0 rather than fighting that pattern from here. The one-shot
# CLI scripts (scripts/*.py, app/services/dr_backup.py) each call
# asyncio.run() exactly once per process and exit, so they never see a second
# loop either. alembic's own run (migrations/env.py) opens a throwaway engine
# of its own, not this one.
#
# DB_POOL_SIZE=0 keeps NullPool — an honest escape hatch, not a hidden mode,
# for any other process that turns out to violate the one-loop assumption.
def _pool_kwargs_for(pool_size: int, max_overflow: int) -> dict:
    """The create_async_engine() pooling kwargs for a given DB_POOL_SIZE.

    poolclass is explicit (AsyncAdaptedQueuePool) rather than left to dialect
    defaults when pooling: a file-backed sqlite+aiosqlite URL — what most of
    the test suite uses — defaults to NullPool on its own, and NullPool
    rejects pool_size/max_overflow/pool_timeout outright, so leaving
    poolclass unset broke every test module at import time the moment
    DB_POOL_SIZE was not 0.
    """
    if pool_size == 0:
        return {"poolclass": NullPool}
    return {
        "poolclass": AsyncAdaptedQueuePool,
        "pool_size": pool_size,
        "max_overflow": max_overflow,
        "pool_pre_ping": True,    # a connection Postgres closed while idle fails fast, not mid-query
        "pool_recycle": 1800,     # stay under any load balancer / firewall idle-close window
        "pool_timeout": 30,
    }


engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    future=True,
    **_pool_kwargs_for(settings.db_pool_size, settings.db_max_overflow),
)

# Create async session factory
async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False
)


class Base(DeclarativeBase):
    """Base class for all models"""
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency to get database session"""
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# Redis connection
redis_client = None


async def get_redis() -> redis.Redis:
    """Get Redis client connection"""
    global redis_client
    if redis_client is None:
        redis_client = redis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True
        )
    return redis_client


async def close_redis():
    """Close Redis connection"""
    global redis_client
    if redis_client:
        await redis_client.close()
        redis_client = None


async def init_db(strict: bool = False):
    """Create tables, apply migrations, seed the first accounts.

    strict is `python -m app.migrate` (app/migrate.py), the step a rollout
    runs once before any new pod starts: there an Alembic failure raises
    instead of being printed, and the result is checked against the head, so
    the Job fails and the rollout never begins. A boot (strict=False) keeps
    going as it always has, because a pod that will not start is a worse
    outage than one skipped migration.

    Each migration runs in **its own transaction**. Sharing one was the cause
    of the 65048fc deploy failure, and the mechanism is worth spelling out
    because it is not obvious:

      * Postgres aborts an entire transaction on the first failed statement.
      * Every migration below swallows its own exception, which reads as
        "carry on regardless" — but the transaction is already poisoned, so
        every later statement silently becomes "current transaction is
        aborted", and on exit the whole block rolls back.
      * That rollback includes `create_all`. One unrelated migration failing
        therefore undid the table creation as well, and the pod came up
        against a database missing columns the models read on every query.

    Isolating them costs a handful of short transactions at boot and means a
    single failing migration is exactly that — one skipped step, logged, with
    everything else applied.
    """
    from app.models import (property, cookie, scraping_job, lead, user,
                            crm_models, app_setting, portal, email_log,
                            sms_log, forwarder, scrape_schedule, ai_usage, ai_chat,
                            telegram_link, audit_event)

    # Whether this database existed before this boot decides what Alembic is
    # told below: a fresh one IS the models (stamp head); an established one
    # is brought to the baseline by the steps below, then upgraded.
    async with engine.begin() as conn:
        await _guard(conn)
        fresh = not await conn.run_sync(lambda c: inspect(c).has_table("users"))
        await conn.run_sync(Base.metadata.create_all)

    # Every _migrate_* step below is pre-Alembic DDL: it exists to bring a
    # database up to the baseline
    # Alembic takes over from, and a database Alembic has stamped is past
    # that baseline for good. Its `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`
    # still takes ACCESS EXCLUSIVE even though every column already exists —
    # and app/migrate.py runs init_db() against the live database on every
    # deploy, so each lock queues behind whatever else is running (a plain
    # SELECT once waited 4.5 s behind one; the AI loops hold a transaction
    # across an LLM call). A stamped database therefore skips them whatever
    # its revision — including one behind this image, which is every deploy
    # that brings a migration — except the safety nets below: the steps that
    # mirror a post-Alembic revision (0009, 0010, 0012, 0013, 0015), because
    # a stamp can be wrong or a revision's failure only logged (see their
    # docstrings). They ask the catalog first — no lock when there is nothing
    # to add — except the cookie one, on a small table. A fresh or
    # pre-Alembic database runs them all, as before.
    revision_safety_nets = {_migrate_cookie_is_enabled, _migrate_users_totp_last_step,
                            _migrate_phone_normalized, _migrate_properties_ai_pipeline,
                            _migrate_portal_need_enrich}
    async with engine.begin() as conn:
        await _guard(conn)
        stamped = await _is_alembic_stamped(conn)

    # Order still matters where one migration depends on another's columns;
    # it is preserved. What changed is the blast radius when one fails, and
    # that a stamped database now skips every _migrate_* step outright.
    for step in (_migrate_users_totp,
                 _migrate_users_totp_last_step,
                 _migrate_users_divar_phone,
                 _migrate_scraping_jobs_divar_phone,
                 _migrate_properties_owner_phone,
                 _migrate_dpa_activities,
                 _migrate_lead_form_v2,
                 _migrate_property_serial,
                 _migrate_property_corner,
                 _migrate_calendar_sms,
                 _migrate_proxy_exit,
                 _migrate_cookie_challenged_at,
                 _migrate_customer_criteria,
                 _migrate_filing,
                 _migrate_advertiser_type,
                 _migrate_advertiser_signals,
                 _migrate_job_resume,
                 _migrate_contact_channel,
                 _migrate_cookie_owner,
                 _migrate_identity_required,
                 _migrate_cookie_is_enabled,
                 _backfill_cookie_owner,
                 _backfill_forwarder_permission,
                 _migrate_profile,
                 _migrate_call_queue,
                 _migrate_forwarder_sim2,
                 _backfill_advertiser_signals,
                 _migrate_cookie_usage,
                 _migrate_property_quality,
                 _migrate_job_finish_reason,
                 _migrate_price_history,
                 _migrate_image_hashes,
                 _migrate_sms_panel,
                 _migrate_portal_need_enrich,
                 _migrate_properties_ai_pipeline,
                 _migrate_phone_normalized,
                 _backfill_ai_pipeline_fingerprints,
                 _seed_reference_data,
                 _backfill_owner_ids):
        if stamped and step.__name__.startswith("_migrate_") \
                and step not in revision_safety_nets:
            continue
        try:
            async with engine.begin() as conn:
                await _guard(conn)
                await step(conn)
        except Exception as e:
            # The step already swallows its own errors; this catches the ones
            # it cannot — a lock timeout on the very first statement, or a
            # failure while committing.
            print(f"{step.__name__} skipped: {e}")

    # Every boot, stamped or not, like the revision safety nets above: every
    # login reads these columns, _verify_auth_v2 below refuses to start
    # without them, and the step asks the catalog first — no lock when they
    # are already there.
    async with engine.begin() as conn:
        await _guard(conn)
        await _migrate_auth_v2(conn)

    # From here on, schema changes are Alembic revisions (migrations/versions):
    # the steps above bring an old database to the baseline, this applies
    # everything after it. Same rule as the steps: a failure is logged and
    # the pod still comes up, because the columns a route needs are checked
    # by _verify_auth_v2 below, not assumed.
    try:
        await _alembic_sync(fresh)
    except Exception as e:
        if strict:
            raise
        print(f"alembic skipped: {e}")

    # A clean transaction for the check, so it reads the real schema rather
    # than inheriting the wreckage of a failed migration and mis-reporting why.
    async with engine.begin() as conn:
        await _verify_auth_v2(conn)
    if strict:
        # What the app pods will check before they serve: said here, the Job
        # fails with the reason instead of every new pod refusing to start.
        await assert_schema_current()

    # Seeding creates the *first* accounts. On an established database both are
    # no-ops, so a failure here — a lock timeout, a transient database blip —
    # must not stop a pod that is otherwise ready to serve. _verify_auth_v2
    # above is the check that is allowed to refuse to start; this is not.
    for seed in (_seed_super_admin, _seed_root):
        try:
            await seed()
        except Exception as e:
            print(f"{seed.__name__} skipped: {e}")


async def _is_alembic_stamped(conn) -> bool:
    """True once Alembic has recorded any revision for this database: it is
    past the baseline the pre-Alembic steps in init_db() bring an old
    database to, so they have nothing left to do (the one exception,
    _migrate_cookie_is_enabled, is kept by the caller). With no
    alembic_version row — fresh, or pre-Alembic — it is not stamped yet, and
    they all run: a fresh database is only stamped by `_alembic_sync` further
    down, after them.

    Never lets a check meant to save a lock cost the boot instead: any
    failure here reads as "not stamped", same as before this existed.
    """
    try:
        cfg = _alembic_config()
        if cfg is None:
            return False

        def _current(sync_conn):
            from alembic.runtime.migration import MigrationContext
            return MigrationContext.configure(sync_conn).get_current_revision()

        current = await conn.run_sync(_current)
        return current is not None
    except Exception:
        return False


async def _alembic_sync(fresh: bool) -> None:
    """Stamp or upgrade, on the app's own guarded connection.

    fresh database  → create_all just built the models' schema = head: stamp.
    no version yet  → an established database from before Alembic: stamp the
                      baseline the boot-time steps have brought it to, then
                      upgrade to head.
    versioned, behind → upgrade to head (a no-op when nothing is newer).
    versioned, AHEAD  → a revision this image's own script directory has
                      never heard of is not behind, it is ahead: a newer
                      release's schema, reached by a rollback
                      (`kubectl rollout undo`, or the deploy script undoing a
                      failed rollout). Every migration is additive precisely
                      so that keeps working — logged and left alone, never
                      raised, the same rule assert_schema_current already
                      applies to a running pod. Without this, `python -m
                      app.migrate`'s strict mode (app/migrate.py) would fail
                      the Job trying to "upgrade" a schema already ahead of
                      it, turning a routine rollback into an outage.
    """
    from alembic import command
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    cfg = _alembic_config()
    if cfg is None:
        print("alembic skipped: alembic.ini not found")
        return
    head = _script_head(cfg)

    def _run(sync_conn):
        cfg.attributes["connection"] = sync_conn
        current = MigrationContext.configure(sync_conn).get_current_revision()
        if fresh:
            command.stamp(cfg, "head")
            print(f"alembic: fresh database stamped {head}")
        elif current is None:
            command.stamp(cfg, "0001")
            command.upgrade(cfg, "head")
            print(f"alembic: pre-alembic database stamped baseline, upgraded to {head}")
        elif current != head:
            try:
                ScriptDirectory.from_config(cfg).get_revision(current)
            except Exception:
                from loguru import logger
                logger.warning(f"alembic: database is at {current}, unknown to this "
                               f"image's {head} — ahead of it (a rollback); migrations "
                               "are additive, leaving it alone")
                return
            command.upgrade(cfg, "head")
            print(f"alembic: upgraded {current} → {head}")

    async with engine.begin() as conn:
        await _guard(conn)
        await conn.run_sync(_run)


def _alembic_config():
    """This image's Alembic config, or None when alembic.ini is not shipped."""
    from pathlib import Path
    from alembic.config import Config

    ini = Path(__file__).resolve().parent.parent / "alembic.ini"
    if not ini.exists():
        return None
    cfg = Config(str(ini))
    cfg.set_main_option("script_location", str(ini.parent / "migrations"))
    return cfg


def _script_head(cfg) -> str | None:
    from alembic.script import ScriptDirectory
    return ScriptDirectory.from_config(cfg).get_current_head()


async def assert_schema_current(eng=None) -> None:
    """Refuse to start while the database is BEHIND this image's Alembic head.

    For a pod started with DB_MIGRATE_ON_BOOT=false, which leaves the schema
    to `python -m app.migrate`. A database behind the image means the Job did
    not run or failed: serving now would fail requests on a pod that reported
    Ready, so this raises, the pod never becomes ready, and the rollout halts
    with the previous pods still serving.

    A database AHEAD of the image — at a revision this image has never heard
    of — is allowed. That is a rollback (`kubectl rollout undo`, or the deploy
    script undoing a failed rollout) onto a schema the newer release already
    migrated, and every migration here is additive precisely so that older
    code keeps working on it. Refusing it would turn a routine rollback into
    an outage: the previous image could no longer start anywhere.
    """
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory
    from loguru import logger

    cfg = _alembic_config()
    if cfg is None:
        raise RuntimeError("alembic.ini not found — cannot tell whether the schema is current")
    script = ScriptDirectory.from_config(cfg)
    head = script.get_current_head()

    def _read(sync_conn):
        if not inspect(sync_conn).has_table("users"):
            return False, None
        return True, MigrationContext.configure(sync_conn).get_current_revision()

    async with (eng or engine).begin() as conn:
        if conn.dialect.name == "postgresql":
            await _guard(conn)      # a migration holding alembic_version must not hang the boot
        has_users, current = await conn.run_sync(_read)
    if has_users and current == head:
        return
    if has_users and current is not None:
        try:
            script.get_revision(current)
        except Exception:
            logger.warning(f"database schema is at {current}, newer than this image's {head} — "
                           "a rollback onto a newer schema; migrations are additive, starting")
            return
    raise RuntimeError(
        f"database schema is at {current or 'nothing'}{'' if has_users else ' (no users table)'}, "
        f"this image needs {head} — run `python -m app.migrate` first. Refusing to start.")


async def _guard(conn):
    """Never wait indefinitely for a lock during startup.

    An ALTER TABLE needs ACCESS EXCLUSIVE. During a rolling deploy the previous
    pod is still running, and one connection left idle in transaction is enough
    to hold a conflicting lock indefinitely. The new pod then blocks here,
    never passes its readiness probe, so Kubernetes never terminates the old
    pod that is holding the lock — the deploy deadlocks and times out with the
    site pinned on the old image. That is exactly how 65048fc failed, five
    minutes of "1 old replicas are pending termination" and no other clue.

    Five seconds is far more than any of these statements needs against a free
    table, and a timeout is caught by the caller — so the pod boots and the
    migration applies on the next restart instead of taking the deploy down.

    LOCAL — this transaction only. Every caller runs inside engine.begin(),
    and with a connection pool the connection goes back to the pool after
    boot: a plain SET would ride along into ordinary requests, which would
    then give up on a lock after 5 s and on any query after 120 s.
    """
    await conn.execute(text("SET LOCAL lock_timeout = '5s'"))
    await conn.execute(text("SET LOCAL statement_timeout = '120s'"))


async def _migrate_dpa_activities(conn):
    """Idempotently add auto_activities/activities JSON columns to DPA table."""
    try:
        from sqlalchemy import text
        await conn.execute(text(
            "ALTER TABLE crm_daily_performance "
            "ADD COLUMN IF NOT EXISTS auto_activities JSON DEFAULT '{}'"))
        await conn.execute(text(
            "ALTER TABLE crm_daily_performance "
            "ADD COLUMN IF NOT EXISTS activities JSON DEFAULT '{}'"))
    except Exception as e:
        print(f"DPA activities migration skipped: {e}")


async def _migrate_property_serial(conn):
    """Add properties.serial_no and backfill existing rows from 1000 up."""
    try:
        from sqlalchemy import text
        await conn.execute(text("ALTER TABLE properties ADD COLUMN IF NOT EXISTS serial_no INTEGER"))
        # Backfill any row still missing a serial, oldest first, continuing
        # from the highest serial already handed out. Restarting at 1000 would
        # collide with existing codes, and the unique index would abort the
        # whole migration — leaving those rows without a code indefinitely.
        await conn.execute(text("""
            WITH ranked AS (
                SELECT id,
                       (SELECT COALESCE(MAX(serial_no), 999) FROM properties)
                       + ROW_NUMBER() OVER (ORDER BY id) AS s
                FROM properties WHERE serial_no IS NULL
            )
            UPDATE properties p SET serial_no = ranked.s
            FROM ranked WHERE p.id = ranked.id
        """))
        await conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_properties_serial_no ON properties (serial_no)"))
    except Exception as e:
        print(f"property serial migration skipped: {e}")


async def _migrate_sms_panel(conn):
    """Columns the «پیامک» panel adds to the CRM's existing SMS log.

    Deliberately extending crm_sms_logs rather than creating a second table:
    the CRM already writes every send there, and two histories would mean two
    places to look when someone asks whether a customer was messaged.
    """
    try:
        from sqlalchemy import text
        await conn.execute(text(
            "ALTER TABLE crm_sms_logs "
            "ADD COLUMN IF NOT EXISTS message_id VARCHAR(40), "
            "ADD COLUMN IF NOT EXISTS cost INTEGER, "
            "ADD COLUMN IF NOT EXISTS delivery_status INTEGER, "
            "ADD COLUMN IF NOT EXISTS delivery_text VARCHAR(60), "
            "ADD COLUMN IF NOT EXISTS delivery_checked_at TIMESTAMPTZ, "
            "ADD COLUMN IF NOT EXISTS sent_by VARCHAR(200), "
            "ADD COLUMN IF NOT EXISTS campaign VARCHAR(120), "
            "ADD COLUMN IF NOT EXISTS kind VARCHAR(20) DEFAULT 'manual'"))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_crm_sms_logs_message_id "
            "ON crm_sms_logs (message_id)"))
        # The panel's default view is newest-first within one campaign; without
        # this it scans the whole table once a broadcast has filled it.
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_crm_sms_logs_campaign_sent "
            "ON crm_sms_logs (campaign, sent_at DESC)"))
    except Exception as e:
        print(f"SMS panel migration skipped: {e}")


async def _migrate_properties_ai_pipeline(conn):
    """Alembic 0013's columns, also here: properties is read on every
    request, and Alembic's own failure at boot is only logged (see the
    module docstring), so a skipped 0013 would leave the reader/embedder/
    matcher's staleness queries hitting columns that do not exist yet.
    Same 8 columns, same 3 indexes (plain — the due-queries have no id
    lower bound any more, see app/ai/listing_reader.py's run_once); the
    catalog is asked first so a boot with nothing to add never queues for
    the lock.
    """
    try:
        from sqlalchemy import text
        result = await conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='properties' AND column_name='ai_content_fp' "
            "AND table_schema=current_schema()"
        ))
        if result.fetchone() is None:
            await conn.execute(text(
                "ALTER TABLE properties "
                "ADD COLUMN IF NOT EXISTS ai_content_fp VARCHAR(16), "
                "ADD COLUMN IF NOT EXISTS ai_embed_fp VARCHAR(16), "
                "ADD COLUMN IF NOT EXISTS ai_read_fp VARCHAR(16), "
                "ADD COLUMN IF NOT EXISTS ai_read_attempts INTEGER NOT NULL DEFAULT 0, "
                "ADD COLUMN IF NOT EXISTS ai_photo_fp VARCHAR(16), "
                "ADD COLUMN IF NOT EXISTS ai_photo_attempts INTEGER NOT NULL DEFAULT 0, "
                "ADD COLUMN IF NOT EXISTS ai_matched_at TIMESTAMPTZ, "
                "ADD COLUMN IF NOT EXISTS ai_match_fp VARCHAR(16)"))
            for name, col in (("ix_properties_ai_read_at", "ai_read_at"),
                              ("ix_properties_ai_embedded_at", "ai_embedded_at"),
                              ("ix_properties_ai_matched_at", "ai_matched_at")):
                await conn.execute(text(f"CREATE INDEX IF NOT EXISTS {name} ON properties ({col})"))
    except Exception as e:
        print(f"ai pipeline migration skipped: {e}")


# Rows per UPDATE batch. Unlike _ADVERTISER_BACKFILL_BATCH this one does not
# stop after a batch: every row must be done before the loops start (below).
_AI_FP_BACKFILL_BATCH = 2000
# The ceiling on one boot's share. A few thousand rows take seconds; a table
# far past that finishes on the next boot — and says so in the log.
_AI_FP_BACKFILL_SECONDS = 120


async def _backfill_ai_pipeline_fingerprints(conn):
    """ai_content_fp for rows the ORM event listener never touched (every
    row that existed before this deploy), and — the part that actually
    matters — the per-stage "fp at last pass" columns for whatever each
    stage had ALREADY finished, so nothing already read, embedded or judged
    looks freshly stale the moment this lands.

    All of the table, not one batch per boot: the listener stamps
    ai_content_fp on ANY write to a row, so a row left for a later boot
    turns stale the first time anything touches it — the re-embed this
    release starts writes to every row — and the reader re-reads it (money)
    and the engine re-judges and re-announces it (Telegram). Batches keep
    each statement small; the time ceiling keeps a huge table from holding
    the boot, and the rest converges on the next one.
    """
    try:
        import json
        import time
        from datetime import datetime, timezone
        from sqlalchemy import text
        from app.models.property import content_fingerprint
        # plain ints from the AI modules, never a DB call
        from app.ai.listing_reader import PROMPT_VERSION as READER_VERSION
        from app.ai.embeddings import EMBED_VERSION

        cursor_row = (await conn.execute(text(
            "SELECT value FROM app_settings WHERE key = 'match_engine_cursor'"))).first()
        try:
            match_cursor = int(cursor_row[0]) if cursor_row and cursor_row[0] else None
        except (TypeError, ValueError):
            match_cursor = None

        update = text(
            "UPDATE properties SET ai_content_fp = :fp, "
            "ai_read_fp = CASE WHEN :read THEN :fp ELSE ai_read_fp END, "
            "ai_embed_fp = CASE WHEN :embed THEN :fp ELSE ai_embed_fp END, "
            "ai_matched_at = CASE WHEN :matched THEN COALESCE(ai_matched_at, :now) ELSE ai_matched_at END, "
            "ai_match_fp = CASE WHEN :matched THEN :fp ELSE ai_match_fp END "
            "WHERE id = :i")
        now = datetime.now(timezone.utc)
        deadline = time.monotonic() + _AI_FP_BACKFILL_SECONDS
        done, last_id = 0, 0
        while time.monotonic() < deadline:
            rows = (await conn.execute(text(
                "SELECT id, title, description, property_type, category_name, listing_type, "
                "area, rooms, floor, total_floors, year_built, district, neighborhood, city_name, "
                "has_elevator, has_parking, has_storage, has_balcony, document_type, unit_status, "
                "corner_type, frontage, building_direction, "
                "ai_read_at, ai_facts, ai_embed_version, ai_embedded_at "
                "FROM properties WHERE ai_content_fp IS NULL AND id > :after ORDER BY id LIMIT :n"
            ), {"after": last_id, "n": _AI_FP_BACKFILL_BATCH})).all()
            if not rows:
                break
            batch = []
            for r in rows:
                fp = content_fingerprint(r)
                # a JSON column through a raw text() query can come back as
                # the string itself, depending on the driver's codecs
                facts = r.ai_facts
                if isinstance(facts, str):
                    try:
                        facts = json.loads(facts)
                    except ValueError:
                        facts = None
                already_read = r.ai_read_at is not None and isinstance(facts, dict) \
                    and facts.get("prompt_version") == READER_VERSION
                already_embedded = r.ai_embedded_at is not None and r.ai_embed_version == EMBED_VERSION
                already_matched = match_cursor is not None and r.id <= match_cursor
                batch.append({"fp": fp, "read": already_read, "embed": already_embedded,
                              "matched": already_matched, "now": now, "i": r.id})
            await conn.execute(update, batch)
            done += len(batch)
            last_id = rows[-1].id
        if done:
            left = (await conn.execute(text(
                "SELECT count(*) FROM properties WHERE ai_content_fp IS NULL"))).scalar()
            print(f"ai pipeline backfill: {done} rows fingerprinted, {left} left for the next boot "
                  f"(match cursor {match_cursor if match_cursor is not None else 'unknown'})")
    except Exception as e:
        print(f"ai pipeline backfill skipped: {e}")


async def _migrate_phone_normalized(conn):
    """The normalized companion column for every phone that is looked up or
    deduped: leads.phone_number, properties.phone_number, crm_contacts.phone,
    crm_customers.mobile1/2. See app/models/phone.py for the normalization
    and the ORM event that fills it on every write from here on.

    No backfill here — Alembic 0012 does that once, in batches. This only
    guards the column and its index existing, because every one of these
    tables is read on every request and a route must not 500 for a column
    Alembic failed to add.
    """
    try:
        from sqlalchemy import text
        # asked first: ALTER TABLE takes its lock before IF NOT EXISTS is
        # checked, and these are the four busiest tables — every boot after
        # the first would queue for them for nothing
        done = (await conn.execute(text(
            "SELECT 1 FROM information_schema.columns WHERE table_name='crm_customers' "
            "AND column_name='mobile2_normalized' AND table_schema=current_schema()"))).first()
        if done:
            return
        await conn.execute(text(
            "ALTER TABLE leads ADD COLUMN IF NOT EXISTS phone_number_normalized VARCHAR(20)"))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_leads_phone_number_normalized "
            "ON leads (phone_number_normalized)"))
        await conn.execute(text(
            "ALTER TABLE properties ADD COLUMN IF NOT EXISTS phone_number_normalized VARCHAR(20)"))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_properties_phone_number_normalized "
            "ON properties (phone_number_normalized)"))
        await conn.execute(text(
            "ALTER TABLE crm_contacts ADD COLUMN IF NOT EXISTS phone_normalized VARCHAR(20)"))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_crm_contacts_phone_normalized "
            "ON crm_contacts (phone_normalized)"))
        await conn.execute(text(
            "ALTER TABLE crm_customers "
            "ADD COLUMN IF NOT EXISTS mobile1_normalized VARCHAR(20), "
            "ADD COLUMN IF NOT EXISTS mobile2_normalized VARCHAR(20)"))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_crm_customers_mobile1_normalized "
            "ON crm_customers (mobile1_normalized)"))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_crm_customers_mobile2_normalized "
            "ON crm_customers (mobile2_normalized)"))
    except Exception as e:
        print(f"phone normalization migration skipped: {e}")


async def _migrate_job_resume(conn):
    """What a run needs in order to be continued: its own settings, and the
    run it continues. Rows from before land NULL and the resume endpoint says
    so rather than inventing a config."""
    try:
        from sqlalchemy import text
        await conn.execute(text(
            "ALTER TABLE scraping_jobs "
            "ADD COLUMN IF NOT EXISTS config JSON, "
            "ADD COLUMN IF NOT EXISTS resumed_from UUID, "
            "ADD COLUMN IF NOT EXISTS divar_count INTEGER"))
    except Exception as e:
        print(f"job resume migration skipped: {e}")


async def _migrate_contact_channel(conn):
    """How each listing's contact reveal ended.

    NULL on existing rows and left that way: a row with no phone from before
    this existed could be either kind, and guessing «chat_only» would stop
    the retry that might fill it. The next visit records the truth.
    """
    try:
        from sqlalchemy import text
        await conn.execute(text(
            "ALTER TABLE properties ADD COLUMN IF NOT EXISTS contact_channel VARCHAR(16)"))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_properties_contact_channel "
            "ON properties (contact_channel)"))
    except Exception as e:
        print(f"contact channel migration skipped: {e}")


async def _migrate_identity_required(conn):
    """When Divar asked a stored account to verify its identity."""
    try:
        from sqlalchemy import text
        await conn.execute(text(
            "ALTER TABLE cookies ADD COLUMN IF NOT EXISTS identity_required_at TIMESTAMPTZ"))
    except Exception as e:
        print(f"identity_required migration skipped: {e}")


async def _migrate_cookie_is_enabled(conn):
    """The owner's on/off switch for a Divar number (Alembic 0009 adds it too).

    A second, unpushed revision «0009» once added `cookies.enabled` instead.
    Had it reached production, Alembic would have recorded 0009 as applied,
    never added is_enabled, and every cookies query would have failed with
    /health still green. Adding it here as well makes that harmless.
    """
    try:
        from sqlalchemy import text
        await conn.execute(text(
            "ALTER TABLE cookies ADD COLUMN IF NOT EXISTS is_enabled BOOLEAN NOT NULL DEFAULT TRUE"))
    except Exception as e:
        print(f"cookie is_enabled migration skipped: {e}")


async def _migrate_cookie_owner(conn):
    """Whose Divar number each stored session is.

    Nullable in the schema only because a column cannot be added NOT NULL to a
    table that already has rows. _backfill_cookie_owner below gives every
    existing row an owner, and the endpoints refuse to create one without.
    """
    try:
        from sqlalchemy import text
        await conn.execute(text(
            "ALTER TABLE cookies ADD COLUMN IF NOT EXISTS owner_user_id INTEGER"))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_cookies_owner_user_id "
            "ON cookies (owner_user_id)"))
        await conn.execute(text("""
            DO $$ BEGIN
                ALTER TABLE cookies ADD CONSTRAINT fk_cookies_owner
                    FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE SET NULL;
            EXCEPTION WHEN duplicate_object OR undefined_table THEN NULL;
            END $$;
        """))
    except Exception as e:
        print(f"cookie owner migration skipped: {e}")


async def _backfill_cookie_owner(conn):
    """Give the sessions that predate ownership an owner.

    Two passes, in this order and for this reason:

      1. users.divar_phone already records «this number is mine», which is the
         very statement the column is for. Matching on it attributes each
         session to the person who said so.
      2. whatever is left was set up before anybody said, and on this install
         that was the super admin. Leaving it NULL instead would make those
         sessions invisible to every list — a scraper pool that silently
         empties is worse than an attribution somebody can correct.

    Both are guarded by `owner_user_id IS NULL`, so a row assigned once is
    never reassigned by a later boot.
    """
    try:
        from sqlalchemy import text
        by_phone = await conn.execute(text("""
            UPDATE cookies c SET owner_user_id = u.id
            FROM users u
            WHERE c.owner_user_id IS NULL
              AND u.divar_phone IS NOT NULL
              AND regexp_replace(u.divar_phone, '\\D', '', 'g')
                = regexp_replace(c.phone_number, '\\D', '', 'g')
        """))
        rest = await conn.execute(text("""
            UPDATE cookies SET owner_user_id = (
                SELECT id FROM users
                 WHERE role IN ('root', 'super_admin')
                 ORDER BY id ASC LIMIT 1
            )
            WHERE owner_user_id IS NULL
              AND EXISTS (SELECT 1 FROM users WHERE role IN ('root', 'super_admin'))
        """))
        if by_phone.rowcount or rest.rowcount:
            print(f"cookie owner backfill: {by_phone.rowcount or 0} by divar_phone, "
                  f"{rest.rowcount or 0} to the super admin")
    except Exception as e:
        print(f"cookie owner backfill skipped: {e}")


async def _backfill_owner_ids(conn):
    """Owned rows the previous release wrote carry a name and no account.

    During a rolling deploy (and after a rollback) the old pods keep
    assigning leads, tasks and customers by name. Alembic 0016 resolved
    everything before it; this resolves what came after — only rows whose
    name changed since their account was last resolved, never a row already
    judged ownerless (see OWNERSHIP in app/auth/visibility.py). Before 0016
    has added the columns it has nothing to do. Plain SQL, both dialects.
    """
    try:
        from app.auth.visibility import backfill_owner_ids
        touched = await conn.run_sync(backfill_owner_ids)
        if touched:
            print(f"owner accounts resolved for {touched} row(s) written by name")
    except Exception as e:
        print(f"owner account backfill skipped: {e}")


async def _migrate_forwarder_sim2(conn):
    """A dual-SIM phone: the second number on the device row (2026-09-20)."""
    try:
        from sqlalchemy import text
        await conn.execute(text(
            "ALTER TABLE forwarder_devices ADD COLUMN IF NOT EXISTS sim_phone2 VARCHAR(20)"))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_forwarder_devices_sim_phone2 ON forwarder_devices (sim_phone2)"))
    except Exception as e:
        print(f"forwarder sim2 migration skipped: {e}")


async def _migrate_call_queue(conn):
    """The call queue on leads: when to dial next, how many times it has
    been dialled, what the phone said last."""
    try:
        from sqlalchemy import text
        await conn.execute(text(
            "ALTER TABLE leads "
            "ADD COLUMN IF NOT EXISTS next_call_at TIMESTAMPTZ, "
            "ADD COLUMN IF NOT EXISTS call_attempts INTEGER NOT NULL DEFAULT 0, "
            "ADD COLUMN IF NOT EXISTS last_call_at TIMESTAMPTZ, "
            "ADD COLUMN IF NOT EXISTS last_call_outcome VARCHAR(20)"))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_leads_next_call_at ON leads (next_call_at)"))
    except Exception as e:
        print(f"call queue migration skipped: {e}")


async def _migrate_profile(conn):
    """The profile page: who the person is, and the version number that lets
    a password change sign their other devices out."""
    try:
        from sqlalchemy import text
        await conn.execute(text(
            "ALTER TABLE users "
            "ADD COLUMN IF NOT EXISTS headline VARCHAR(120), "
            "ADD COLUMN IF NOT EXISTS bio TEXT, "
            "ADD COLUMN IF NOT EXISTS links JSON DEFAULT '{}', "
            "ADD COLUMN IF NOT EXISTS presence VARCHAR(16) NOT NULL DEFAULT 'available', "
            "ADD COLUMN IF NOT EXISTS avatar_token VARCHAR(32), "
            "ADD COLUMN IF NOT EXISTS token_version INTEGER NOT NULL DEFAULT 0"))
    except Exception as e:
        print(f"profile migration skipped: {e}")


async def _backfill_forwarder_permission(conn):
    """«فرستندهٔ پیامک» used to ride on «حساب‌های دیوار». Now that it is its
    own key, every admin who could open it yesterday gets it today — once.

    Once, and not «whenever missing»: a super_admin who later takes the key
    away must not find it back after the next restart. The marker row in
    app_settings is what makes the second boot a no-op.
    """
    try:
        from sqlalchemy import text
        marker = await conn.execute(text("""
            INSERT INTO app_settings (key, value, updated_by)
            VALUES ('migration:forwarder_permission', 'done', 'startup')
            ON CONFLICT (key) DO NOTHING
        """))
        if not marker.rowcount:
            return
        res = await conn.execute(text("""
            UPDATE users
               SET permissions = (permissions::jsonb || '["forwarder"]'::jsonb)::json
             WHERE role = 'admin'
               AND permissions IS NOT NULL
               AND jsonb_typeof(permissions::jsonb) = 'array'
               AND permissions::jsonb ? 'divar_auth'
               AND NOT permissions::jsonb ? 'forwarder'
        """))
        if res.rowcount:
            print(f"forwarder permission backfilled for {res.rowcount} admin(s)")
    except Exception as e:
        print(f"forwarder permission backfill skipped: {e}")


async def _migrate_advertiser_signals(conn):
    """What the ad's own words say about who posted it.

    Divar's own declaration already has a column; these two sit beside it
    because Divar returns agency listings under a «شخصی» filter, and the
    disagreement is the thing worth seeing.

    Deliberately NO default, so existing rows land NULL rather than FALSE.
    NULL means «not looked at yet» and FALSE means «looked at, private», and
    the backfill below needs to tell those apart — a DEFAULT FALSE would have
    silently declared the whole archive private before anything read a word
    of it.
    """
    try:
        from sqlalchemy import text
        await conn.execute(text(
            "ALTER TABLE properties "
            "ADD COLUMN IF NOT EXISTS agency_suspected BOOLEAN, "
            "ADD COLUMN IF NOT EXISTS agency_evidence VARCHAR(100)"))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_properties_agency_suspected "
            "ON properties (agency_suspected)"))
    except Exception as e:
        print(f"advertiser signals migration skipped: {e}")


# One boot's worth. Small enough that a rollout never waits on it, large
# enough that an archive of a few thousand is done on the first restart.
_ADVERTISER_BACKFILL_BATCH = 5000


async def _backfill_advertiser_signals(conn):
    """Read the label onto the listings already stored.

    The scraper labels what it saves from here on, but the listing that
    prompted all this — «املاک هستم», filed as شخصی — was scraped weeks ago
    and is one of about twelve hundred already in the table. A label that
    only applies to future rows would leave the panel quietly saying nothing
    about the ones somebody is actually looking at.

    Pure text over rows already held: no network, no files, no Divar. Capped
    per boot and driven off `agency_suspected IS NULL`, so it converges and
    then costs one indexed count forever after.
    """
    try:
        from sqlalchemy import text
        from app.services import advertiser_signals

        rows = (await conn.execute(text(
            "SELECT id, title, description FROM properties "
            "WHERE agency_suspected IS NULL LIMIT :n"
        ), {"n": _ADVERTISER_BACKFILL_BATCH})).all()
        if not rows:
            return

        flagged = 0
        for r in rows:
            looks, phrase = advertiser_signals.detect(r.description, r.title)
            if looks:
                flagged += 1
            await conn.execute(text(
                "UPDATE properties SET agency_suspected = :s, agency_evidence = :e "
                "WHERE id = :i"
            ), {"s": looks, "e": phrase, "i": r.id})
        print(f"advertiser backfill: {len(rows)} read, {flagged} look like agencies")
    except Exception as e:
        print(f"advertiser backfill skipped: {e}")


async def _migrate_filing(conn):
    """کمد و زونکن — the filing columns on properties.

    The two tables themselves are created by create_all; only the columns
    added to the existing properties table need an ALTER.
    """
    try:
        from sqlalchemy import text
        await conn.execute(text(
            "ALTER TABLE properties "
            "ADD COLUMN IF NOT EXISTS binder_id INTEGER, "
            "ADD COLUMN IF NOT EXISTS is_pinned BOOLEAN DEFAULT FALSE, "
            "ADD COLUMN IF NOT EXISTS is_archived BOOLEAN DEFAULT FALSE, "
            "ADD COLUMN IF NOT EXISTS is_private BOOLEAN DEFAULT FALSE, "
            "ADD COLUMN IF NOT EXISTS is_draft BOOLEAN DEFAULT FALSE, "
            "ADD COLUMN IF NOT EXISTS created_by VARCHAR(200), "
            "ADD COLUMN IF NOT EXISTS tags VARCHAR(500)"))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_properties_binder_id ON properties (binder_id)"))
        # added after the table, so it survives a database that predates it
        await conn.execute(text("""
            DO $$ BEGIN
                ALTER TABLE properties ADD CONSTRAINT fk_properties_binder
                    FOREIGN KEY (binder_id) REFERENCES crm_binders(id) ON DELETE SET NULL;
            EXCEPTION WHEN duplicate_object OR undefined_table THEN NULL;
            END $$;
        """))
        # پوشه — a binder inside a binder (2026-09-20). The table predates the
        # column on every database that matters, so create_all cannot add it.
        await conn.execute(text(
            "ALTER TABLE crm_binders ADD COLUMN IF NOT EXISTS parent_id INTEGER"))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_crm_binders_parent_id ON crm_binders (parent_id)"))
        await conn.execute(text("""
            DO $$ BEGIN
                ALTER TABLE crm_binders ADD CONSTRAINT fk_crm_binders_parent
                    FOREIGN KEY (parent_id) REFERENCES crm_binders(id) ON DELETE CASCADE;
            EXCEPTION WHEN duplicate_object OR undefined_table THEN NULL;
            END $$;
        """))
    except Exception as e:
        print(f"filing migration skipped: {e}")


async def _migrate_customer_criteria(conn):
    """Explicit search criteria on the customer intake form.

    Existing rows are left with NULLs on purpose: the matcher falls back to
    reading intent from the free-text fields, so nothing stops working until
    someone opens the customer and fills them in.
    """
    try:
        from sqlalchemy import text
        await conn.execute(text(
            "ALTER TABLE crm_customers "
            "ADD COLUMN IF NOT EXISTS desired_city VARCHAR(100), "
            "ADD COLUMN IF NOT EXISTS desired_type VARCHAR(20), "
            "ADD COLUMN IF NOT EXISTS deal_type VARCHAR(10) DEFAULT 'buy'"))
    except Exception as e:
        print(f"customer criteria migration skipped: {e}")


async def _migrate_calendar_sms(conn):
    """SMS-reminder columns, plus the split of the single attendee into the
    three sides of an appointment (مالک / مشتری / کارشناس فروش)."""
    try:
        from sqlalchemy import text
        await conn.execute(text(
            "ALTER TABLE crm_calendar_events "
            "ADD COLUMN IF NOT EXISTS sms_reminder BOOLEAN DEFAULT FALSE, "
            "ADD COLUMN IF NOT EXISTS sms_sent BOOLEAN DEFAULT FALSE, "
            "ADD COLUMN IF NOT EXISTS owner_name VARCHAR(200), "
            "ADD COLUMN IF NOT EXISTS owner_phone VARCHAR(20), "
            "ADD COLUMN IF NOT EXISTS customer_name VARCHAR(200), "
            "ADD COLUMN IF NOT EXISTS customer_phone VARCHAR(20), "
            "ADD COLUMN IF NOT EXISTS agent_phone VARCHAR(20)"))
        # rows written before the split kept both sides in attendee_*
        await conn.execute(text(
            "UPDATE crm_calendar_events "
            "SET owner_name = COALESCE(owner_name, attendee_name), "
            "    owner_phone = COALESCE(owner_phone, attendee_phone) "
            "WHERE owner_phone IS NULL AND attendee_phone IS NOT NULL"))
    except Exception as e:
        print(f"calendar sms migration skipped: {e}")


async def _migrate_property_corner(conn):
    """Add properties.corner_type and recover it from already-scraped ad text.

    Divar has no «نبش» field, so it only ever appears in the title, the
    description or a feature chip. Old rows are backfilled with the same
    detector the scraper now runs, which is why this scans instead of just
    adding the column. Rows whose «نبش» turns out to be part of an address
    stay NULL and get re-checked on the next boot — a cheap re-read of a
    small subset, and self-healing if the detector improves.
    """
    try:
        from sqlalchemy import text
        from app.scraper.parsers import detect_corner_type
        await conn.execute(text(
            "ALTER TABLE properties ADD COLUMN IF NOT EXISTS corner_type VARCHAR(20)"))
        rows = (await conn.execute(text(
            "SELECT id, title, description FROM properties "
            "WHERE corner_type IS NULL "
            "AND (title LIKE '%نبش%' OR description LIKE '%نبش%')"
        ))).all()
        found = 0
        for r in rows:
            corner = detect_corner_type(r.title, r.description)
            if corner:
                await conn.execute(
                    text("UPDATE properties SET corner_type = :c WHERE id = :i"),
                    {"c": corner, "i": r.id})
                found += 1
        if rows:
            print(f"corner_type backfill: {found}/{len(rows)} rows mentioning نبش matched")
    except Exception as e:
        print(f"property corner migration skipped: {e}")


async def _seed_reference_data(conn):
    """Make sure every city and category the panel offers exists as a row.

    The dropdowns are built from CITIES/CATEGORIES in app/config.py, but these
    tables were only ever populated by the database's own init script, which
    seeds a much shorter list — 20 of 174 cities and 7 of 17 categories in the
    deployed one. Anything missing had two consequences: a scrape in that city
    or category saved its job with a NULL foreign key, so the dashboard showed
    «—» for it, and filtering the job list by that category could not resolve
    a row to filter on.

    is_active is set explicitly rather than left to the column default: the
    model declares default=True on the Python side only, so a table built by
    create_all() has no server default and these rows would land NULL — and
    /properties/cities/list filters on is_active == True, which would hide
    exactly the rows this is adding.

    Idempotent and cheap: two multi-row INSERTs with a bare ON CONFLICT DO
    NOTHING — untargeted on purpose, so it also absorbs a clash on cities.name,
    which the init script declares UNIQUE but the model does not. A conflict
    that raised here would abort the transaction every other migration in
    init_db() shares.
    This runs inside startup and the readiness probe is waiting on it, so it
    stays at two statements no matter how long the lists get.
    """
    try:
        from sqlalchemy import text
        from app.config import CITIES, CATEGORIES

        if CITIES:
            values, params = [], {}
            for i, (slug, info) in enumerate(CITIES.items()):
                values.append(f"(:cn{i}, :cs{i}, :cp{i}, TRUE)")
                params[f"cn{i}"] = info.get("name")
                params[f"cs{i}"] = slug
                params[f"cp{i}"] = info.get("province")
            await conn.execute(text(
                "INSERT INTO cities (name, slug, province, is_active) VALUES "
                + ", ".join(values)
                + " ON CONFLICT DO NOTHING"), params)

        if CATEGORIES:
            values, params = [], {}
            for i, (slug, info) in enumerate(CATEGORIES.items()):
                values.append(f"(:gn{i}, :gs{i}, :gu{i}, TRUE)")
                params[f"gn{i}"] = info.get("name")
                params[f"gs{i}"] = slug
                params[f"gu{i}"] = "/s/{city}/" + slug
            await conn.execute(text(
                "INSERT INTO categories (name, slug, url_path, is_active) VALUES "
                + ", ".join(values)
                + " ON CONFLICT DO NOTHING"), params)
    except Exception as e:
        print(f"reference data seed skipped: {e}")


async def _migrate_cookie_usage(conn):
    """Per-account reveal budget for چرخش شماره.

    Divar charges its SMS challenge to the account and remembers across our
    jobs. The scraper counted reveals on itself, and a fresh scraper is built
    per job — so the count restarted every run while the account's real spend
    kept climbing. These two columns move the count to where the spend actually
    happens.
    """
    try:
        from sqlalchemy import text
        await conn.execute(text(
            "ALTER TABLE cookies "
            "ADD COLUMN IF NOT EXISTS reveals INTEGER NOT NULL DEFAULT 0, "
            "ADD COLUMN IF NOT EXISTS last_used_at TIMESTAMPTZ, "
            # When Divar was last actually asked about this session, as opposed
            # to when we last wrote the row. is_valid without it is a belief
            # with no date on it, and the panel was showing it as fact.
            "ADD COLUMN IF NOT EXISTS last_checked_at TIMESTAMPTZ"))
    except Exception as e:
        print(f"cookie usage migration skipped: {e}")


async def _migrate_advertiser_type(conn):
    """Re-decide آژانس-vs-شخصی for rows whose posted name gives it away.

    Divar's advertiser-type row is absent on many ads and agencies routinely
    post under «شخصی», so rows landed as personal (or as nothing) that are
    plainly a shop. Anything with a name is re-run through the same detector
    the scraper now uses.

    Scraped rows before this change have no seller_name stored at all, so they
    cannot be recovered here — they get corrected the next time they are
    scraped. This fixes the rows that do carry a name.

    One statement, not a row loop. This runs inside startup, and startup is what
    the readiness probe is waiting on — a per-row UPDATE makes boot time scale
    with the table and can push a rollout past its deadline.
    """
    try:
        from sqlalchemy import text
        from app.scraper.parsers import _AGENCY_NAME_HINTS
        # the hints are module constants, but bind them anyway rather than
        # pasting Persian text into SQL
        clauses = " OR ".join(f"LOWER(seller_name) LIKE :p{i}"
                              for i in range(len(_AGENCY_NAME_HINTS)))
        params = {f"p{i}": f"%{h.lower()}%" for i, h in enumerate(_AGENCY_NAME_HINTS)}
        result = await conn.execute(text(
            "UPDATE properties SET advertiser_type = 'agency' "
            "WHERE seller_name IS NOT NULL AND seller_name <> '' "
            "AND (advertiser_type IS NULL OR advertiser_type = 'personal') "
            f"AND ({clauses})"
        ), params)
        if result.rowcount:
            print(f"advertiser_type backfill: {result.rowcount} named rows re-filed as agency")
    except Exception as e:
        print(f"advertiser type migration skipped: {e}")


async def _migrate_lead_form_v2(conn):
    """Idempotently add properties.extra_attrs and leads.rented_at."""
    try:
        from sqlalchemy import text
        await conn.execute(text(
            "ALTER TABLE properties ADD COLUMN IF NOT EXISTS extra_attrs JSON DEFAULT '{}'"))
        await conn.execute(text(
            "ALTER TABLE leads ADD COLUMN IF NOT EXISTS rented_at TIMESTAMPTZ"))
    except Exception as e:
        print(f"lead form v2 migration skipped: {e}")


async def _migrate_users_totp(conn):
    """Idempotently add totp_secret / totp_enabled columns to users table."""
    try:
        from sqlalchemy import text
        result = await conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='users' AND column_name='totp_enabled'"
        ))
        if result.fetchone() is None:
            await conn.execute(text(
                "ALTER TABLE users "
                "ADD COLUMN IF NOT EXISTS totp_secret VARCHAR(64), "
                "ADD COLUMN IF NOT EXISTS totp_enabled BOOLEAN NOT NULL DEFAULT FALSE"
            ))
    except Exception:
        pass


async def _migrate_users_totp_last_step(conn):
    """users.totp_last_step (Alembic 0010 adds it too).

    Also here because the User model selects it on every query and Alembic's
    failures at boot are only logged: a skipped 0010 would fail every login.
    The catalog is asked first, so a boot with nothing to add never queues for
    the lock.
    """
    try:
        from sqlalchemy import text
        result = await conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='users' AND column_name='totp_last_step' "
            "AND table_schema=current_schema()"
        ))
        if result.fetchone() is None:
            await conn.execute(text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS totp_last_step BIGINT"
            ))
    except Exception as e:
        print(f"totp_last_step migration skipped: {e}")


async def _migrate_users_divar_phone(conn):
    """Idempotently add divar_phone column to users table."""
    try:
        from sqlalchemy import text
        result = await conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='users' AND column_name='divar_phone'"
        ))
        if result.fetchone() is None:
            await conn.execute(text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS divar_phone VARCHAR(20)"
            ))
    except Exception:
        pass


async def _migrate_scraping_jobs_divar_phone(conn):
    """Idempotently add divar_phone column to scraping_jobs table."""
    try:
        from sqlalchemy import text
        result = await conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='scraping_jobs' AND column_name='divar_phone'"
        ))
        if result.fetchone() is None:
            await conn.execute(text(
                "ALTER TABLE scraping_jobs ADD COLUMN IF NOT EXISTS divar_phone VARCHAR(20)"
            ))
    except Exception:
        pass


async def _migrate_cookie_challenged_at(conn):
    """Idempotently add cookies.challenged_at — see Cookie.challenged_at."""
    try:
        from sqlalchemy import text
        await conn.execute(text(
            "ALTER TABLE cookies ADD COLUMN IF NOT EXISTS challenged_at TIMESTAMPTZ"))
    except Exception as e:
        from loguru import logger as _log
        _log.warning(f"[migrate] cookies.challenged_at: {e}")


async def _migrate_proxy_exit(conn):
    """Idempotently add proxies.exit_country / exit_ip / is_hosting.

    Learned on every test. A proxy that reaches Divar from Iceland passes the
    reachability check and is still the least convincing thing a visitor to an
    Iranian site can be; the panel needs to be able to say so.
    """
    try:
        from sqlalchemy import text
        await conn.execute(text(
            "ALTER TABLE proxies "
            "ADD COLUMN IF NOT EXISTS exit_country VARCHAR(2), "
            "ADD COLUMN IF NOT EXISTS exit_ip VARCHAR(45), "
            "ADD COLUMN IF NOT EXISTS is_hosting BOOLEAN"))
    except Exception as e:
        from loguru import logger as _log
        _log.warning(f"[migrate] proxies exit columns: {e}")


async def _migrate_job_finish_reason(conn):
    """Idempotently add finish_reason to scraping_jobs.

    No backfill: jobs that ran before this existed genuinely have no recorded
    reason, and NULL says that honestly rather than inventing one.
    """
    try:
        from loguru import logger as _log
        from sqlalchemy import text
        result = await conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='scraping_jobs' AND column_name='finish_reason'"
        ))
        if result.fetchone() is None:
            await conn.execute(text(
                "ALTER TABLE scraping_jobs ADD COLUMN IF NOT EXISTS finish_reason VARCHAR(300)"
            ))
            _log.info("Added finish_reason to scraping_jobs")
    except Exception:
        pass


async def _migrate_image_hashes(conn):
    """Idempotently add properties.image_hashes.

    No backfill. Hashing the images already on disk is a job for a one-off
    task, not for every pod at boot — a few thousand JPEGs opened during
    startup is a rollout that times out.
    """
    try:
        from loguru import logger as _log
        from sqlalchemy import text
        result = await conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='properties' AND column_name='image_hashes'"))
        if result.fetchone() is None:
            await conn.execute(text(
                "ALTER TABLE properties ADD COLUMN IF NOT EXISTS image_hashes JSON"))
            await conn.execute(text(
                "ALTER TABLE properties ADD COLUMN IF NOT EXISTS image_quality JSON"))
            _log.info("Added image_hashes to properties")
    except Exception:
        pass


async def _migrate_price_history(conn):
    """Idempotently add the price-trail columns to properties.

    No backfill, and none is possible: the earlier prices were overwritten as
    each listing was re-scraped and are gone. NULL here means «no move has
    been recorded since this shipped», which is the truth.
    """
    try:
        from loguru import logger as _log
        from sqlalchemy import text
        result = await conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='properties' AND column_name='price_changed_at'"
        ))
        if result.fetchone() is None:
            await conn.execute(text(
                "ALTER TABLE properties ADD COLUMN IF NOT EXISTS price_history JSON"))
            await conn.execute(text(
                "ALTER TABLE properties ADD COLUMN IF NOT EXISTS previous_price BIGINT"))
            await conn.execute(text(
                "ALTER TABLE properties ADD COLUMN IF NOT EXISTS "
                "price_changed_at TIMESTAMP WITH TIME ZONE"))
            await conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_properties_price_changed_at "
                "ON properties (price_changed_at)"))
            _log.info("Added the price-trail columns to properties")
    except Exception:
        pass


async def _migrate_portal_need_enrich(conn):
    """portal_property_requests.need_enriched_at / need_enrich_attempts
    (Alembic 0015 adds them too).

    Also here because ordinary requests (GET /portal/admin/requests, /mine)
    select the whole row: a skipped 0015 would 500 every one of them, not
    just the background enrichment pass.
    """
    try:
        from sqlalchemy import text
        result = await conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='portal_property_requests' AND column_name='need_enriched_at' "
            "AND table_schema=current_schema()"
        ))
        if result.fetchone() is None:
            await conn.execute(text(
                "ALTER TABLE portal_property_requests "
                "ADD COLUMN IF NOT EXISTS need_enriched_at TIMESTAMPTZ, "
                "ADD COLUMN IF NOT EXISTS need_enrich_attempts INTEGER NOT NULL DEFAULT 0"
            ))
            # Every request already here was read on the visitor's own request
            # (the old path) — without this the background pass would read the
            # whole history again, paying for it and refilling fields a
            # consultant emptied since. Only now, when the column is new.
            await conn.execute(text(
                "UPDATE portal_property_requests SET need_enriched_at = now() "
                "WHERE need_enriched_at IS NULL"))
    except Exception as e:
        print(f"portal need-enrich migration skipped: {e}")


async def _migrate_property_quality(conn):
    """Idempotently add the scrape-quality columns to properties.

    Added without a backfill on purpose: NULL means "scraped before anything
    checked", which is the truth and is exactly what the panel should be able
    to tell apart from "checked and fine". A backfill would also rewrite every
    existing row at boot, which is how a rollout times out.
    """
    try:
        from loguru import logger as _log
        from sqlalchemy import text
        result = await conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='properties' AND column_name='quality_score'"
        ))
        if result.fetchone() is None:
            await conn.execute(text(
                "ALTER TABLE properties ADD COLUMN IF NOT EXISTS quality_score DOUBLE PRECISION"
            ))
            await conn.execute(text(
                "ALTER TABLE properties ADD COLUMN IF NOT EXISTS quality_issues TEXT"
            ))
            await conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_properties_quality_score "
                "ON properties (quality_score)"
            ))
            _log.info("Added quality_score/quality_issues to properties")
    except Exception:
        pass


async def _migrate_properties_owner_phone(conn):
    """Idempotently add owner_phone column to properties table."""
    try:
        from sqlalchemy import text
        result = await conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='properties' AND column_name='owner_phone'"
        ))
        if result.fetchone() is None:
            await conn.execute(text(
                "ALTER TABLE properties ADD COLUMN IF NOT EXISTS owner_phone VARCHAR(20)"
            ))
            await conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_properties_owner_phone ON properties (owner_phone)"
            ))
            await conn.execute(text(
                "ALTER TABLE properties ADD COLUMN IF NOT EXISTS advertiser_type VARCHAR(20)"
            ))
    except Exception:
        pass


async def _seed_super_admin():
    """Create the default super_admin account if no users exist."""
    from app.models.user import User
    from app.auth.jwt import get_password_hash
    from app.config import get_settings

    cfg = get_settings()

    async with async_session_maker() as session:
        # Same lock guard as the migrations. This runs during startup, after a
        # migration may have just failed on a lock — and a SELECT queued behind
        # a pending ACCESS EXCLUSIVE request waits as long as that request
        # does. Without a timeout here the process hangs before uvicorn opens
        # its port, so the readiness probe gets "connection refused", the
        # liveness probe kills the pod, and it crashloops. That is the b491c0c
        # rollout, exactly.
        # LOCAL: the session's transaction only — see _guard
        await session.execute(text("SET LOCAL lock_timeout = '5s'"))
        result = await session.execute(
            __import__("sqlalchemy", fromlist=["select"]).select(User)
        )
        if result.scalars().first():
            return  # users already exist

        # Only now is the placeholder actually about to become a real
        # password. Warning about it at boot cried wolf on every restart of a
        # database that was seeded months ago and never reads this value.
        if cfg.super_admin_password == "CHANGE_ME":
            from loguru import logger as _log
            _log.warning(
                "SUPER_ADMIN_PASSWORD is the placeholder and is being used to "
                "create the super-admin account right now — change it after "
                "first login.")

        admin = User(
            username=cfg.super_admin_username,
            full_name="Super Admin",
            hashed_password=get_password_hash(cfg.super_admin_password),
            role="super_admin",
            is_active=True,
        )
        session.add(admin)
        await session.commit()
        from loguru import logger
        logger.info(
            f"Default super_admin created: username='{cfg.super_admin_username}'"
        )


async def _migrate_auth_v2(conn):
    """Portal sign-up columns, plus the move from three roles to four.

    Additive only — this runs against a live database while the previous image
    is still serving, so every statement has to be safe for a pod that has not
    restarted yet. New columns are nullable or defaulted; nothing is dropped.

    The backfill exists so the rollout does not quietly take access away from
    anyone. Two rules:
      * the old 'user' role becomes 'admin', keeping exactly the two areas it
        could already see (dashboard + properties) — a conversion must not
        widen access either;
      * an admin that predates the permission column gets the full set, because
        that is what it effectively had when the routers only checked for a
        valid token.
    A later edit by super_admin is what narrows anyone down.
    """
    try:
        import json
        from sqlalchemy import text
        from app.auth.permissions import ALL_PERMISSIONS, LEGACY_USER_PERMISSIONS

        # Ask the catalog before asking for the lock.
        #
        # ADD COLUMN IF NOT EXISTS is idempotent but not free: Postgres takes
        # ACCESS EXCLUSIVE when it opens the relation, *before* it checks
        # whether there is anything to add. On an already-migrated database
        # this statement does nothing and still has to win the strictest lock
        # there is — and while it waits it sits at the head of the lock queue,
        # where every later request on `users`, including a plain SELECT,
        # queues up behind it.
        #
        # That is what took down deploys 65048fc and b491c0c: the columns had
        # existed for weeks, the ALTER was a no-op, and it still deadlocked the
        # rollout. Every sibling migration already checks the catalog first
        # (see _migrate_users_totp); this one never did.
        present = {r[0] for r in (await conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='users' AND table_schema=current_schema()"))).all()}
        if not {"phone", "phone_verified", "email_verified", "email_2fa_enabled",
                "marketing_opt_in", "permissions"} <= present:
            await conn.execute(text(
                "ALTER TABLE users "
                "ADD COLUMN IF NOT EXISTS phone VARCHAR(20), "
                "ADD COLUMN IF NOT EXISTS phone_verified BOOLEAN NOT NULL DEFAULT FALSE, "
                "ADD COLUMN IF NOT EXISTS email_verified BOOLEAN NOT NULL DEFAULT FALSE, "
                "ADD COLUMN IF NOT EXISTS email_2fa_enabled BOOLEAN NOT NULL DEFAULT FALSE, "
                "ADD COLUMN IF NOT EXISTS marketing_opt_in BOOLEAN NOT NULL DEFAULT FALSE, "
                "ADD COLUMN IF NOT EXISTS permissions JSON"))
        # Partial index: many staff rows have no portal phone at all, and a
        # plain UNIQUE would collapse them onto a single NULL slot in some
        # engines. Postgres allows repeated NULLs anyway; the WHERE clause
        # keeps the index small and says the intent out loud.
        await conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_phone_unique "
            "ON users (phone) WHERE phone IS NOT NULL"))

        legacy = json.dumps(LEGACY_USER_PERMISSIONS)
        full = json.dumps(ALL_PERMISSIONS)

        # Order matters: tag the legacy rows while they still say 'user'.
        res = await conn.execute(text(
            "UPDATE users SET permissions = CAST(:p AS JSON) "
            "WHERE role = 'user' AND permissions IS NULL"), {"p": legacy})
        tagged = res.rowcount or 0

        res = await conn.execute(text(
            "UPDATE users SET role = 'admin' WHERE role = 'user'"))
        converted = res.rowcount or 0

        res = await conn.execute(text(
            "UPDATE users SET permissions = CAST(:p AS JSON) "
            "WHERE role = 'admin' AND permissions IS NULL"), {"p": full})
        widened = res.rowcount or 0

        # Anything left without a list (super_admin, root, visitor) gets an
        # empty one so the column is never NULL for the app to reason about.
        await conn.execute(text(
            "UPDATE users SET permissions = CAST('[]' AS JSON) WHERE permissions IS NULL"))

        if converted or widened:
            print(f"auth v2: {converted} 'user' account(s) -> admin "
                  f"({tagged} kept their previous two areas), "
                  f"{widened} existing admin(s) given the full permission set")
    except Exception as e:
        # Swallowed like its siblings — but see the verification below, which
        # is what actually decides whether this boot may continue.
        print(f"auth v2 migration statements failed: {e}")


async def _verify_auth_v2(conn):
    """Refuse to boot if the users table is missing a column the model needs.

    Every other migration here swallows its errors, which is right for them:
    they add a column some feature reads, and a feature degrades. These five
    are different. The User model selects them on every single query, so a
    silently-skipped ALTER does not degrade one screen — it breaks login, the
    dashboard and the API at once, on a deploy that reported success.

    Raising is the safer failure. The rollout is `kubectl set image` against a
    Deployment, so a pod that dies during startup never becomes ready and the
    previous pod keeps serving traffic: a failed deploy instead of an outage.
    """
    from sqlalchemy import text

    # totp_last_step: if both Alembic 0010 and its boot ALTER lost the lock
    # race (a DR pg_dump holding the table), every user load would 500 on a
    # pod that reported Ready — refusing here rolls the deploy back instead.
    required = {"phone", "phone_verified", "email_verified", "email_2fa_enabled",
                "marketing_opt_in", "permissions", "totp_last_step"}
    dialect = conn.engine.dialect.name

    if dialect == "postgresql":
        # Scoped to the active schema: information_schema.columns spans every
        # schema the role can see, so an unrelated "users" table in another one
        # could satisfy this check while the table the app actually writes to is
        # still missing its columns — the exact failure this guard exists to catch.
        rows = (await conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'users' AND table_schema = current_schema()"))).all()
        present = {r[0] for r in rows}
    elif dialect == "sqlite":
        rows = (await conn.execute(text("PRAGMA table_info(users)"))).all()
        present = {r[1] for r in rows}
    else:
        return  # unknown engine: nothing reliable to check against

    missing = required - present
    if missing:
        raise RuntimeError(
            "auth v2 migration did not apply — users table is missing "
            f"{sorted(missing)}. Refusing to start: the User model reads these "
            "on every query, so serving now would fail every request. "
            "Apply the ALTER manually and restart."
        )


async def _seed_root():
    """Create the developer's root account if ROOT_PASSWORD is configured.

    Idempotent by username. Never touches an existing row: if the account is
    already there the password stays whatever it was rotated to, so putting the
    variable back in the environment cannot silently reset it.
    """
    from sqlalchemy import select
    from app.models.user import User
    from app.auth.jwt import get_password_hash
    from app.config import get_settings
    from loguru import logger

    cfg = get_settings()
    if not cfg.root_password:
        return

    async with async_session_maker() as session:
        await session.execute(text("SET LOCAL lock_timeout = '5s'"))   # see _seed_super_admin
        existing = await session.execute(
            select(User).where(User.username == cfg.root_username))
        if existing.scalars().first():
            return

        session.add(User(
            username=cfg.root_username,
            email=cfg.root_email or None,
            full_name="Root",
            hashed_password=get_password_hash(cfg.root_password),
            role="root",
            is_active=True,
            permissions=[],
        ))
        await session.commit()
        logger.info(f"Root account created: username='{cfg.root_username}'")


async def close_db():
    """Close database connections"""
    await engine.dispose()
