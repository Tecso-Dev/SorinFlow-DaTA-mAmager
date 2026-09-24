"""a TOTP code is accepted once

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-24

users.totp_last_step — the time-step of the last code accepted for the
account. pyotp honours a code for its own 30 s and one step either side, so
the same six digits used to log in again for up to 90 s. A code whose step is
not newer than this one is now refused.

Nullable, no default: NULL means «none accepted yet», which is every account
today, and the previous image never reads it. Guarded like the others:
create_all or the boot-time ALTER may already have built it.
"""
from alembic import op
import sqlalchemy as sa


revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

TABLE = "users"


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "totp_last_step" not in {c["name"] for c in insp.get_columns(TABLE)}:
        op.add_column(TABLE, sa.Column("totp_last_step", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column(TABLE, "totp_last_step")
