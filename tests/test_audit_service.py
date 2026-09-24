"""
The audit trail's own service: app/services/audit.py.

Modelled on tests/test_sms_event_log.py and test_job_log.py, and for the
same reason — a caller of record() is usually mid-transaction (deleting a
user, saving a setting), so the two properties that matter are that it
writes on its own session and never raises, whatever the caller was doing.
"""
import inspect
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_audit.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")


class TestItCannotTakeDownWhatItDescribes:

    def test_record_uses_its_own_session(self):
        from app.services import audit
        src = inspect.getsource(audit.record)
        assert "async_session_maker()" in src
        assert "session.commit()" in src

    def test_record_never_raises(self):
        from app.services import audit
        src = inspect.getsource(audit.record)
        assert "except Exception" in src and "return False" in src

    @pytest.mark.asyncio
    async def test_a_broken_database_is_false_not_an_exception(self, monkeypatch):
        from app.services import audit

        def boom(*a, **kw):
            raise RuntimeError("database is on fire")

        monkeypatch.setattr(audit, "async_session_maker", boom)
        assert await audit.record("login_success") is False

    @pytest.mark.asyncio
    async def test_a_broken_prune_is_zero_not_an_exception(self, monkeypatch):
        from app.services import audit

        def boom(*a, **kw):
            raise RuntimeError("database is on fire")

        monkeypatch.setattr(audit, "async_session_maker", boom)
        assert await audit.prune() == 0

    def test_the_model_is_registered_for_create_all(self):
        """A model whose module is never imported before create_all is a
        table a fresh boot forgets — see app/models/__init__.py."""
        src = open(os.path.join(os.path.dirname(__file__), "..", "app", "database.py"),
                   encoding="utf-8").read()
        assert "audit_event" in src.split("Base.metadata.create_all")[0]


class TestSecretsAreStrippedRecursively:

    @pytest.mark.parametrize("key", [
        "password", "Password", "api_key", "sms_token", "otp_code",
        "secret", "cookie", "passphrase", "Authorization",
    ])
    def test_a_secret_looking_key_is_dropped(self, key):
        from app.services.audit import _strip_secrets
        out = _strip_secrets({key: "s3cr3t", "fields": ["ok"]})
        assert key not in out
        assert out["fields"] == ["ok"]

    def test_nested_dicts_and_lists_are_cleaned_too(self):
        from app.services.audit import _strip_secrets
        out = _strip_secrets({
            "user": {"username": "sobhan", "password": "x"},
            "rows": [{"token": "y", "name": "a"}, {"name": "b"}],
        })
        assert out == {
            "user": {"username": "sobhan"},
            "rows": [{"name": "a"}, {"name": "b"}],
        }

    def test_ordinary_values_pass_through_unchanged(self):
        from app.services.audit import _strip_secrets
        assert _strip_secrets({"role": "admin", "count": 3}) == {"role": "admin", "count": 3}


class TestARealWrite:

    @pytest.fixture(scope="module", autouse=True)
    async def _schema(self):
        # Only this table, not Base.metadata.create_all: scraping_jobs.job_id
        # is a postgresql UUID column sqlite cannot render (test_auth_roles.py
        # skips whole-schema tests on sqlite for the same reason).
        from app.database import engine
        from app.models.audit_event import AuditEvent
        async with engine.begin() as conn:
            await conn.run_sync(AuditEvent.__table__.create, checkfirst=True)

    @pytest.mark.asyncio
    async def test_a_recorded_event_round_trips_with_the_right_shape(self):
        from sqlalchemy import select
        from app.database import async_session_maker
        from app.models.audit_event import AuditEvent
        from app.log_redaction import request_id_var
        from app.services import audit

        class _Actor:
            id = 7
            username = "sobhan"
            role = "root"

        token = request_id_var.set("req-abc123")
        try:
            ok = await audit.record(
                "user_delete", actor=_Actor(), target_type="user", target_id=9,
                summary="کاربر حذف شد", detail={"reason": "left the team", "password": "leaked"})
        finally:
            request_id_var.reset(token)
        assert ok is True

        async with async_session_maker() as db:
            row = (await db.execute(
                select(AuditEvent).where(AuditEvent.action == "user_delete")
                .order_by(AuditEvent.id.desc()))).scalars().first()
        assert row is not None
        assert row.actor_user_id == 7
        assert row.actor_username == "sobhan"
        assert row.actor_role == "root"
        assert row.target_type == "user"
        assert row.target_id == "9"          # coerced to str, matches the column
        assert row.summary == "کاربر حذف شد"
        assert row.detail == {"reason": "left the team"}   # password dropped
        assert row.request_id == "req-abc123"
        assert row.created_at is not None

    @pytest.mark.asyncio
    async def test_no_actor_still_records_with_null_actor_fields(self):
        from sqlalchemy import select
        from app.database import async_session_maker
        from app.models.audit_event import AuditEvent
        from app.services import audit

        assert await audit.record("login_failed", summary="تلاش ورود ناموفق: «ghost»") is True
        async with async_session_maker() as db:
            row = (await db.execute(
                select(AuditEvent).where(AuditEvent.action == "login_failed")
                .order_by(AuditEvent.id.desc()))).scalars().first()
        assert row.actor_user_id is None and row.actor_username is None

    @pytest.mark.asyncio
    async def test_prune_drops_only_what_is_older_than_the_cutoff(self):
        from datetime import datetime, timedelta, timezone
        from sqlalchemy import select
        from app.database import async_session_maker
        from app.models.audit_event import AuditEvent
        from app.services import audit

        old_cutoff = datetime.now(timezone.utc) - timedelta(days=400)
        async with async_session_maker() as db:
            db.add(AuditEvent(action="ancient_event", created_at=old_cutoff))
            await db.commit()
        await audit.record("recent_event")

        n = await audit.prune(days=365)
        assert n >= 1

        async with async_session_maker() as db:
            actions = set((await db.execute(select(AuditEvent.action))).scalars().all())
        assert "ancient_event" not in actions
        assert "recent_event" in actions


class TestTheActionCatalog:
    def test_every_action_has_a_persian_label(self):
        from app.services.audit import ACTIONS
        assert ACTIONS
        for key, label in ACTIONS.items():
            assert isinstance(key, str) and key
            assert isinstance(label, str) and label
