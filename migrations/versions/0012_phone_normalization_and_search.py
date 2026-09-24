"""normalized phone lookups, and the properties list's indexes

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-24

Three additive, independent changes, bundled into one revision because they
are all "make the panel findable" work on the same handful of hot tables:

  * properties (is_active, scraped_at) — the properties list's default
    WHERE + ORDER BY (app/api/routes/properties.py), currently unindexed;
  * a normalized companion column for every phone that is looked up or
    deduped (see app/models/phone.py) — leads.phone_number,
    properties.phone_number, crm_contacts.phone, crm_customers.mobile1/2 —
    backfilled here in batches, kept in sync from here on by an ORM event
    on the models, so no route has to remember to;
  * GIN trigram indexes for the free-text ILIKE '%term%' searches already in
    the codebase: properties title/description/district/neighborhood/
    address/seller_name/tags/phone_number (app/api/routes/properties.py,
    filing.py, crm.py, app/ai/assistant.py), leads.property_title/notes/
    phone_number/seller_name (crm.py), crm_contacts.name/phone/phone2 and
    crm_customers.full_name/mobile1/mobile2/desired_district/
    consultant_name (crm.py, assistant.py).

Trigram indexes are declared ONLY here, never on the models — see the four
things that has to stay true, worked through:

  * a fresh database (every normal test run, and a fresh production install)
    is built by Base.metadata.create_all() before Alembic ever runs
    (app/database.init_db) — that call is not guarded the way the steps
    below are, so a model-declared trigram index would need pg_trgm to
    already exist or create_all fails outright, taking every table with it,
    not just search;
  * an existing (production) database reaches head through this migration,
    which is guarded: CREATE EXTENSION runs inside its own SAVEPOINT, and a
    role without the right for it loses only the trigram indexes, not the
    rest of this revision;
  * `alembic check` stays clean because migrations/env.py's include_object
    filters out anything named *_trgm — without it, a schema that has these
    indexes (from this migration) but whose models do not declare them would
    look like drift on every future autogenerate;
  * sqlite is unaffected: it has no GIN and no pg_trgm, and this file skips
    straight past that section on any non-Postgres dialect. The composite
    index and the phone columns are plain SQL and run on both.

Plain CREATE INDEX throughout, not CONCURRENTLY: this runs on the app's own
guarded boot connection (lock_timeout 5s, statement_timeout 120s — see
app/database._guard) inside one transaction, which CONCURRENTLY cannot run
in at all. Every table here is a few thousand to tens of thousands of rows
today — comfortably inside that budget; see the phase report for the
measured durations.
"""
from alembic import op
import sqlalchemy as sa


revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

# (table, raw column, normalized column) — the exact list app/models/phone.py's
# sync_phone_columns() is wired to on the models.
_PHONE_COLUMNS = [
    ("leads", "phone_number", "phone_number_normalized"),
    ("properties", "phone_number", "phone_number_normalized"),
    ("crm_contacts", "phone", "phone_normalized"),
    ("crm_customers", "mobile1", "mobile1_normalized"),
    ("crm_customers", "mobile2", "mobile2_normalized"),
]

# One boot's worth per table — small enough this never sits near the
# statement_timeout even on the largest table this touches today.
_BACKFILL_BATCH = 2000

# (table, column) — every ILIKE '%term%' target already in the codebase; see
# this file's docstring for exactly where each one is searched. Postgres only.
_TRIGRAM_COLUMNS = [
    ("properties", "title"), ("properties", "description"),
    ("properties", "district"), ("properties", "neighborhood"),
    ("properties", "address"), ("properties", "seller_name"),
    ("properties", "tags"), ("properties", "phone_number"),
    ("leads", "property_title"), ("leads", "notes"),
    ("leads", "phone_number"), ("leads", "seller_name"),
    ("crm_contacts", "name"), ("crm_contacts", "phone"), ("crm_contacts", "phone2"),
    ("crm_customers", "full_name"), ("crm_customers", "mobile1"),
    ("crm_customers", "mobile2"), ("crm_customers", "desired_district"),
    ("crm_customers", "consultant_name"),
]


def _try(bind, label, fn) -> None:
    """Run fn() inside its own SAVEPOINT.

    Alembic runs this whole revision chain (0002 through head) in ONE
    database transaction when it brings a pre-Alembic database to head
    (init_db's "stamp 0001, then upgrade" path) — there is no per-revision
    boundary the way init_db's OWN boot steps each get (see app/database
    ._guard). Without this, one statement here failing for a reason that
    has nothing to do with this revision — a table some other process left
    in an unexpected shape — would roll back every revision already applied
    in the same transaction, including the alembic_version stamp itself.
    One savepoint per operation makes a failure here exactly as local as a
    failed boot-time _migrate_* step: logged, skipped, nothing else undone.
    """
    try:
        with bind.begin_nested():
            fn()
    except Exception as e:
        print(f"0012 {label} skipped: {e}")


