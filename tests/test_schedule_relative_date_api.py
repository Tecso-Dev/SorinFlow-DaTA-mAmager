"""
A schedule's publish date, through the real routes (#33).

Create, list, edit, run now and delete each get a behavioural test here, and
so does deleting a user who has schedules. The date logic itself is in
test_schedule_relative_date.py; this checks it is wired to the app: what is
stored, what the panel is sent, what a firing launches, and that a schedule
still only ever runs as its owner.

Needs Postgres, like test_scrape_schedules.py. Time is held still by swapping
the `datetime` the scheduler module uses.
"""
import asyncio
import itertools
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_sched_rel_api.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.services import scrape_scheduler as sch   # noqa: E402
from test_schedule_relative_date import _freeze, _tehran   # noqa: E402

# ── through the real app (Postgres, like test_scrape_schedules.py) ────────────

API = "/api/scraper/schedules"


def _engine():
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    eng = create_async_engine(os.environ["DATABASE_URL"])
    return eng, async_sessionmaker(eng, expire_on_commit=False)


def _run(coro_fn):
    async def _go():
        eng, maker = _engine()
        try:
            async with maker() as s:
                return await coro_fn(s)
        finally:
            await eng.dispose()
    return asyncio.run(_go())


def _finish_all_runs():
    """Earlier tests leave runs «running» and the launcher caps those at three;
    a pending row would also be taken for work by a later module's worker."""
    from sqlalchemy import update
    from app.models.scraping_job import ScrapingJob

    async def _go(s):
        await s.execute(update(ScrapingJob).where(
            ScrapingJob.status.in_(("running", "paused", "pending"))).values(status="completed"))
        await s.commit()
    _run(_go)


@pytest.fixture(scope="module")
def client():
    import app.database as db
    from app.config import get_settings
    if not str(db.engine.url).startswith("postgresql"):
        pytest.skip("needs Postgres — see test_auth_roles.py", allow_module_level=True)
    cfg = get_settings()
    saved = (cfg.environment, cfg.api_key, cfg.scrape_scheduler, cfg.match_engine,
             cfg.scrape_worker_enabled)
    cfg.environment, cfg.api_key = "test", ""
    # The loop must not fire during the test, and a worker in the app would
    # take the pending rows these make for real work.
    cfg.scrape_scheduler = cfg.match_engine = cfg.scrape_worker_enabled = False
    from _fake_redis import redis_factory
    get_redis = redis_factory()
    import app.services.verification as v
    from app.scraper import otp_store
    from app.api.routes import auth as auth_routes
    db.get_redis = v.get_redis = otp_store.get_redis = auth_routes.get_redis = get_redis
    from fastapi.testclient import TestClient
    import app.main as m
    with TestClient(m.app) as c:
        yield c
    _finish_all_runs()
    (cfg.environment, cfg.api_key, cfg.scrape_scheduler, cfg.match_engine,
     cfg.scrape_worker_enabled) = saved


def _mk_user(username, role="admin", permissions=("scraper",), **extra):
    from app.models.user import User
    from app.auth.jwt import get_password_hash

    async def _go(s):
        u = User(username=username, full_name=username, role=role,
                 hashed_password=get_password_hash("pw123456"),
                 permissions=list(permissions), is_active=True, **extra)
        s.add(u)
        await s.commit()
        await s.refresh(u)
        return u.id
    return _run(_go)


