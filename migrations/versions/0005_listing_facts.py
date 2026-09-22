"""the listing reader's facts

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-22

What the AI read in a listing's text (app/ai/listing_reader.py):
the structured facts as JSON with a confidence per field, and when.
Guarded like 0002 and 0003: create_all may already have built the columns.
"""
from alembic import op
import sqlalchemy as sa


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

TABLE = "properties"


def _missing(insp, name):
    return name not in {c["name"] for c in insp.get_columns(TABLE)}


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if _missing(insp, "ai_facts"):
        op.add_column(TABLE, sa.Column("ai_facts", sa.JSON(), nullable=True))
    if _missing(insp, "ai_read_at"):
        op.add_column(TABLE, sa.Column("ai_read_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column(TABLE, "ai_read_at")
    op.drop_column(TABLE, "ai_facts")
