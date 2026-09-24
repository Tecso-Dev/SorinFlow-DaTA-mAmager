"""portal requests remember whether their description was read

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-24

The visitor's request is created from the form alone now — the model reads
whatever the form left blank in the background (app/crm/portal_bridge.py
enrich_needs), not on the request that is waiting for a page.
need_enriched_at marks a request done, successfully or not, so a slow or
broken gateway does not read the same request every pass; need_enrich_attempts
counts real failures only, capped at 3 — a gateway that is off, over budget
or unconfigured costs no attempt.

Guarded like the others: create_all or the boot-time ALTER may already have
built these.
"""
from alembic import op
import sqlalchemy as sa


revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

TABLE = "portal_property_requests"


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    cols = {c["name"] for c in insp.get_columns(TABLE)}
    if "need_enriched_at" not in cols:
        op.add_column(TABLE, sa.Column("need_enriched_at", sa.DateTime(timezone=True), nullable=True))
        # the requests already here were read when they were filed (the old
        # path) — only new ones are the background pass's business
        op.execute(sa.text(f"UPDATE {TABLE} SET need_enriched_at = CURRENT_TIMESTAMP "
                           f"WHERE need_enriched_at IS NULL"))
    if "need_enrich_attempts" not in cols:
        op.add_column(TABLE, sa.Column(
            "need_enrich_attempts", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column(TABLE, "need_enrich_attempts")
    op.drop_column(TABLE, "need_enriched_at")
