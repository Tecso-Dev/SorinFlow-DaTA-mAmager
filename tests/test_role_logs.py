"""
Each SorinFlow role now writes its own log file (app/main.py's logger setup)
instead of every role sharing scraper.log. With each role its own pod and
each pod its own emptyDir, a shared name meant the panel's log viewer —
which tails whichever api replica answers — could never show a scrape or a
scheduler-loop line, only that api pod's own. GET /api/stats/logs?log=...
reads any of the three, so the viewer can offer the others too.
"""
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_role_logs.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

NS = f"rolelogs{uuid.uuid4().hex[:6]}"


@pytest.fixture(scope="module")
def client():
    from starlette.testclient import TestClient
    import app.main as m
    return TestClient(m.app)


@pytest.fixture(scope="module", autouse=True)
async def _schema():
    from app.database import engine
    from app.models.user import User
    async with engine.begin() as conn:
        await conn.run_sync(User.__table__.create, checkfirst=True)


@pytest.fixture(scope="module")
async def root_user():
    from app.database import async_session_maker
    from app.models.user import User
    from app.auth.jwt import get_password_hash
    async with async_session_maker() as db:
        user = User(username=f"{NS}_root", hashed_password=get_password_hash("x"),
                   role="root", is_active=True)
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user


def _auth(user):
    from app.auth.jwt import create_access_token, access_claims
    return {"Authorization": f"Bearer {create_access_token(access_claims(user))}"}


@pytest.fixture
def logs_dir(monkeypatch, tmp_path):
    """Every route reads app.config.get_settings().logs_path — the cached
    singleton, so mutating its attribute (monkeypatch restores it after)
    reaches the route without touching real env vars or the lru_cache."""
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "logs_path", str(tmp_path))
    return tmp_path


class TestRoleDeterminesTheLogFilename:

    def test_api_and_scheduler_get_their_own_file(self):
        from app.main import _ROLE_LOG_NAMES
        assert _ROLE_LOG_NAMES.get("api", "scraper.log") == "api.log"
        assert _ROLE_LOG_NAMES.get("scheduler", "scraper.log") == "scheduler.log"

    def test_worker_and_all_keep_scraper_log(self):
        """Local dev, docker-compose.local.yml and the test suite all run
        role=all — the name they have always written must not change."""
        from app.main import _ROLE_LOG_NAMES
        assert _ROLE_LOG_NAMES.get("worker", "scraper.log") == "scraper.log"
        assert _ROLE_LOG_NAMES.get("all", "scraper.log") == "scraper.log"


class TestTheLogViewerPicksTheFile:

    def test_defaults_to_scraper_log(self, client, root_user, logs_dir):
        (logs_dir / "scraper.log").write_text("scraper line\n")
        r = client.get("/api/stats/logs", headers=_auth(root_user))
        assert r.status_code == 200 and r.json()["lines"] == ["scraper line"]

    def test_can_read_the_api_and_scheduler_files_too(self, client, root_user, logs_dir):
        (logs_dir / "api.log").write_text("api line\n")
        (logs_dir / "scheduler.log").write_text("scheduler line\n")
        r = client.get("/api/stats/logs?log=api.log", headers=_auth(root_user))
        assert r.status_code == 200 and r.json()["lines"] == ["api line"]
        r = client.get("/api/stats/logs?log=scheduler.log", headers=_auth(root_user))
        assert r.status_code == 200 and r.json()["lines"] == ["scheduler line"]

    def test_anything_outside_the_whitelist_is_rejected(self, client, root_user, logs_dir):
        """Not a path — a name from a fixed list of three, so this can never
        become an arbitrary-file read off logs_path."""
        (logs_dir.parent / "secrets.env").write_text("SECRET_KEY=x\n")
        r = client.get("/api/stats/logs?log=../secrets.env", headers=_auth(root_user))
        assert r.status_code == 422
        r = client.get("/api/stats/logs?log=scraper.log.bak", headers=_auth(root_user))
        assert r.status_code == 422
