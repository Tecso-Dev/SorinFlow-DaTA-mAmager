"""
SorinFlow Divar Scraper - Database Connection
"""
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import text
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool
import redis.asyncio as redis
from typing import AsyncGenerator
from app.config import get_settings

settings = get_settings()

# Create async engine
engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    poolclass=NullPool,
    future=True
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


async def init_db():
    """Create tables, apply migrations, seed the first accounts.

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
                            crm_models, app_setting, portal, email_log)

    async with engine.begin() as conn:
        await _guard(conn)
        await conn.run_sync(Base.metadata.create_all)

    # Order still matters where one migration depends on another's columns;
    # it is preserved. What changed is the blast radius when one fails.
    for step in (_migrate_users_totp,
                 _migrate_users_divar_phone,
                 _migrate_scraping_jobs_divar_phone,
                 _migrate_properties_owner_phone,
                 _migrate_dpa_activities,
                 _migrate_lead_form_v2,
                 _migrate_property_serial,
                 _migrate_property_corner,
                 _migrate_calendar_sms,
                 _migrate_customer_criteria,
                 _migrate_filing,
                 _migrate_advertiser_type,
                 _migrate_advertiser_signals,
                 _migrate_cookie_usage,
                 _migrate_property_quality,
                 _migrate_job_finish_reason,
                 _migrate_price_history,
                 _migrate_image_hashes,
                 _migrate_sms_panel,
                 _seed_reference_data):
        try:
            async with engine.begin() as conn:
                await _guard(conn)
                await step(conn)
        except Exception as e:
            # The step already swallows its own errors; this catches the ones
            # it cannot — a lock timeout on the very first statement, or a
            # failure while committing.
            print(f"{step.__name__} skipped: {e}")

    async with engine.begin() as conn:
        await _guard(conn)
        await _migrate_auth_v2(conn)

    # A clean transaction for the check, so it reads the real schema rather
    # than inheriting the wreckage of a failed migration and mis-reporting why.
    async with engine.begin() as conn:
        await _verify_auth_v2(conn)

    # Seeding creates the *first* accounts. On an established database both are
    # no-ops, so a failure here — a lock timeout, a transient database blip —
    # must not stop a pod that is otherwise ready to serve. _verify_auth_v2
    # above is the check that is allowed to refuse to start; this is not.
    for seed in (_seed_super_admin, _seed_root):
        try:
            await seed()
        except Exception as e:
            print(f"{seed.__name__} skipped: {e}")


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
    """
    await conn.execute(text("SET lock_timeout = '5s'"))
    await conn.execute(text("SET statement_timeout = '120s'"))


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


async def _migrate_advertiser_signals(conn):
    """What the ad's own words say about who posted it.

    Divar's own declaration already has a column; these two sit beside it
    because Divar returns agency listings under a «شخصی» filter, and the
    disagreement is the thing worth seeing. Existing rows land FALSE/NULL:
    nothing rescans the archive, so an old row simply says nothing rather
    than claiming to be private.
    """
    try:
        from sqlalchemy import text
        await conn.execute(text(
            "ALTER TABLE properties "
            "ADD COLUMN IF NOT EXISTS agency_suspected BOOLEAN DEFAULT FALSE, "
            "ADD COLUMN IF NOT EXISTS agency_evidence VARCHAR(100)"))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_properties_agency_suspected "
            "ON properties (agency_suspected)"))
    except Exception as e:
        print(f"advertiser signals migration skipped: {e}")


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
        await session.execute(text("SET lock_timeout = '5s'"))
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
        if not {"phone", "phone_verified", "email_verified",
                "marketing_opt_in", "permissions"} <= present:
            await conn.execute(text(
                "ALTER TABLE users "
                "ADD COLUMN IF NOT EXISTS phone VARCHAR(20), "
                "ADD COLUMN IF NOT EXISTS phone_verified BOOLEAN NOT NULL DEFAULT FALSE, "
                "ADD COLUMN IF NOT EXISTS email_verified BOOLEAN NOT NULL DEFAULT FALSE, "
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

    required = {"phone", "phone_verified", "email_verified",
                "marketing_opt_in", "permissions"}
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
        await session.execute(text("SET lock_timeout = '5s'"))   # see _seed_super_admin
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
