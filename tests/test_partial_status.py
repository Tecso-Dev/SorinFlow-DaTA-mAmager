"""
A run whose collection was cut short finishes «ناقص», not «تکمیل شده» (#28).

Run 47 asked for 50 new shops. Divar said it had 105; page 2 of the search
was refused, 24 candidates were collected, 19 saved — and the row read
«تکمیل شده», 105 / 105. The decision: a run that stopped short for any
reason other than reaching the end of Divar's list or its own target ends
with the status «partial» (ناقص), its reason on the row, and «ادامه»
offered like on a failed or cancelled run.

A new value in a String(50) column: no migration, and the previous code
reads it as just another status string.
"""
import asyncio
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_partial.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.models.scraping_job import ScrapingJob  # noqa: E402

REASON = ("دیوار صفحهٔ 2 جست‌وجو را رد کرد (HTTP 400: invalid filter for shop-sell: credit). "
          "24 نامزد از 105 آگهیِ دیوار جمع شد. فیلتر «ودیعه» برای دستهٔ «خرید مغازه» معتبر نیست؛ "
          "آن را خالی کنید و دوباره اجرا کنید.")
CONFIG = {"city": "urmia", "category": "buy-store", "max_items": 50, "download_images": False,
          "max_deposit": 100_000_000, "advertiser_type": "personal"}


class TestTheModel:

    @pytest.mark.parametrize("status,offered", [
        ("partial", True), ("failed", True), ("cancelled", True),
        ("completed", False), ("running", False), ("pending", False), ("paused", False),
    ])
    def test_resume_is_offered_on_a_run_that_stopped_short(self, status, offered):
        job = ScrapingJob(status=status, config=dict(CONFIG))
        assert job.can_resume is offered
        assert job.to_dict()["can_resume"] is offered

    def test_not_without_the_settings_to_resume_from(self):
        assert ScrapingJob(status="partial", config=None).can_resume is False

    def test_partial_is_a_finished_status(self):
        from app.models.scraping_job import FINISHED_STATUSES, RESUMABLE_STATUSES
        assert "partial" in FINISHED_STATUSES and "partial" in RESUMABLE_STATUSES
        assert "completed" in FINISHED_STATUSES and "completed" not in RESUMABLE_STATUSES

    def test_a_reason_longer_than_the_column_is_shortened_not_refused(self):
        """finish_reason is String(300). A partial reason carries Divar's own
        message and the filter tally is appended to it; Postgres refuses a
        longer value, and the commit that carries it takes the run down."""
        job = ScrapingJob(status="partial")
        job.finish_reason = "ب" * 400
        assert len(job.finish_reason) == 300 and job.finish_reason.endswith("…")
        job.finish_reason = REASON
        assert job.finish_reason == REASON
        job.finish_reason = None
        assert job.finish_reason is None


# ── through the real app (Postgres) ──────────────────────────────────────────

@pytest.fixture(scope="module")
def client():
    import fakeredis.aioredis
    import app.database as db
    from app.config import get_settings
    if not str(db.engine.url).startswith("postgresql"):
        pytest.skip("needs Postgres — see test_auth_roles.py", allow_module_level=True)
    cfg = get_settings()
    saved = (cfg.environment, cfg.api_key, cfg.cookies_path, cfg.scrape_scheduler, cfg.match_engine,
             cfg.scrape_worker_enabled)
    cfg.environment, cfg.api_key = "test", ""
    cfg.cookies_path = "/tmp/sorinflow-test-cookies"
    cfg.scrape_scheduler = False
    cfg.match_engine = False
    # a resumed run is queued, and nothing here may pick it up and open a
    # browser towards Divar
    cfg.scrape_worker_enabled = False
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
    (cfg.environment, cfg.api_key, cfg.cookies_path, cfg.scrape_scheduler, cfg.match_engine,
     cfg.scrape_worker_enabled) = saved


