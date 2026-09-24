"""which Telegram account is which panel user

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-24

telegram_links (app/models/telegram_link.py): the assistant «سورین»
answers a linked panel user in a private chat, with that user's own panel
rights, instead of answering whoever writes in the backup's chats. A new
table the previous image never reads, so a rollback is safe. Guarded like
the others: create_all may already have built it.
"""
from alembic import op
import sqlalchemy as sa


revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

TABLE = "telegram_links"


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if TABLE in insp.get_table_names():
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False, unique=True),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("telegram_username", sa.String(64)),
        sa.Column("linked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_telegram_links_id", TABLE, ["id"])


def downgrade() -> None:
    op.drop_table(TABLE)
