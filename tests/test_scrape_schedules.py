"""
Saved scrapes that fire themselves.

Between 15 and 18 September the scraper ran once — when somebody pressed
the button. A schedule is the form plus an hour, fired as its owner through
the same launcher a click uses, with the outcome written where the panel
shows it.
"""
import os
import sys
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_sched.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.services import scrape_scheduler as sch   # noqa: E402


class TestTheClock:

    def test_the_next_occurrence_is_tehran_wall_clock(self):
        # 04:00 UTC is 07:30 in Tehran; the next 08:00 Tehran is 04:30 UTC the same day
        now = datetime(2026, 9, 18, 4, 0, tzinfo=timezone.utc)
        nxt = sch.next_occurrence(8, 0, now=now)
        assert nxt == datetime(2026, 9, 18, 4, 30, tzinfo=timezone.utc)

    def test_an_hour_already_passed_today_is_tomorrow(self):
        now = datetime(2026, 9, 18, 10, 0, tzinfo=timezone.utc)   # 13:30 Tehran
        nxt = sch.next_occurrence(8, 0, now=now)
        assert nxt == datetime(2026, 9, 19, 4, 30, tzinfo=timezone.utc)

    def test_the_exact_minute_counts_as_passed(self):
        """Otherwise a firing at 08:00:00 recomputes next_run_at = now and
        fires again a minute later."""
        now = datetime(2026, 9, 18, 4, 30, tzinfo=timezone.utc)
        assert sch.next_occurrence(8, 0, now=now).day == 19


class TestTheDailyDefault:

    def test_no_age_and_no_date_means_the_last_day(self):
        cfg = sch.config_for_run({"city": "urmia", "category": "rent-apartment", "max_items": 50})
        assert cfg["max_age_hours"] == 24

    def test_an_explicit_age_or_date_is_kept(self):
        assert sch.config_for_run({"max_age_hours": 6})["max_age_hours"] == 6
        assert "max_age_hours" not in sch.config_for_run({"posted_date": "2026-09-18"})

    def test_the_owner_key_from_a_stored_run_does_not_leak_into_the_form(self):
        assert "owner_user_id" not in sch.config_for_run({"owner_user_id": 3, "city": "x"})


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


class FakeDb:
    def __init__(self, owner):
        self.owner = owner
        self.commits = 0

    async def execute(self, q):
        return _Result([self.owner] if self.owner else [])

    async def commit(self):
        self.commits += 1


def _schedule(**kw):
    base = dict(id=7, name="ارومیه صبح", owner_user_id=21, hour=8, minute=0, enabled=True,
                config={"city": "urmia", "category": "rent-apartment", "max_items": 30},
                last_job_id=None, last_run_at=None, last_result=None, next_run_at=None)
    base.update(kw)
    return SimpleNamespace(**base)


class TestFiring:

    def test_it_launches_as_the_owner_through_the_real_launcher(self, monkeypatch):
        seen = {}

        async def fake_launch(job_config, db, current_user, resumed_from=None,
                              interactive=True):
            seen["cfg"] = job_config.model_dump(exclude_none=True)
            seen["db"] = db
            seen["user"] = current_user
            # a schedule replays a saved form: nobody is there to pick again
            seen["interactive"] = interactive
            return SimpleNamespace(job_id="1a5e5004-f416-4aaf-8ae7-45f8edc68804")
        import app.api.routes.scraper as routes
        monkeypatch.setattr(routes, "_launch_job", fake_launch)
        owner = SimpleNamespace(id=21, is_active=True, username="sobhan", role="root")
        s = _schedule()
        db = FakeDb(owner)
        res = asyncio.run(sch.fire(s, db))
        assert res["status"] == "started"
        assert seen["user"] is owner, "a schedule must run as its owner — their pool, their permissions"
        assert seen["db"] is db, "the schedule's own session, as a request would pass its own"
        assert seen["interactive"] is False, "a number switched off since must fall back, not fail daily"
        assert seen["cfg"]["max_age_hours"] == 24 and seen["cfg"]["city"] == "urmia"
        assert s.last_job_id.startswith("1a5e5004") and s.last_result["status"] == "started"
        assert s.next_run_at is not None and db.commits == 1

    def test_a_refusal_is_recorded_not_raised(self, monkeypatch):
        """The launcher answers 429 when three runs are already going; the
        loop must survive it and the card must say so."""
        from fastapi import HTTPException

        async def refuse(*a, **kw):
            raise HTTPException(status_code=429, detail="Too many running jobs")
        import app.api.routes.scraper as routes
        monkeypatch.setattr(routes, "_launch_job", refuse)
        s = _schedule()
        res = asyncio.run(sch.fire(s, FakeDb(SimpleNamespace(id=21, is_active=True))))
        assert res == {"status": "failed", "detail": "Too many running jobs"}
        assert s.last_result["status"] == "failed" and s.next_run_at is not None

    def test_a_disabled_owner_is_skipped(self, monkeypatch):
        called = []

        async def never(*a, **kw):
            called.append(1)
        import app.api.routes.scraper as routes
        monkeypatch.setattr(routes, "_launch_job", never)
        s = _schedule()
        res = asyncio.run(sch.fire(s, FakeDb(SimpleNamespace(id=21, is_active=False))))
        assert res["status"] == "skipped" and not called