def _composite_index(insp) -> None:
    if "ix_properties_active_scraped_at" not in {i["name"] for i in insp.get_indexes("properties")}:
        op.create_index("ix_properties_active_scraped_at", "properties",
                         ["is_active", sa.text("scraped_at DESC")])


def _phone_column(insp, table, norm) -> None:
    if norm not in {c["name"] for c in insp.get_columns(table)}:
        op.add_column(table, sa.Column(norm, sa.String(20), nullable=True))
    ix = f"ix_{table}_{norm}"
    if ix not in {i["name"] for i in insp.get_indexes(table)}:
        op.create_index(ix, table, [norm])


def _backfill_one(bind, table, raw, norm) -> None:
    """Fill the normalized column for rows written before it existed, in
    batches so a large table never sits inside one UPDATE past
    statement_timeout. Reuses the exact function the ORM event uses for
    every write from here on (app/models/phone.py) — one normalizer, not a
    second copy of it re-written in SQL.
    """
    from app.models.phone import normalize_phone

    # Walk by id, not by "still NULL": a raw value with no digits («مخفی», an
    # empty string) normalizes to NULL again, so the same rows would come back
    # on every select — two thousand of them and the loop never ends, the
    # boot never finishes, and the deploy rolls back.
    last_id = 0
    while True:
        rows = bind.execute(sa.text(
            f"SELECT id, {raw} FROM {table} "
            f"WHERE {norm} IS NULL AND {raw} IS NOT NULL AND id > :after "
            f"ORDER BY id LIMIT :n"
        ), {"after": last_id, "n": _BACKFILL_BATCH}).fetchall()
        if not rows:
            break
        bind.execute(
            sa.text(f"UPDATE {table} SET {norm} = :norm WHERE id = :id"),
            [{"id": r[0], "norm": normalize_phone(r[1])} for r in rows],
        )
        last_id = rows[-1][0]


def _existing_index_names(bind) -> set:
    return {r[0] for r in bind.execute(sa.text(
        "SELECT indexname FROM pg_indexes WHERE schemaname = current_schema()")).fetchall()}


def _trigram_indexes(bind) -> None:
    has_trgm = False
    try:
        with bind.begin_nested():
            bind.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        has_trgm = True
    except Exception as e:
        # A managed Postgres can withhold CREATE EXTENSION from a
        # non-superuser role. The savepoint above already rolled back just
        # this attempt, so the rest of the migration is unaffected —
        # skipping search speed is fine; failing the whole deploy over it
        # is not.
        print(f"pg_trgm unavailable, trigram search indexes skipped: {e}")
    if not has_trgm:
        return

    existing = _existing_index_names(bind)
    for table, column in _TRIGRAM_COLUMNS:
        name = f"ix_{table}_{column}_trgm"
        if name in existing:
            continue
        _try(bind, f"{name} index", lambda n=name, t=table, c=column: bind.execute(sa.text(
            f'CREATE INDEX "{n}" ON {t} USING gin ({c} gin_trgm_ops)')))


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    _try(bind, "properties composite index", lambda: _composite_index(insp))

    for table, raw, norm in _PHONE_COLUMNS:
        _try(bind, f"{table}.{norm} column",
             lambda t=table, n=norm: _phone_column(insp, t, n))
        _try(bind, f"{table}.{norm} backfill",
             lambda t=table, r=raw, n=norm: _backfill_one(bind, t, r, n))

    if bind.dialect.name == "postgresql":
        _trigram_indexes(bind)


def downgrade() -> None:
    bind = op.get_bind()

    if bind.dialect.name == "postgresql":
        existing = _existing_index_names(bind)
        for table, column in _TRIGRAM_COLUMNS:
            name = f"ix_{table}_{column}_trgm"
            if name in existing:
                op.execute(f'DROP INDEX IF EXISTS "{name}"')

    insp = sa.inspect(bind)
    for table, _raw, norm in _PHONE_COLUMNS:
        if f"ix_{table}_{norm}" in {i["name"] for i in insp.get_indexes(table)}:
            op.drop_index(f"ix_{table}_{norm}", table_name=table)
        if norm in {c["name"] for c in insp.get_columns(table)}:
            op.drop_column(table, norm)

    if "ix_properties_active_scraped_at" in {i["name"] for i in insp.get_indexes("properties")}:
        op.drop_index("ix_properties_active_scraped_at", table_name="properties")
