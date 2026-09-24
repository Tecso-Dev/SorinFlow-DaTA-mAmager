"""the reader, the embedder and the matcher re-process what changed

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-24

Three columns worth of «has this listing moved since I last looked at it»:

  * properties.ai_content_fp — a short hash of the fields the reader and the
    matcher read (app/models/property.py:content_fingerprint), kept current
    by an ORM event listener on every insert/update.
  * a "fingerprint at my last pass" column per stage — ai_read_fp (+
    ai_read_attempts, the per-listing attempt cap), ai_embed_fp, and
    ai_matched_at / ai_match_fp for the match engine, which never wrote
    anything to the row before this.
  * the same pair for the vision tagger — ai_photo_fp, ai_photo_attempts.

Guarded like 0006/0009: create_all may already have built these on a fresh
database. ai_read_at, ai_embedded_at and ai_matched_at gain a plain index
each: the reader/embedder/matcher's due-queries have no id lower bound any
more (see app/ai/listing_reader.py's run_once), so "never looked at" has to
be findable without a full scan. Model-declared (index=True), so `alembic
check` sees no drift — same shape as ai_duplicate_of in 0006.

No backfill here — app/database.py's boot step does it with the running
app's own definition of "already processed", so it also covers a database
migrated by Alembic outside a boot (the reverse is not true: init_db always
runs at boot, alembic upgrade does not always follow).
"""
from alembic import op
import sqlalchemy as sa


revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

TABLE = "properties"
COLUMNS = (
    sa.Column("ai_content_fp", sa.String(16), nullable=True),
    sa.Column("ai_embed_fp", sa.String(16), nullable=True),
    sa.Column("ai_read_fp", sa.String(16), nullable=True),
    sa.Column("ai_read_attempts", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("ai_photo_fp", sa.String(16), nullable=True),
    sa.Column("ai_photo_attempts", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("ai_matched_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("ai_match_fp", sa.String(16), nullable=True),
)
# (index name, column) — SQLAlchemy's default ix_<table>_<column> naming, so
# it matches what Column(index=True) on the model produces.
INDEXES = (
    ("ix_properties_ai_read_at", "ai_read_at"),
    ("ix_properties_ai_embedded_at", "ai_embedded_at"),
    ("ix_properties_ai_matched_at", "ai_matched_at"),
)


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    have = {c["name"] for c in insp.get_columns(TABLE)}
    for col in COLUMNS:
        if col.name not in have:
            op.add_column(TABLE, col)
    have_ix = {i["name"] for i in insp.get_indexes(TABLE)}
    for name, col in INDEXES:
        if name not in have_ix:
            op.create_index(name, TABLE, [col], unique=False)


def downgrade() -> None:
    for name, _col in INDEXES:
        op.drop_index(name, table_name=TABLE)
    for col in reversed(COLUMNS):
        op.drop_column(TABLE, col.name)
