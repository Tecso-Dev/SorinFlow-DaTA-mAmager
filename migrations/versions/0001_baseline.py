"""baseline — the schema as it stood on 2026-09-21

Revision ID: 0001
Revises:
Create Date: 2026-09-21

Everything before this revision was hand-written ALTERs at boot
(app/database.py, the _migrate_* steps). They still run, idempotently, so
a database older than this baseline is brought up to it first; then the
app stamps this revision and applies whatever comes after.

On an empty database this revision creates the whole schema from the
models. ponytail: it is create_all from the *current* models, so once a
later revision exists, a bare `alembic upgrade head` on an empty database
would build the new columns here and then trip over them in that revision.
An empty database is brought up by the app (create_all, then stamp head) —
see init_db. Freeze this into explicit op.create_table calls if the CLI
path on an empty database ever has to work on its own.
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    from app.database import Base
    from app.models import (property, cookie, scraping_job, lead, user,   # noqa: F401
                            crm_models, app_setting, portal, email_log,
                            sms_log, forwarder, scrape_schedule, ai_usage, ai_chat)
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    from app.database import Base
    Base.metadata.drop_all(bind=op.get_bind())