class TestItIsWiredIn:

    def test_the_launcher_queues_whoever_calls_it(self):
        """A request and a schedule start a run the same way: the row, then
        its id on the queue a worker drains (tested in test_scrape_queue.py)."""
        import inspect
        import app.api.routes.scraper as routes
        src = inspect.getsource(routes._launch_job)
        assert "await scrape_queue.enqueue(job_id)" in src
        assert "background_tasks" not in inspect.signature(routes._launch_job).parameters

    def test_the_table_is_created_at_startup_and_known_to_the_restore(self):
        assert "scrape_schedule" in Path("app/database.py").read_text(encoding="utf-8")
        assert "scrape_schedule" in Path("scripts/restore_backup.py").read_text(encoding="utf-8")

    def test_the_loop_starts_with_the_app(self):
        import app.main as m
        loops = {name: (fn, roles) for name, fn, _stall, roles in m._loops()}
        assert loops["scrape_scheduler"][0] is sch.scheduler_loop
        assert set(loops["scrape_scheduler"][1]) == {"all", "scheduler"}, \
            "one process fires the schedules — two would launch every run twice"

    def test_the_routes_are_scoped_to_the_owner(self):
        src = Path("app/api/routes/scraper.py").read_text(encoding="utf-8")
        body = src[src.index("async def _my_schedule"):src.index("def _schedule_view")]
        assert "ScrapeSchedule.owner_user_id == user.id" in body
        assert "status_code=404" in body, "a wrong id must not reveal whether it exists"
        for route in ('"/schedules"', '"/schedules/{schedule_id}"', '"/schedules/{schedule_id}/run"'):
            assert route in src

    def test_the_form_can_be_saved_as_a_schedule(self):
        html = Path("frontend/index.html").read_text(encoding="utf-8")
        js = Path("frontend/js/app.js").read_text(encoding="utf-8")
        assert 'onclick="saveAsSchedule()"' in html and 'id="schedules-table"' in html
        assert "loadSchedules();" in js.split("case 'scraper':")[1][:200]
        fn = js[js.index("function _scrapeFormConfig"):js.index("// ═══ Scheduled scrapes")]
        for key in ("min_price", "max_rent", "has_parking", "advertiser_type", "rotate_every"):
            assert key in fn, f"{key} is on the form but not in the saved schedule"
        assert "prompt(" not in js[js.index("async function saveAsSchedule"):js.index("async function executeBulkScraping")]


# ── through the real app (Postgres, like test_profile.py) ─────────────────────

@pytest.fixture(scope="module")
def client():
    import fakeredis.aioredis
    import app.database as db
    from app.config import get_settings
    if not str(db.engine.url).startswith("postgresql"):
        pytest.skip("needs Postgres — see test_auth_roles.py", allow_module_level=True)
    cfg = get_settings()
    saved = (cfg.environment, cfg.api_key, cfg.cookies_path, cfg.scrape_scheduler)
    cfg.environment, cfg.api_key = "test", ""
    cfg.cookies_path = "/tmp/sorinflow-test-cookies"
    cfg.scrape_scheduler = False          # the loop must not fire during the test
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)

    async def _get_redis():
        return fake
    db.get_redis = _get_redis
    import app.services.verification as v
    v.get_redis = _get_redis
    from fastapi.testclient import TestClient
    import app.main as m
    with TestClient(m.app) as c:
        yield c
    (cfg.environment, cfg.api_key, cfg.cookies_path, cfg.scrape_scheduler) = saved


