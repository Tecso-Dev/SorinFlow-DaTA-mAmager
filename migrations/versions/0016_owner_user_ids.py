"""owned rows name their owner's account, not only a display name

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-25

Six tables say who owns a row by a display name — full_name, else username:
private files (properties.created_by), personal cabinets (crm_cabinets.owner),
matches (crm_customer_matches.consultant), tasks (crm_tasks.assigned_to),
the call queue (leads.assigned_to) and «سورین»'s customers
(crm_customers.consultant_name). Each gains, beside its name column:

  * <name>_user_id — the account, users.id ON DELETE SET NULL, indexed;
  * owner_resolved_from — the name that account was resolved from.

Backfilled here by app/auth/visibility.py:backfill_owner_ids — the same
function init_db runs at every boot, for rows the previous release writes
during the rolling deploy: a row gets the id of the ONE staff account whose
display name is exactly its name; nobody, or two, leaves it NULL.

Additive only: the previous release never reads these columns and keeps
writing names, which the boot step resolves. Guarded like the others:
create_all may already have built them. SQLite gets the columns without
the foreign keys (no ALTER ... ADD CONSTRAINT there).
"""
from alembic import op
import sqlalchemy as sa


revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

# table → (name column, account column, FK name) — the FK names match the
# models', so `alembic check` sees no drift
OWNED = (
    ("properties", "created_by", "created_by_user_id", "fk_properties_created_by_user"),
    ("crm_cabinets", "owner", "owner_user_id", "fk_crm_cabinets_owner_user"),
    ("crm_customer_matches", "consultant", "consultant_user_id", "fk_crm_customer_matches_consultant_user"),
    ("crm_tasks", "assigned_to", "assigned_to_user_id", "fk_crm_tasks_assigned_to_user"),
    ("leads", "assigned_to", "assigned_to_user_id", "fk_leads_assigned_to_user"),
    ("crm_customers", "consultant_name", "consultant_user_id", "fk_crm_customers_consultant_user"),
)
RESOLVED = "owner_resolved_from"


def upgrade() -> None:
    bind = op.get_bind()
    fks_allowed = bind.dialect.name != "sqlite"
    for table, _name, id_col, fk in OWNED:
        insp = sa.inspect(bind)
        cols = {c["name"] for c in insp.get_columns(table)}
        if id_col not in cols:
            op.add_column(table, sa.Column(id_col, sa.Integer(), nullable=True))
        if RESOLVED not in cols:
            op.add_column(table, sa.Column(RESOLVED, sa.String(200), nullable=True))
        ix = f"ix_{table}_{id_col}"
        if ix not in {i["name"] for i in insp.get_indexes(table)}:
            op.create_index(ix, table, [id_col], unique=False)
        if fks_allowed and fk not in {f.get("name") for f in insp.get_foreign_keys(table)}:
            op.create_foreign_key(fk, table, "users", [id_col], ["id"], ondelete="SET NULL")

    from app.auth.visibility import backfill_owner_ids
    touched = backfill_owner_ids(bind)
    print(f"0016: owner accounts resolved for {touched} row(s)")


def downgrade() -> None:
    bind = op.get_bind()
    for table, _name, id_col, fk in reversed(OWNED):
        insp = sa.inspect(bind)
        if bind.dialect.name != "sqlite" and fk in {f.get("name") for f in insp.get_foreign_keys(table)}:
            op.drop_constraint(fk, table, type_="foreignkey")
        if f"ix_{table}_{id_col}" in {i["name"] for i in insp.get_indexes(table)}:
            op.drop_index(f"ix_{table}_{id_col}", table_name=table)
        cols = {c["name"] for c in insp.get_columns(table)}
        for col in (RESOLVED, id_col):
            if col in cols:
                op.drop_column(table, col)
