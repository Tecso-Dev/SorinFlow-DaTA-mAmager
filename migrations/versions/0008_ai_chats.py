"""the assistant's questions

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-22

One row per question the office asked its assistant (app/models/ai_chat.py):
who, what, which tools were read, what was answered. Guarded like the
others: create_all may already have built it.
"""
from alembic import op
import sqlalchemy as sa


revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

TABLE = "ai_chats"


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if TABLE in insp.get_table_names():
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chat_id", sa.String(40)),
        sa.Column("who", sa.String(120)),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text()),
        sa.Column("tools", sa.JSON()),
        sa.Column("ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("error", sa.String(300)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_ai_chats_id", TABLE, ["id"])
    op.create_index("ix_ai_chats_chat_id", TABLE, ["chat_id"])
    op.create_index("ix_ai_chats_created_at", TABLE, ["created_at"])


def downgrade() -> None:
    op.drop_table(TABLE)