def _mk_user(username, role, permissions=None):
    from app.models.user import User
    from app.auth.jwt import get_password_hash
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

    async def _go():
        eng = create_async_engine(os.environ["DATABASE_URL"])
        maker = async_sessionmaker(eng, expire_on_commit=False)
        try:
            async with maker() as s:
                u = User(username=username, full_name=username, role=role,
                         hashed_password=get_password_hash("pw123456"),
                         permissions=permissions or [], is_active=True)
                s.add(u)
                await s.commit()
                await s.refresh(u)
                return u.id
        finally:
            await eng.dispose()
    return asyncio.run(_go())


def _tok(client, username):
    r = client.post("/api/users/token", data={"username": username, "password": "pw123456"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


class TestThroughTheApp:

    def test_save_list_edit_run_delete_scoped_to_the_owner(self, client, monkeypatch):
        _mk_user("sc_owner", "admin", ["scraper"])
        _mk_user("sc_other", "admin", ["scraper"])
        me, other = _tok(client, "sc_owner"), _tok(client, "sc_other")

        r = client.post("/api/scraper/schedules", headers=me, json={
            "name": "ارومیه صبح", "hour": 8, "minute": 30,
            "config": {"city": "urmia", "category": "rent-apartment", "max_items": 30, "min_rent": 5000000}})
        assert r.status_code == 200, r.text
        s = r.json()
        assert s["config"]["max_age_hours"] == 24, "the daily default was not applied"
        assert s["config"]["min_rent"] == 5000000 and s["city_name"] == "ارومیه"
        assert s["next_run_at"]
        sid = s["id"]

        assert [x["id"] for x in client.get("/api/scraper/schedules", headers=me).json()["schedules"]] == [sid]
        assert client.get("/api/scraper/schedules", headers=other).json()["schedules"] == []
        assert client.patch(f"/api/scraper/schedules/{sid}", headers=other, json={"enabled": False}).status_code == 404
        assert client.post(f"/api/scraper/schedules/{sid}/run", headers=other).status_code == 404

        r = client.patch(f"/api/scraper/schedules/{sid}", headers=me, json={"enabled": False, "hour": 9})
        assert r.status_code == 200 and r.json()["enabled"] is False and r.json()["hour"] == 9

        # run-now goes through the real launcher, as the owner; the launcher
        # itself is replaced so no browser starts here
        seen = {}

        async def fake_launch(job_config, db, current_user, resumed_from=None,
                              interactive=True):
            seen["user"] = current_user.username
            seen["cfg"] = job_config.model_dump(exclude_none=True)
            return SimpleNamespace(job_id="cafe0000-0000-0000-0000-000000000000")
        import app.api.routes.scraper as routes
        monkeypatch.setattr(routes, "_launch_job", fake_launch)
        r = client.post(f"/api/scraper/schedules/{sid}/run", headers=me)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "started" and seen["user"] == "sc_owner"
        assert seen["cfg"]["max_age_hours"] == 24
        assert r.json()["schedule"]["last_job_id"].startswith("cafe0000")

        assert client.delete(f"/api/scraper/schedules/{sid}", headers=me).status_code == 200
        assert client.get("/api/scraper/schedules", headers=me).json()["schedules"] == []

    def test_a_bad_city_is_refused_when_saved_not_at_eight_tomorrow(self, client):
        _mk_user("sc_bad", "admin", ["scraper"])
        r = client.post("/api/scraper/schedules", headers=_tok(client, "sc_bad"), json={
            "name": "x", "config": {"city": "atlantis", "category": "rent-apartment"}})
        assert r.status_code == 400

    def test_without_the_scraper_permission_there_is_no_schedule(self, client):
        _mk_user("sc_crm", "admin", ["crm"])
        assert client.get("/api/scraper/schedules", headers=_tok(client, "sc_crm")).status_code == 403
