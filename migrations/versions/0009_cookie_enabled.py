"""a Divar number its owner can switch off

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-24

cookies.is_enabled — the owner's «this SIM is not reachable right now».
Rotation, «خودکار» and a manual pick skip a number that is off, so a code
is never sent to a phone nobody can read. Every existing number starts on:
until today nothing could switch one off.

Guarded like the others: create_all may already have built the column.
"""
from alembic import op
import sqlalchemy as sa


revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

TABLE = "cookies"


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "is_enabled" not in {c["name"] for c in insp.get_columns(TABLE)}:
        op.add_column(TABLE, sa.Column("is_enabled", sa.Boolean(), nullable=False,
                                       server_default=sa.true()))


def downgrade() -> None:
    op.drop_column(TABLE, "is_enabled")
