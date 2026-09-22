"""what the photos show

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-22

The vision model's tags for a listing's photos (app/ai/photo_tagger.py):
condition, furnished, rooms shown, floor plans and logos, quality — and when.
Guarded like 0002 and 0003: create_all may already have built the columns.
"""
from alembic import op
import sqlalchemy as sa


revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

TABLE = "properties"


def _missing(insp, name):
    return name not in {c["name"] for c in insp.get_columns(TABLE)}


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if _missing(insp, "ai_photo_tags"):
        op.add_column(TABLE, sa.Column("ai_photo_tags", sa.JSON(), nullable=True))
    if _missing(insp, "ai_photos_at"):
        op.add_column(TABLE, sa.Column("ai_photos_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column(TABLE, "ai_photos_at")
    op.drop_column(TABLE, "ai_photo_tags")
