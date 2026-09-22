"""a run remembers the Divar accounts it used

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-22

scraping_jobs.divar_phone only ever held the number a run was started ON;
a run on «خودکار» had nothing, and a rotated run said nothing about the
numbers it moved through. The scraper now writes the current account to
divar_phone and every account so far to accounts_used, so the panel can
say which number did the scraping.

Guarded like 0002: create_all may already have built the column.
"""
from alembic import op
import sqlalchemy as sa


revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

TABLE = "scraping_jobs"


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "accounts_used" not in {c["name"] for c in insp.get_columns(TABLE)}:
        op.add_column(TABLE, sa.Column("accounts_used", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column(TABLE, "accounts_used")