def _run(coro_fn):
    """Run one coroutine on its own engine, the way the other HTTP suites
    seed: the app's engine belongs to the TestClient's loop."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

    async def _go():
        eng = create_async_engine(os.environ["DATABASE_URL"])
        try:
            return await coro_fn(async_sessionmaker(eng, expire_on_commit=False))
        finally:
            await eng.dispose()
    return asyncio.run(_go())


_ids: dict = {}


def _seed():
    """One person, a partial run and a completed one of theirs."""
    if _ids:
        return _ids
    from app.models.user import User
    from app.auth.jwt import get_password_hash

    async def _go(maker):
        async with maker() as s:
            boss = User(username="ps_boss", full_name="مدیر آزمایشی ناقص", role="super_admin",
                        permissions=["scraper"], hashed_password=get_password_hash("pw123456"),
                        is_active=True)
            s.add(boss)
            await s.flush()
            cfg = {**CONFIG, "owner_user_id": boss.id}
            partial = ScrapingJob(status="partial", config=cfg, finish_reason=REASON,
                                  new_items=19, updated_items=2, total_items=105, scraped_items=24)
            done = ScrapingJob(status="completed", config=cfg, finish_reason="آن روز بیش از این آگهی نداشت")
            s.add_all([partial, done])
            await s.commit()
            return {"boss": boss.id, "partial": str(partial.job_id), "partial_id": partial.id,
                    "done": str(done.job_id)}
    _ids.update(_run(_go))
    return _ids


def _tok(client, username):
    r = client.post("/api/users/token", data={"username": username, "password": "pw123456"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


class TestThroughTheApp:

    def test_the_list_says_partial_offers_resume_and_carries_the_reason(self, client):
        ids = _seed()
        items = client.get("/api/scraper/jobs?limit=100", headers=_tok(client, "ps_boss")).json()["items"]
        by_id = {i["job_id"]: i for i in items}
        row = by_id[ids["partial"]]
        assert row["status"] == "partial" and row["can_resume"] is True
        assert row["finish_reason"] == REASON, "the reason belongs on the row, not only in the log"
        done = by_id[ids["done"]]
        assert done["can_resume"] is False
        assert done["finish_reason"] == "آن روز بیش از این آگهی نداشت"

    def test_one_run_says_the_same(self, client):
        ids = _seed()
        for key in (ids["partial"], str(ids["partial_id"])):
            row = client.get(f"/api/scraper/jobs/{key}", headers=_tok(client, "ps_boss")).json()
            assert row["status"] == "partial" and row["can_resume"] is True
            assert row["finish_reason"] == REASON

    def test_the_status_filter_finds_it(self, client):
        ids = _seed()
        items = client.get("/api/scraper/jobs?status=partial&limit=100",
                           headers=_tok(client, "ps_boss")).json()["items"]
        assert ids["partial"] in {i["job_id"] for i in items}
        assert {i["status"] for i in items} == {"partial"}

    def test_a_partial_run_has_finished_so_there_is_nothing_to_cancel(self, client):
        ids = _seed()
        r = client.post(f"/api/scraper/jobs/{ids['partial']}/cancel", headers=_tok(client, "ps_boss"))
        assert r.status_code == 400 and "ناقص" in r.json()["detail"]

    def test_it_can_be_resumed_as_its_owner(self, client):
        ids = _seed()
        r = client.post(f"/api/scraper/jobs/{ids['partial']}/resume", headers=_tok(client, "ps_boss"))
        assert r.status_code == 200, r.text
        new = r.json()
        assert new["status"] == "pending" and new["job_id"] != ids["partial"]
        row = client.get(f"/api/scraper/jobs/{new['job_id']}", headers=_tok(client, "ps_boss")).json()
        assert row["resumed_from"] == ids["partial"]

        async def _config(maker):
            from sqlalchemy import select
            async with maker() as s:
                return (await s.execute(select(ScrapingJob.config).where(
                    ScrapingJob.job_id == uuid.UUID(new["job_id"])))).scalar_one()
        cfg = _run(_config)
        assert cfg["category"] == "buy-store" and cfg["max_items"] == 50
        assert cfg["owner_user_id"] == ids["boss"]

    def test_the_morning_digest_counts_it(self, client):
        _seed()
        r = client.get("/api/backup/digest", headers=_tok(client, "ps_boss"))
        assert r.status_code == 200, r.text
        n = r.json()["counts"]
        assert n["jobs_partial"] >= 1
        assert f" · {n['jobs_partial']} ناقص" in r.json()["text"]

    def test_a_partial_run_can_be_deleted(self, client):
        ids = _seed()
        r = client.delete(f"/api/scraper/jobs/{ids['partial']}", headers=_tok(client, "ps_boss"))
        assert r.status_code == 200, r.text
        gone = client.get(f"/api/scraper/jobs/{ids['partial']}", headers=_tok(client, "ps_boss"))
        assert gone.status_code == 404

    def test_a_long_reason_reaches_the_database_shortened(self, client):
        """On Postgres a 301st character is an error at commit, not a warning."""
        async def _go(maker):
            async with maker() as s:
                job = ScrapingJob(status="partial", finish_reason="ی" * 500)
                s.add(job)
                await s.commit()
                return job.job_id

        async def _read(maker, job_id):
            from sqlalchemy import select
            async with maker() as s:
                return (await s.execute(select(ScrapingJob.finish_reason).where(
                    ScrapingJob.job_id == job_id))).scalar_one()
        job_id = _run(_go)
        stored = _run(lambda maker: _read(maker, job_id))
        assert len(stored) == 300 and stored.endswith("…")


class TestWhatTheReviewFound:
    """Found in the review of the merged fixes."""

    def test_resuming_a_run_whose_settings_no_longer_read_is_a_409_not_a_500(self, client):
        """The queue fails such a row with «ادامه will not work»; the button
        still showed, and pressing it answered 500."""
        ids = _seed()

        async def _go(maker):
            async with maker() as s:
                bad = ScrapingJob(status="failed", finish_reason="تنظیمات خوانا نبود",
                                  config={**CONFIG, "owner_user_id": ids["boss"],
                                          "max_items": "not-a-number"})
                s.add(bad)
                await s.commit()
                return str(bad.job_id)
        bad_id = _run(_go)
        r = client.post(f"/api/scraper/jobs/{bad_id}/resume", headers=_tok(client, "ps_boss"))
        assert r.status_code == 409, r.text
        assert "ادامه" in r.json()["detail"]

    def test_monitoring_counts_a_partial_run_as_the_scraper_working(self, client):
        """«آخرین تسک موفق» and its 48-hour warning read only «completed», so
        a city whose runs end «ناقص» looked like an idle scraper."""
        from datetime import datetime, timedelta
        ids = _seed()
        when = datetime.now() + timedelta(days=3650)       # later than any other row

        async def _go(maker):
            async with maker() as s:
                s.add(ScrapingJob(status="partial", config={**CONFIG, "owner_user_id": ids["boss"]},
                                  completed_at=when))
                await s.commit()
        _run(_go)
        body = client.get("/api/monitoring/overview", headers=_tok(client, "ps_boss")).json()
        assert body["scraper"]["last_completed_at"].startswith(when.date().isoformat()), body["scraper"]
