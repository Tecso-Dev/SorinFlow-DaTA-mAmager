"""audit events — who did what, when

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-24

One row per staff action worth a record (app/services/audit.py): login,
password/2FA changes, user management, Divar number ownership, backups,
provider-settings saves. Guarded like the others: create_all may already
have built it.
"""
from alembic import op
import sqlalchemy as sa


revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

TABLE = "audit_events"


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if TABLE in insp.get_table_names():
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        # No FK: users get deleted; this is a snapshot of who did it, not a
        # live pointer (see app/models/audit_event.py).
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("actor_username", sa.String(100)),
        sa.Column("actor_role", sa.String(20)),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target_type", sa.String(40)),
        sa.Column("target_id", sa.String(64)),
        sa.Column("summary", sa.String(300)),
        sa.Column("detail", sa.JSON()),
        sa.Column("ip", sa.String(64)),
        sa.Column("request_id", sa.String(64)),
    )
    op.create_index("ix_audit_events_created_at", TABLE, ["created_at"])
    op.create_index("ix_audit_events_actor_created", TABLE, ["actor_user_id", "created_at"])
    op.create_index("ix_audit_events_action_created", TABLE, ["action", "created_at"])


def downgrade() -> None:
    op.drop_table(TABLE)
