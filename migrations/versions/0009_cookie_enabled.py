"""the owner's off switch for a Divar number

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-24

cookies.enabled (app/models/cookie.py): a number whose phone is in a drawer
stays valid and stays its owner's, but is not handed a run. Guarded like the
others: the startup migration or create_all may already have added it.
"""
from alembic import op
import sqlalchemy as sa


revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "cookies" not in insp.get_table_names():
        return
    if any(c["name"] == "enabled" for c in insp.get_columns("cookies")):
        return
    op.add_column("cookies", sa.Column("enabled", sa.Boolean(), nullable=False,
                                       server_default=sa.true()))


def downgrade() -> None:
    op.drop_column("cookies", "enabled")
