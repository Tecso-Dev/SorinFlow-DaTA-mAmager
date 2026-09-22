"""the listing's vector, and its duplicate

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-22

A listing's text as a vector (app/ai/embeddings.py) — JSON on the row,
cosine in Python at today's scale; pgvector when it outgrows that — and
the older listing this one duplicates, flagged never merged.
Guarded like 0002 and 0003: create_all may already have built the columns.
"""
from alembic import op
import sqlalchemy as sa


revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

TABLE = "properties"


def _missing(insp, name):
    return name not in {c["name"] for c in insp.get_columns(TABLE)}


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if _missing(insp, "ai_embedding"):
        op.add_column(TABLE, sa.Column("ai_embedding", sa.JSON(), nullable=True))
    if _missing(insp, "ai_embedded_at"):
        op.add_column(TABLE, sa.Column("ai_embedded_at", sa.DateTime(timezone=True), nullable=True))
    if _missing(insp, "ai_embed_version"):
        op.add_column(TABLE, sa.Column("ai_embed_version", sa.Integer(), nullable=True))
    if _missing(insp, "ai_duplicate_of"):
        op.add_column(TABLE, sa.Column("ai_duplicate_of", sa.Integer(), nullable=True))
    if "ix_properties_ai_duplicate_of" not in {i["name"] for i in insp.get_indexes(TABLE)}:
        op.create_index("ix_properties_ai_duplicate_of", TABLE, ["ai_duplicate_of"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_properties_ai_duplicate_of", table_name=TABLE)
    op.drop_column(TABLE, "ai_duplicate_of")
    op.drop_column(TABLE, "ai_embed_version")
    op.drop_column(TABLE, "ai_embedded_at")
    op.drop_column(TABLE, "ai_embedding")