def _login(client, username):
    r = client.post("/api/users/token", data={"username": username, "password": "pw123456"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


_names = itertools.count(1)


def _person(client, username, **kw):
    """A colleague who may use the scraper; returns (id, auth headers). The
    name gets a number of its own, so a parametrized test can ask for the
    same person once per case."""
    username = f"{username}{next(_names)}"
    uid = _mk_user(username, **kw)
    return uid, _login(client, username)


FORM = {"city": "urmia", "category": "rent-apartment", "min_rent": 5000000}


def _create(client, headers, **config):
    r = client.post(API, headers=headers, json={"name": "روزانه", "hour": 8, "minute": 0,
                                                "config": {**FORM, **config}})
    assert r.status_code == 200, r.text
    return r.json()


def _insert_legacy(owner_id, config, created_at, next_run_at=None):
    """A schedule as the old route saved it: a fixed date in the config."""
    from app.models.scrape_schedule import ScrapeSchedule

    async def _go(s):
        row = ScrapeSchedule(owner_user_id=owner_id, name="قدیمی", hour=8, minute=0, enabled=True,
                             config={**FORM, "max_items": 30, **config}, created_at=created_at,
                             next_run_at=next_run_at)
        s.add(row)
        await s.commit()
        await s.refresh(row)
        return row.id
    return _run(_go)


def _row(schedule_id):
    from app.models.scrape_schedule import ScrapeSchedule

    async def _go(s):
        return await s.get(ScrapeSchedule, schedule_id)
    return _run(_go)


def _audit_rows(schedule_id):
    """(action, summary, detail) for one schedule, oldest first."""
    from sqlalchemy import select
    from app.models.audit_event import AuditEvent

    async def _go(s):
        return (await s.execute(
            select(AuditEvent.action, AuditEvent.summary, AuditEvent.detail)
            .where(AuditEvent.target_type == "scrape_schedule",
                   AuditEvent.target_id == str(schedule_id))
            .order_by(AuditEvent.created_at, AuditEvent.id))).all()
    return _run(_go)


def _launcher(monkeypatch):
    """Replace the launcher with one that records what it was asked to run."""
    seen = []

    async def fake_launch(job_config, db, current_user, resumed_from=None, interactive=True):
        seen.append({"user_id": current_user.id, "cfg": job_config.model_dump(exclude_none=True)})
        return SimpleNamespace(job_id="cafe0000-0000-0000-0000-000000000000")
    import app.api.routes.scraper as routes
    monkeypatch.setattr(routes, "_launch_job", fake_launch)
    return seen


class TestCreate:

    def test_a_relative_date_is_saved_as_asked_and_shown_in_words(self, client):
        _, me = _person(client, "rd_create")
        s = _create(client, me, posted_days_ago=1)
        assert s["config"]["posted_days_ago"] == 1
        assert "posted_date" not in s["config"], "a fixed day would repeat itself"
        assert "max_age_hours" not in s["config"], "a day is named: no «last 24 hours» beside it"
        assert (s["posted_days_ago"], s["posted_label"], s["date_note"]) == (1, "دیروز", None)
        assert _row(s["id"]).config == s["config"], "the database holds the relative form"

    @pytest.mark.parametrize("ago, label", [(0, "امروز"), (1, "دیروز"), (3, "۳ روز پیش")])
    def test_today_yesterday_and_n_days_ago_are_all_offered(self, client, ago, label):
        _, me = _person(client, "rd_create_n")
        s = _create(client, me, posted_days_ago=ago)
        assert s["posted_days_ago"] == ago and s["posted_label"] == label

    def test_a_form_with_no_date_keeps_the_daily_default(self, client):
        _, me = _person(client, "rd_nodate")
        s = _create(client, me, max_items=30)
        assert s["config"]["max_age_hours"] == 24 and "posted_days_ago" not in s["config"]
        assert (s["posted_days_ago"], s["posted_label"], s["date_note"]) == (None, None, None)

    def test_a_cap_typed_in_the_form_is_kept_with_the_date(self, client):
        _, me = _person(client, "rd_cap")
        s = _create(client, me, posted_days_ago=1, max_items=40)
        assert s["config"]["max_items"] == 40 and s["config"]["posted_days_ago"] == 1

    def test_an_older_client_that_sends_a_fixed_date_gets_it_stored_relative(self, client, monkeypatch):
        _, me = _person(client, "rd_oldclient")
        _freeze(monkeypatch, _tehran(2026, 9, 28, 9, 0))
        s = _create(client, me, posted_date="2026-09-27")
        assert s["config"]["posted_days_ago"] == 1 and "posted_date" not in s["config"]
        assert "posted_date" not in _row(s["id"]).config

    @pytest.mark.parametrize("bad", [-1, 31, "دیروز", "1", True, 1.5])
    def test_a_date_that_is_not_a_reachable_day_is_refused_when_saved(self, client, bad):
        _, me = _person(client, "rd_bad")
        r = client.post(API, headers=me, json={"name": "x", "config": {**FORM, "posted_days_ago": bad}})
        assert r.status_code == 400, r.text
        assert "امروز" in r.json()["detail"], "the refusal says what is allowed"
        assert client.get(API, headers=me).json()["schedules"] == []

    @pytest.mark.parametrize("fixed", ["2099-01-01", "2020-01-01", "not-a-date"])
    def test_a_fixed_date_that_cannot_be_relative_is_refused(self, client, fixed):
        _, me = _person(client, "rd_badfixed")
        r = client.post(API, headers=me, json={"name": "x", "config": {**FORM, "posted_date": fixed}})
        assert r.status_code == 400, r.text

    def test_creating_is_in_the_audit_log_with_the_date(self, client):
        _, me = _person(client, "rd_audit_create")
        s = _create(client, me, posted_days_ago=2)
        (action, _, detail), = _audit_rows(s["id"])
        assert action == "scrape_schedule_create" and detail["posted_days_ago"] == 2


class TestList:

    def test_a_schedule_saved_with_a_fixed_date_reads_relative_with_a_note(self, client):
        uid, me = _person(client, "rd_legacy")
        # made on the morning of the 28th, with the 27th typed in
        sid = _insert_legacy(uid, {"posted_date": "2026-09-27"}, _tehran(2026, 9, 28, 9, 0))
        (s,) = [x for x in client.get(API, headers=me).json()["schedules"] if x["id"] == sid]
        assert (s["posted_days_ago"], s["posted_label"]) == (1, "دیروز")
        assert s["config"]["posted_days_ago"] == 1 and "posted_date" not in s["config"]
        assert s["date_note"] == "تاریخ ثابت ۱۴۰۵/۰۷/۰۵ به «دیروز» تبدیل شد (نسبت به روز ساخت، ۱۴۰۵/۰۷/۰۶)"
        # converted on the way out; nothing was rewritten
        assert _row(sid).config["posted_date"] == "2026-09-27"

    def test_a_relative_schedule_has_no_note(self, client):
        _, me = _person(client, "rd_nonote")
        _create(client, me, posted_days_ago=1)
        (s,) = client.get(API, headers=me).json()["schedules"]
        assert s["date_note"] is None and s["posted_label"] == "دیروز"

    def test_root_sees_the_converted_date_of_every_colleague(self, client):
        uid, _ = _person(client, "rd_theirs")
        sid = _insert_legacy(uid, {"posted_date": "2026-09-25"}, _tehran(2026, 9, 28, 9, 0))
        _, root = _person(client, "rd_root_list", role="root", permissions=())
        (s,) = [x for x in client.get(API, headers=root).json()["schedules"] if x["id"] == sid]
        assert s["posted_days_ago"] == 3 and s["posted_label"] == "۳ روز پیش"


class TestEdit:

    def test_the_date_can_be_changed_and_taken_off(self, client):
        _, me = _person(client, "rd_edit")
        s = _create(client, me, posted_days_ago=1)
        url = f"{API}/{s['id']}"

        r = client.patch(url, headers=me, json={"posted_days_ago": 3})
        assert r.status_code == 200, r.text
        assert (r.json()["posted_days_ago"], r.json()["posted_label"]) == (3, "۳ روز پیش")
        assert _row(s["id"]).config["posted_days_ago"] == 3

        r = client.patch(url, headers=me, json={"posted_days_ago": 0})
        assert r.json()["posted_label"] == "امروز"

        r = client.patch(url, headers=me, json={"posted_days_ago": None})
        assert r.status_code == 200, r.text
        cfg = r.json()["config"]
        assert "posted_days_ago" not in cfg and cfg["max_age_hours"] == 24, \
            "no date is the last day again, as for a new schedule"
        assert r.json()["posted_label"] is None
        assert "posted_days_ago" not in _row(s["id"]).config

    def test_an_edit_that_leaves_the_date_out_keeps_it(self, client):
        _, me = _person(client, "rd_keep")
        s = _create(client, me, posted_days_ago=2)
        r = client.patch(f"{API}/{s['id']}", headers=me, json={"hour": 9, "enabled": False})
        assert r.status_code == 200
        assert r.json()["posted_days_ago"] == 2 and r.json()["hour"] == 9

    @pytest.mark.parametrize("bad", [-1, 31])
    def test_a_date_out_of_reach_is_refused_and_nothing_else_changes(self, client, bad):
        _, me = _person(client, "rd_editbad")
        s = _create(client, me, posted_days_ago=1)
        r = client.patch(f"{API}/{s['id']}", headers=me, json={"posted_days_ago": bad, "hour": 5})
        assert r.status_code == 400 and "امروز" in r.json()["detail"], r.text
        row = _row(s["id"])
        assert row.config["posted_days_ago"] == 1 and row.hour == 8

    @pytest.mark.parametrize("bad", ["1", True, 1.5])
    def test_a_date_that_is_not_a_whole_number_is_refused(self, client, bad):
        _, me = _person(client, "rd_editshape")
        s = _create(client, me, posted_days_ago=1)
        r = client.patch(f"{API}/{s['id']}", headers=me, json={"posted_days_ago": bad})
        assert r.status_code == 422, r.text

    def test_editing_a_fixed_date_schedule_stores_only_the_relative_form(self, client):
        uid, me = _person(client, "rd_editlegacy")
        sid = _insert_legacy(uid, {"posted_date": "2026-09-27"}, _tehran(2026, 9, 28, 9, 0))
        r = client.patch(f"{API}/{sid}", headers=me, json={"enabled": False})
        assert r.status_code == 200, r.text
        stored = _row(sid).config
        assert stored["posted_days_ago"] == 1 and "posted_date" not in stored
        assert r.json()["posted_label"] == "دیروز" and r.json()["date_note"] is None, \
            "the fixed date is gone, so there is nothing left to explain"
        # converting is not an edit somebody made: one row for the switch, none for the date
        assert [a for a, _, _ in _audit_rows(sid)] == ["scrape_schedule_update"]

    def test_a_new_date_on_a_fixed_date_schedule_replaces_it(self, client):
        uid, me = _person(client, "rd_replacelegacy")
        sid = _insert_legacy(uid, {"posted_date": "2026-09-01"}, _tehran(2026, 9, 28, 9, 0))
        r = client.patch(f"{API}/{sid}", headers=me, json={"posted_days_ago": 1})
        assert r.status_code == 200
        stored = _row(sid).config
        assert stored["posted_days_ago"] == 1 and "posted_date" not in stored

    def test_the_change_is_in_the_audit_log(self, client):
        _, me = _person(client, "rd_editaudit")
        s = _create(client, me, posted_days_ago=1)
        client.patch(f"{API}/{s['id']}", headers=me, json={"posted_days_ago": 4})
        client.patch(f"{API}/{s['id']}", headers=me, json={"posted_days_ago": None})
        rows = _audit_rows(s["id"])
        assert [a for a, _, _ in rows] == ["scrape_schedule_create", "scrape_schedule_update",
                                           "scrape_schedule_update"]
        assert "تاریخ انتشار «۴ روز پیش»" in rows[1][1]
        assert rows[1][2]["changed"]["date"] == [1, 4]
        assert "تاریخ انتشار برداشته شد" in rows[2][1] and rows[2][2]["changed"]["date"] == [4, None]

    def test_somebody_elses_schedule_is_not_theirs_to_edit(self, client):
        _, me = _person(client, "rd_editmine")
        _, other = _person(client, "rd_editother")
        s = _create(client, me, posted_days_ago=1)
        r = client.patch(f"{API}/{s['id']}", headers=other, json={"posted_days_ago": 5})
        assert r.status_code == 404
        assert _row(s["id"]).config["posted_days_ago"] == 1

    def test_root_can_edit_it_and_it_stays_the_owners(self, client):
        uid, theirs = _person(client, "rd_editowner")
        _, root = _person(client, "rd_editroot", role="root", permissions=())
        s = _create(client, theirs, posted_days_ago=1)
        r = client.patch(f"{API}/{s['id']}", headers=root, json={"posted_days_ago": 2})
        assert r.status_code == 200 and r.json()["posted_days_ago"] == 2
        assert _row(s["id"]).owner_user_id == uid


class TestRunNow:

    def test_run_now_goes_for_the_day_it_is_run_on(self, client, monkeypatch):
        _, me = _person(client, "rd_run")
        s = _create(client, me, posted_days_ago=1)
        seen = _launcher(monkeypatch)

        _freeze(monkeypatch, _tehran(2026, 9, 28, 10, 0))
        r = client.post(f"{API}/{s['id']}/run", headers=me)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "started" and r.json()["posted_date"] == "2026-09-27"
        assert "۱۴۰۵/۰۷/۰۵" in r.json()["detail"], "the card says which day it went for"
        assert r.json()["schedule"]["last_result"]["posted_date"] == "2026-09-27"
        assert seen[0]["cfg"]["posted_date"] == "2026-09-27" and "max_age_hours" not in seen[0]["cfg"]

        # the same schedule, the next morning
        _freeze(monkeypatch, _tehran(2026, 9, 29, 10, 0))
        r = client.post(f"{API}/{s['id']}/run", headers=me)
        assert r.json()["posted_date"] == "2026-09-28"
        assert seen[1]["cfg"]["posted_date"] == "2026-09-28"
        # and the saved form did not move
        assert _row(s["id"]).config["posted_days_ago"] == 1

    def test_run_now_of_a_fixed_date_schedule_moves_with_the_day(self, client, monkeypatch):
        """The bug as it happened: a schedule saved with a fixed date searched
        that day again every morning."""
        uid, me = _person(client, "rd_runlegacy")
        sid = _insert_legacy(uid, {"posted_date": "2026-09-27"}, _tehran(2026, 9, 28, 9, 0))
        seen = _launcher(monkeypatch)
        days = []
        for d in (28, 29, 30):
            _freeze(monkeypatch, _tehran(2026, 9, d, 10, 0))
            assert client.post(f"{API}/{sid}/run", headers=me).status_code == 200
            days.append(seen[-1]["cfg"]["posted_date"])
        assert days == ["2026-09-27", "2026-09-28", "2026-09-29"]

    def test_run_now_with_no_date_is_still_the_last_day(self, client, monkeypatch):
        _, me = _person(client, "rd_runnodate")
        s = _create(client, me, max_items=30)
        seen = _launcher(monkeypatch)
        assert client.post(f"{API}/{s['id']}/run", headers=me).json()["status"] == "started"
        assert seen[0]["cfg"]["max_age_hours"] == 24 and "posted_date" not in seen[0]["cfg"]

    def test_root_running_a_colleagues_schedule_runs_it_as_the_colleague(self, client, monkeypatch):
        owner_id, theirs = _person(client, "rd_runowner")
        _, root = _person(client, "rd_runroot", role="root", permissions=())
        s = _create(client, theirs, posted_days_ago=0)
        seen = _launcher(monkeypatch)
        _freeze(monkeypatch, _tehran(2026, 9, 28, 10, 0))
        r = client.post(f"{API}/{s['id']}/run", headers=root)
        assert r.status_code == 200 and r.json()["status"] == "started"
        assert seen[0]["user_id"] == owner_id, "a schedule runs as its owner, whoever pressed the button"
        assert seen[0]["cfg"]["posted_date"] == "2026-09-28"

    def test_somebody_elses_schedule_cannot_be_run(self, client, monkeypatch):
        _, me = _person(client, "rd_runmine")
        _, other = _person(client, "rd_runother")
        s = _create(client, me, posted_days_ago=1)
        seen = _launcher(monkeypatch)
        assert client.post(f"{API}/{s['id']}/run", headers=other).status_code == 404
        assert seen == []

    def test_it_is_the_same_job_a_manual_start_makes_for_that_day(self, client, monkeypatch):
        """Both through the real launcher, and compared as the worker will
        read them: what «اجرای الان» queues is what «شروع» queues for the
        day the schedule means."""
        from app.models.scraping_job import ScrapingJob
        from app.services import scrape_queue
        _finish_all_runs()
        _, me = _person(client, "rd_parity")
        form = {**FORM, "has_parking": True, "download_images": True, "rotate_every": 40}
        s = _create(client, me, posted_days_ago=1, has_parking=True, download_images=True, rotate_every=40)

        _freeze(monkeypatch, _tehran(2026, 9, 28, 8, 0))
        ran = client.post(f"{API}/{s['id']}/run", headers=me).json()
        assert ran["status"] == "started", ran
        manual = client.post("/api/scraper/start", headers=me, json={**form, "posted_date": "2026-09-27"})
        assert manual.status_code == 200, manual.text

        def _kwargs(job_id):
            from sqlalchemy import select

            async def _go(sess):
                row = (await sess.execute(select(ScrapingJob).where(ScrapingJob.job_id == job_id))).scalar_one()
                return scrape_queue.job_kwargs(row)
            kwargs = _run(_go)
            kwargs.pop("job_id")
            return kwargs
        scheduled, by_hand = _kwargs(ran["schedule"]["last_job_id"]), _kwargs(manual.json()["job_id"])
        assert scheduled["posted_date"] == "2026-09-27"
        assert scheduled == by_hand
        _finish_all_runs()


class TestTheClockFiresIt:
    """The loop that fires schedules at their hour — not the button — on the
    two kinds of row there are: one saved relative, one saved with a fixed date."""

    def test_each_days_firing_goes_for_that_days_own_date(self, client, monkeypatch):
        uid, me = _person(client, "rd_tick")
        _freeze(monkeypatch, _tehran(2026, 9, 27, 8, 30))
        saved = _create(client, me, posted_days_ago=1)             # its next 08:00 is on the 28th
        old = _insert_legacy(uid, {"posted_date": "2026-09-26"}, _tehran(2026, 9, 27, 9, 0),
                             next_run_at=_tehran(2026, 9, 28, 8, 0))
        seen = _launcher(monkeypatch)

        def firing_at(moment):
            """What the loop launches for these two schedules at `moment`."""
            _freeze(monkeypatch, moment)
            before = len(seen)
            asyncio.run(sch.tick())
            return sorted(f["cfg"]["posted_date"] for f in seen[before:] if f["user_id"] == uid)

        assert firing_at(_tehran(2026, 9, 27, 12, 0)) == [], "nothing is due before the hour"
        assert firing_at(_tehran(2026, 9, 28, 8, 1)) == ["2026-09-27", "2026-09-27"]
        assert firing_at(_tehran(2026, 9, 28, 8, 2)) == [], "each fires once for the day"
        # the next morning: the same two schedules, a day on
        assert firing_at(_tehran(2026, 9, 29, 8, 1)) == ["2026-09-28", "2026-09-28"]
        assert _row(saved["id"]).config["posted_days_ago"] == 1
        assert _row(old).config["posted_date"] == "2026-09-26", "nothing was rewritten on the way"
        assert _row(saved["id"]).last_result["posted_date"] == "2026-09-28"


class TestDelete:

    def test_a_deleted_schedule_is_gone_and_the_others_stay(self, client):
        _, me = _person(client, "rd_del")
        a = _create(client, me, posted_days_ago=1)
        b = _create(client, me, posted_days_ago=0)
        assert client.delete(f"{API}/{a['id']}", headers=me).status_code == 200
        assert [x["id"] for x in client.get(API, headers=me).json()["schedules"]] == [b["id"]]
        assert client.delete(f"{API}/{a['id']}", headers=me).status_code == 404, "gone means gone"
        assert _row(a["id"]) is None
        (create, delete) = _audit_rows(a["id"])
        assert (create[0], delete[0]) == ("scrape_schedule_create", "scrape_schedule_delete")
        assert delete[2]["posted_days_ago"] == 1, "the record says what was deleted"

    def test_a_fixed_date_schedule_deletes_like_any_other(self, client):
        uid, me = _person(client, "rd_dellegacy")
        sid = _insert_legacy(uid, {"posted_date": "2026-09-27"}, _tehran(2026, 9, 28, 9, 0))
        assert client.delete(f"{API}/{sid}", headers=me).status_code == 200
        assert _row(sid) is None
        assert _audit_rows(sid)[-1][2]["posted_days_ago"] == 1

    def test_somebody_elses_schedule_is_not_theirs_to_delete(self, client):
        _, me = _person(client, "rd_delmine")
        _, other = _person(client, "rd_delother")
        s = _create(client, me, posted_days_ago=1)
        assert client.delete(f"{API}/{s['id']}", headers=other).status_code == 404
        assert _row(s["id"]) is not None


class TestDeletingAUserDeletesTheirSchedules:
    """Fixed on 1405/07/05 (Postgres refused the delete of anybody with a
    schedule); confirmed here with schedules made through the API."""

    def test_the_schedules_go_with_the_person_and_nobody_elses(self, client):
        gone_id, theirs = _person(client, "rd_leaver")
        _, admin = _person(client, "rd_admin", role="root", permissions=())
        kept_id, mine = _person(client, "rd_stayer")
        relative = _create(client, theirs, posted_days_ago=1)
        plain = _create(client, theirs, max_items=20)
        fixed = _insert_legacy(gone_id, {"posted_date": "2026-09-27"}, _tehran(2026, 9, 28, 9, 0))
        stays = _create(client, mine, posted_days_ago=2)

        assert client.delete(f"/api/users/{gone_id}", headers=admin).status_code == 200
        for sid in (relative["id"], plain["id"], fixed):
            assert _row(sid) is None, "a schedule outlived the person it ran for"
        assert _row(stays["id"]) is not None and _row(stays["id"]).owner_user_id == kept_id
        listed = [x["id"] for x in client.get(API, headers=admin).json()["schedules"]]
        assert not {relative["id"], plain["id"], fixed} & set(listed)
        assert stays["id"] in listed

        events = client.get("/api/audit/events", params={"action": "user_delete"}, headers=admin).json()["items"]
        said = [e["summary"] for e in events if "rd_leaver" in (e.get("summary") or "")]
        assert said and "3 زمان‌بندی اسکرپ" in said[0], said
