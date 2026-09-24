"""
GET /api/audit/events and /api/audit/actions — root and super_admin only.

Against the real app (app.main.app), not a rebuilt one, so the actual
role-gating dependency chain and middleware stack are what is tested —
but without running lifespan (init_db wants the full schema; TestClient
without `with` never starts it), and with only the two tables this needs
created directly, not the whole app.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_audit_api.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

# This suite runs against one shared sqlite database (every test file's own
# DATABASE_URL setdefault only takes effect for whichever file the process
# imports first), and the real login/password/2FA endpoints this stream
# wired to audit.record() write real rows as a side effect of unrelated
# test files. Every user and query in this file is scoped under this
# namespace so its assertions hold regardless of what else lands in the
# table.
NS = "auditz9k"


def _q(params):
    return {**params, "actor": NS}


@pytest.fixture(scope="module")
def client():
    from starlette.testclient import TestClient
    import app.main as m
    return TestClient(m.app)


@pytest.fixture(scope="module", autouse=True)
async def _schema():
    from app.database import engine
    from app.models.user import User
    from app.models.audit_event import AuditEvent
    async with engine.begin() as conn:
        await conn.run_sync(User.__table__.create, checkfirst=True)
        await conn.run_sync(AuditEvent.__table__.create, checkfirst=True)


async def _make_user(username, role):
    from app.database import async_session_maker
    from app.models.user import User
    from app.auth.jwt import get_password_hash
    async with async_session_maker() as db:
        user = User(username=username, hashed_password=get_password_hash("x"),
                   role=role, is_active=True)
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user


def _token(user):
    from app.auth.jwt import create_access_token, access_claims
    return create_access_token(access_claims(user))


def _auth(user):
    return {"Authorization": f"Bearer {_token(user)}"}


@pytest.fixture(scope="module")
async def root_user():
    return await _make_user(f"{NS}_root", "root")


@pytest.fixture(scope="module")
async def admin_user():
    return await _make_user(f"{NS}_admin", "admin")


@pytest.fixture(scope="module", autouse=True)
async def _seed_events(_schema):
    from app.database import async_session_maker
    from app.models.audit_event import AuditEvent

    now = datetime.now(timezone.utc)
    async with async_session_maker() as db:
        db.add_all([
            AuditEvent(action="login_success", actor_username=f"{NS}_sobhan", actor_role="root",
                      summary="ورود موفق: sobhan", created_at=now),
            AuditEvent(action="login_failed", actor_username=f"{NS}_mallory", actor_role=None,
                      summary="تلاش ورود ناموفق", created_at=now - timedelta(days=1)),
            AuditEvent(action="user_delete", actor_username=f"{NS}_sobhan", actor_role="root",
                      summary="کاربر حذف شد", created_at=now - timedelta(days=40)),
        ])
        await db.commit()


class TestRoleGating:
    def test_no_token_is_401(self, client):
        assert client.get("/api/audit/events").status_code == 401

    def test_an_admin_is_403(self, client, admin_user):
        r = client.get("/api/audit/events", headers=_auth(admin_user))
        assert r.status_code == 403

    def test_root_may_read(self, client, root_user):
        r = client.get("/api/audit/events", headers=_auth(root_user))
        assert r.status_code == 200

    def test_actions_is_gated_the_same_way(self, client, admin_user, root_user):
        assert client.get("/api/audit/actions").status_code == 401
        assert client.get("/api/audit/actions", headers=_auth(admin_user)).status_code == 403
        assert client.get("/api/audit/actions", headers=_auth(root_user)).status_code == 200


class TestListingEvents:
    def test_shape_and_newest_first(self, client, root_user):
        body = client.get("/api/audit/events", headers=_auth(root_user), params=_q({})).json()
        assert "items" in body and "total" in body
        assert body["total"] == 3
        created = [i["created_at"] for i in body["items"]]
        assert created == sorted(created, reverse=True)

    def test_a_row_carries_the_persian_action_label(self, client, root_user):
        body = client.get("/api/audit/events", headers=_auth(root_user),
                          params=_q({"action": "login_success"})).json()
        assert body["items"], "seeded row not found"
        row = body["items"][0]
        assert row["action"] == "login_success"
        assert row["action_label"] == "ورود موفق"

    def test_filter_by_actor_is_a_substring_match(self, client, root_user):
        body = client.get("/api/audit/events", headers=_auth(root_user),
                          params={"actor": f"{NS}_mall"}).json()
        assert body["total"] == 1
        assert body["items"][0]["actor_username"] == f"{NS}_mallory"

    def test_filter_by_action(self, client, root_user):
        body = client.get("/api/audit/events", headers=_auth(root_user),
                          params=_q({"action": "user_delete"})).json()
        assert body["total"] == 1
        assert body["items"][0]["action"] == "user_delete"

    def test_filter_by_since_excludes_older_rows(self, client, root_user):
        since = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        body = client.get("/api/audit/events", headers=_auth(root_user),
                          params=_q({"since": since})).json()
        assert body["total"] == 2, "the 40-day-old row should be excluded by a 2-day-old since"
        assert all(i["created_at"] >= since for i in body["items"])
        assert not any(i["action"] == "user_delete" for i in body["items"])

    def test_filter_by_until_excludes_newer_rows(self, client, root_user):
        until = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        body = client.get("/api/audit/events", headers=_auth(root_user),
                          params=_q({"until": until})).json()
        assert body["total"] == 1
        assert body["items"][0]["action"] == "user_delete"

    def test_a_bare_until_day_includes_that_whole_day(self, client, root_user):
        """The panel sends «تا» as a day. It means through the end of that
        Tehran day — as midnight it dropped every event of the day itself."""
        tehran = timezone(timedelta(hours=3, minutes=30))
        today = datetime.now(tehran).date().isoformat()
        body = client.get("/api/audit/events", headers=_auth(root_user),
                          params=_q({"since": today, "until": today})).json()
        assert any(i["action"] == "login_success" for i in body["items"]), body

    def test_bad_since_is_a_400_not_a_500(self, client, root_user):
        r = client.get("/api/audit/events", headers=_auth(root_user),
                       params=_q({"since": "not-a-date"}))
        assert r.status_code == 400

    def test_limit_is_capped_at_200(self, client, root_user):
        r = client.get("/api/audit/events", headers=_auth(root_user),
                       params=_q({"limit": 500}))
        assert r.status_code == 422

    def test_default_limit_is_50(self):
        import inspect
        from app.api.routes.audit import list_events
        assert inspect.signature(list_events).parameters["limit"].default.default == 50

    def test_pagination_offset_moves_the_window(self, client, root_user):
        page1 = client.get("/api/audit/events", headers=_auth(root_user),
                           params=_q({"limit": 1, "offset": 0})).json()
        page2 = client.get("/api/audit/events", headers=_auth(root_user),
                           params=_q({"limit": 1, "offset": 1})).json()
        assert len(page1["items"]) == 1 and len(page2["items"]) == 1
        assert page1["items"][0]["id"] != page2["items"][0]["id"]
        assert page1["total"] == page2["total"] == 3


class TestActionCatalog:
    def test_every_seeded_action_is_in_the_catalog(self, client, root_user):
        body = client.get("/api/audit/actions", headers=_auth(root_user)).json()
        keys = {i["key"] for i in body["items"]}
        for a in ("login_success", "login_failed", "user_delete"):
            assert a in keys
        assert all(i["label"] for i in body["items"])
