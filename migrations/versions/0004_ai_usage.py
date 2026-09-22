"""the AI ledger

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-22

One row per call to the model gateway (app/models/ai_usage.py): who asked,
which model, what it cost — Liara's own figures. The daily cap is enforced
on it and the AI card reads it. Guarded like the others: create_all may
already have built it.
"""
from alembic import op
import sqlalchemy as sa


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

TABLE = "ai_usage"


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if TABLE in insp.get_table_names():
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("agent", sa.String(40), nullable=False),
        sa.Column("job", sa.String(20), nullable=False),
        sa.Column("model", sa.String(120)),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("cost_toman", sa.Float(), nullable=False, server_default="0"),
        sa.Column("ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("error", sa.String(300)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_ai_usage_id", TABLE, ["id"])
    op.create_index("ix_ai_usage_agent", TABLE, ["agent"])
    op.create_index("ix_ai_usage_created_at", TABLE, ["created_at"])


def downgrade() -> None:
    op.drop_table(TABLE)
