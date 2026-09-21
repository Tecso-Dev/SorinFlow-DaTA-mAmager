"""portal request knows its customer

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-21

A visitor's request becomes a CRM customer so the matching engine sees it
(app/crm/portal_bridge.py); the request keeps the customer's id. Cleared,
not cascaded, when the customer is deleted.

Guarded like the boot-time steps were: a database whose tables create_all
built from these models already has all three, and the pre-Alembic path
(stamp the baseline, upgrade) replays this on top of it.
"""
from alembic import op
import sqlalchemy as sa


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TABLE = "portal_property_requests"


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "customer_id" not in {c["name"] for c in insp.get_columns(TABLE)}:
        op.add_column(TABLE, sa.Column("customer_id", sa.Integer(), nullable=True))
    if "ix_portal_property_requests_customer_id" not in {i["name"] for i in insp.get_indexes(TABLE)}:
        op.create_index("ix_portal_property_requests_customer_id", TABLE, ["customer_id"], unique=False)
    if "fk_portal_requests_customer" not in {f["name"] for f in insp.get_foreign_keys(TABLE)}:
        op.create_foreign_key("fk_portal_requests_customer", TABLE, "crm_customers",
                              ["customer_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    op.drop_constraint("fk_portal_requests_customer", TABLE, type_="foreignkey")
    op.drop_index("ix_portal_property_requests_customer_id", table_name=TABLE)
    op.drop_column(TABLE, "customer_id")
