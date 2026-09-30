"""
«تلاش دوباره»: a run's left-out listings, tried again inside that run (#58).

«تلاش دوباره برای این آگهی‌ها به اسکرپی که آن‌ها را جا گذاشته وصل نیست.»
A listing a run saved without a number — or could not open — was tried again
by «بازاسکرپ» or «اسکرپ تکی», each a NEW row in the jobs table, or by the
next run of the same city and category as its own candidate. Either way the
counters, the log and the list that changed belonged to another run.

Now POST /api/scraper/jobs/{id}/retry puts the same row back in the queue
with the listings its own list offers, and the worker runs them on it. Here
it goes the whole way — the HTTP request, the row, the queue entry, the
worker's run_scraping_job with the row's config, the retry's loop and the
database — with only the browser replaced (tests/_scrape_harness.py), and
the jobs table read back through the API the panel uses.
"""
import asyncio
import os
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

import _scrape_harness as h  # noqa: E402

PASSWORD = "pw-retry-123456"
FAKE: dict = {}


@pytest.fixture(scope="module")
def client():
    import fakeredis.aioredis
    import app.database as db
    from app.config import get_settings
    h.pg_url()
    cfg = get_settings()
    saved = (cfg.environment, cfg.api_key, cfg.cookies_path, cfg.scrape_scheduler, cfg.match_engine,
             cfg.scrape_worker_enabled)
    cfg.environment, cfg.api_key = "test", ""
    cfg.cookies_path = "/tmp/sorinflow-test-cookies"
    cfg.scrape_scheduler = False
    cfg.match_engine = False
    cfg.scrape_worker_enabled = False      # the `worker` fixture is the only worker here
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    FAKE["redis"] = fake

    async def _get_redis():
        return fake
    saved_get = db.get_redis
    db.get_redis = _get_redis
    import app.services.verification as v
    v.get_redis = _get_redis
    from fastapi.testclient import TestClient
    import app.main as m
    with TestClient(m.app) as c:
        yield c
    db.get_redis = saved_get
    (cfg.environment, cfg.api_key, cfg.cookies_path, cfg.scrape_scheduler, cfg.match_engine,
     cfg.scrape_worker_enabled) = saved


def _db(fn):
    async def _go():
        eng, maker = await h.open_db()
        try:
            return await fn(maker)
        finally:
            await eng.dispose()
    return asyncio.run(_go())


def _user(client, role="admin"):
    from app.auth.jwt import get_password_hash
    from app.models.user import User
    tag = random.randint(10_000, 99_999)

    async def _mk(maker):
        async with maker() as s:
            u = User(username=f"retry_{role}_{tag}", full_name=f"کاربر آزمایشی {tag}", role=role,
                     permissions=["scraper"], hashed_password=get_password_hash(PASSWORD),
                     is_active=True, phone=f"0912{tag:05d}00", phone_verified=True)
            s.add(u)
            await s.commit()
            return u.id, u.username
    uid, username = _db(_mk)
    r = client.post("/api/users/token", data={"username": username, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"id": uid, "auth": {"Authorization": f"Bearer {r.json()['access_token']}"}}


@pytest.fixture(scope="module")
def person(client):
    return _user(client)


@pytest.fixture
def worker(monkeypatch):
    """The worker taking a queued row: run_scraping_job with the row's own
    config, over a fake browser. run(job_id, pages, feed) → the fake scraper."""
    from app.api.routes import scraper as sr
    from app.services import scrape_queue
    h.quiet(monkeypatch)

    async def _sweep(**_kw):
        return {"alive": 0, "dead": 0, "unknown": 0}
    monkeypatch.setattr("app.services.divar_session.sweep", _sweep)

    def run(job_id, pages, feed=(), on_open=None):
        made = []

        def build(db_session, proxy_enabled=False, headless=True):
            made.append(h.FakeScraper(db_session, pages=pages, feed=feed, on_open=on_open))
            return made[-1]
        monkeypatch.setattr(sr, "DivarScraper", build)

        async def _go(maker):
            row = await h.job_row(maker, job_id)
            kwargs = scrape_queue.job_kwargs(row)
            await sr.run_scraping_job(**kwargs)
            return kwargs
        made_kwargs = _db(_go)
        if not made:
            return None           # the row was not pending: nothing ran
        made[0].kwargs = made_kwargs
        return made[0]
    return run


def _listed(client, who, job_id):
    items = client.get("/api/scraper/jobs?limit=100", headers=who["auth"]).json()["items"]
    return next(i for i in items if i["job_id"] == job_id)


def _rows_of(owner):
    from sqlalchemy import select
    from app.models.scraping_job import ScrapingJob

    async def _go(maker):
        async with maker() as s:
            rows = (await s.execute(select(ScrapingJob.job_id, ScrapingJob.config))).all()
        return sorted(str(j) for j, cfg in rows if (cfg or {}).get("owner_user_id") == owner)
    return _db(_go)


def _first_run(client, who, worker, pages):
    """A search run of three listings: one saved without a number, one whose
    page would not open, one saved with its number."""
    lost, broken, fine = h.token(), h.token(), h.token()
    r = client.post("/api/scraper/start", json={"city": h.CITY, "category": h.CATEGORY,
                                                "max_items": 10, "download_images": False},
                    headers=who["auth"])
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    worker(job_id, {lost: h.page(lost), broken: h.BROKEN,
                    fine: h.page(fine, phone=h.PHONE.format(701)), **pages},
           feed=[lost, broken, fine])
    row = _listed(client, who, job_id)
    assert row["status"] == "completed"
    assert (row["new_items"], row["updated_items"], row["failed_items"]) == (1, 0, 2), row
    return job_id, lost, broken, fine


def _skipped(client, who, job_id):
    return client.get(f"/api/scraper/jobs/{job_id}/skipped", headers=who["auth"]).json()


class TestTheRetryIsTheSameRow:
    def test_it_queues_the_same_row_and_opens_no_new_one(self, client, person, worker, monkeypatch):
        from app.services import scrape_queue
        queued = []

        async def enqueue(job_id):
            queued.append(job_id)
        job_id, lost, broken, _ = _first_run(client, person, worker, {})
        monkeypatch.setattr(scrape_queue, "enqueue", enqueue)
        before = _rows_of(person["id"])
        r = client.post(f"/api/scraper/jobs/{job_id}/retry", json={}, headers=person["auth"])
        assert r.status_code == 200, r.text
        assert r.json() == {"job_id": job_id, "status": "pending", "count": 2}
        assert _rows_of(person["id"]) == before, "a new task row was opened"
        row = _db(lambda m: h.job_row(m, job_id))
        assert row.status == "pending" and row.completed_at is None
        assert sorted(i["divar_id"] for i in row.config["retry"]["items"]) == sorted([lost, broken])
        assert queued == [job_id]

    def test_the_worker_runs_it_on_that_row_and_the_counters_move_there(self, client, person, worker):
        job_id, lost, broken, fine = _first_run(client, person, worker, {})
        client.post(f"/api/scraper/jobs/{job_id}/retry", json={}, headers=person["auth"])
        s = worker(job_id, {lost: h.page(lost, phone=h.PHONE.format(702)),
                            broken: h.page(broken, phone=h.PHONE.format(703))})
        assert sorted(s.opened) == sorted([lost, broken]), "the retry opened something else"
        assert fine not in s.opened
        row = _listed(client, person, job_id)
        assert row["status"] == "completed"
        # the numberless one was stored already: «بروز»; the broken one is new
        assert (row["new_items"], row["updated_items"], row["failed_items"]) == (2, 1, 0), row
        assert row["finish_reason"].startswith("تلاش دوباره: از 2 آگهی، 2 ذخیره شد"), row["finish_reason"]
        assert _skipped(client, person, job_id)["items"] == [], "the list still offers them"
        assert _db(lambda m: h.property_row(m, lost)).phone_number == h.PHONE.format(702)

    def test_its_log_is_that_row_s_log(self, client, person, worker):
        job_id, lost, broken, _ = _first_run(client, person, worker, {})
        client.post(f"/api/scraper/jobs/{job_id}/retry", json={}, headers=person["auth"])
        worker(job_id, {lost: h.page(lost, phone=h.PHONE.format(704)), broken: h.BROKEN})
        lines = _db(lambda m: h.log_lines(m, job_id))
        assert any(m.startswith("«تلاش دوباره» برای 2 آگهیِ اسکرپ‌نشدهٔ همین اسکرپ در صف") for m in lines), lines
        assert any(m.startswith("تلاش دوباره برای 2 آگهیِ اسکرپ‌نشدهٔ همین اسکرپ — در همین اسکرپ")
                   for m in lines), lines
        assert any(m.startswith("تلاش دوباره: از 2 آگهی، 1 ذخیره شد؛ هنوز اسکرپ‌نشده: 1 ") for m in lines), lines

    def test_one_still_without_a_number_is_one_row_on_the_list_not_two(self, client, person, worker):
        job_id, lost, broken, _ = _first_run(client, person, worker, {})
        client.post(f"/api/scraper/jobs/{job_id}/retry", json={"reason": "no_phone"},
                    headers=person["auth"])
        s = worker(job_id, {lost: h.page(lost)})
        assert s.opened == [lost], "a bucket's retry opened another bucket too"
        items = _skipped(client, person, job_id)["items"]
        assert sorted((i["divar_id"], i["reason"]) for i in items) == \
            sorted([(lost, "no_phone"), (broken, "failed")])
        (again,) = [i for i in items if i["divar_id"] == lost]
        assert "تلاش دوباره" in again["detail"] and "اجرای بعدی" not in again["detail"]
        row = _listed(client, person, job_id)
        assert (row["new_items"], row["updated_items"], row["failed_items"]) == (1, 0, 2), row

    def test_the_status_it_had_comes_back(self, client, person, worker):
        from sqlalchemy import update
        from app.models.scraping_job import ScrapingJob
        job_id, lost, _, _ = _first_run(client, person, worker, {})

        async def _partial(maker):
            async with maker() as s:
                await s.execute(update(ScrapingJob).where(ScrapingJob.job_id == uuid.UUID(job_id))
                                .values(status="partial", finish_reason="دیوار صفحهٔ ۲ را رد کرد"))
                await s.commit()
        _db(_partial)
        client.post(f"/api/scraper/jobs/{job_id}/retry", json={"divar_ids": [lost]},
                    headers=person["auth"])
        worker(job_id, {lost: h.page(lost, phone=h.PHONE.format(705))})
        row = _listed(client, person, job_id)
        assert row["status"] == "partial", "a retry made a cut-short run look complete"
        assert row["finish_reason"] == \
            "تلاش دوباره: از 1 آگهی، 1 ذخیره شد (0 تازه، 1 بروز)؛ دیوار صفحهٔ ۲ را رد کرد"
        # a second retry does not stack the first one's line
        client.post(f"/api/scraper/jobs/{job_id}/retry", json={}, headers=person["auth"])
        worker(job_id, {})
        row = _listed(client, person, job_id)
        assert row["finish_reason"].count("تلاش دوباره") == 1, row["finish_reason"]
        assert row["finish_reason"].endswith("؛ دیوار صفحهٔ ۲ را رد کرد")


class TestWhoMayAndWhen:
    def test_a_run_still_going_is_refused(self, client, person, worker):
        job_id, *_ = _first_run(client, person, worker, {})
        client.post(f"/api/scraper/jobs/{job_id}/retry", json={}, headers=person["auth"])
        r = client.post(f"/api/scraper/jobs/{job_id}/retry", json={}, headers=person["auth"])
        assert r.status_code == 409 and "در حال اجراست" in r.json()["detail"]

    def test_nothing_left_out_is_refused(self, client, person, worker):
        job_id, lost, broken, _ = _first_run(client, person, worker, {})
        r = client.post(f"/api/scraper/jobs/{job_id}/retry", json={"divar_ids": ["nothere1"]},
                        headers=person["auth"])
        assert r.status_code == 409
        assert _db(lambda m: h.job_row(m, job_id)).status == "completed"

    def test_somebody_else_s_run_is_refused(self, client, person, worker):
        job_id, *_ = _first_run(client, person, worker, {})
        other = _user(client)
        r = client.post(f"/api/scraper/jobs/{job_id}/retry", json={}, headers=other["auth"])
        assert r.status_code == 403
        assert _db(lambda m: h.job_row(m, job_id)).status == "completed"

    def test_root_may_but_it_runs_on_the_owner_s_numbers(self, client, person, worker):
        job_id, lost, _, _ = _first_run(client, person, worker, {})
        root = _user(client, role="root")
        r = client.post(f"/api/scraper/jobs/{job_id}/retry", json={"divar_ids": [lost]},
                        headers=root["auth"])
        assert r.status_code == 200, r.text
        s = worker(job_id, {lost: h.page(lost, phone=h.PHONE.format(706))})
        assert s.kwargs["owner_user_id"] == person["id"]
        assert s.owner_user_id == person["id"], "the retry ran on somebody else's numbers"


class TestTheQueueKeepsIt:
    def test_the_sweep_does_not_fail_an_old_run_just_put_back(self, client, person, worker):
        """The sweep fails a row pending for a day. A week-old run retried a
        moment ago is not that."""
        from sqlalchemy import update
        from app.models.scraping_job import ScrapingJob
        from app.services import scrape_queue
        job_id, *_ = _first_run(client, person, worker, {})

        async def _old(maker):
            async with maker() as s:
                await s.execute(update(ScrapingJob).where(ScrapingJob.job_id == uuid.UUID(job_id))
                                .values(created_at=datetime.now(timezone.utc) - timedelta(days=7)))
                await s.commit()
        _db(_old)
        client.post(f"/api/scraper/jobs/{job_id}/retry", json={}, headers=person["auth"])

        async def _sweep():
            import fakeredis.aioredis
            import app.database as database
            fresh = fakeredis.aioredis.FakeRedis(decode_responses=True)   # its entry lost

            async def _get():
                return fresh
            saved, database.get_redis = database.get_redis, _get
            try:
                await scrape_queue.sweep()
                return await fresh.lrange(scrape_queue.QUEUE, 0, -1)
            finally:
                database.get_redis = saved
        requeued = asyncio.run(_sweep())
        assert _db(lambda m: h.job_row(m, job_id)).status == "pending", "failed as a day-old pending row"
        assert job_id in requeued

    def test_the_pending_age_counts_from_the_retry(self):
        from app.services.scrape_queue import queued_at
        made = datetime(2026, 9, 1, tzinfo=timezone.utc)
        at = "2026-09-29T10:00:00+00:00"
        assert queued_at(made, {"retry": {"at": at}}) == datetime.fromisoformat(at)
        assert queued_at(made, {}) == made
        assert queued_at(made, {"retry": {"at": "garbage"}}) == made


class TestCancellingARetryEndsTheRetryNotTheRun:
    """Review of #58: a cancel pressed on a retry left a finished run
    «لغو‌شده» with no finish line, and offered «ادامه» — a whole new scrape."""

    def test_before_it_starts(self, client, person, worker):
        job_id, lost, broken, _ = _first_run(client, person, worker, {})
        client.post(f"/api/scraper/jobs/{job_id}/retry", json={}, headers=person["auth"])
        r = client.post(f"/api/scraper/jobs/{job_id}/cancel", headers=person["auth"])
        assert r.status_code == 200, r.text
        row = _listed(client, person, job_id)
        assert row["status"] == "completed", row["status"]
        assert row["finish_reason"].startswith("تلاش دوباره لغو شد"), row["finish_reason"]
        assert row["can_resume"] is False, "a cancelled retry offers a whole new scrape"
        assert row["completed_at"]
        assert worker(job_id, {lost: h.page(lost, phone=h.PHONE.format(711))}) is None
        # a retry after it does not stack the cancelled one's line
        client.post(f"/api/scraper/jobs/{job_id}/retry", json={}, headers=person["auth"])
        worker(job_id, {lost: h.page(lost, phone=h.PHONE.format(712))})
        row = _listed(client, person, job_id)
        assert "لغو" not in row["finish_reason"], row["finish_reason"]

    def test_while_it_runs(self, client, person, worker):
        job_id, lost, broken, _ = _first_run(client, person, worker, {})
        client.post(f"/api/scraper/jobs/{job_id}/retry", json={}, headers=person["auth"])

        async def cancel_on_first(_tok):
            if not getattr(cancel_on_first, "done", False):
                cancel_on_first.done = True
                r = client.post(f"/api/scraper/jobs/{job_id}/cancel", headers=person["auth"])
                assert r.status_code == 200, r.text
        s = worker(job_id, {lost: h.page(lost, phone=h.PHONE.format(713)),
                            broken: h.page(broken, phone=h.PHONE.format(714))},
                   on_open=cancel_on_first)
        assert len(s.opened) == 1, "the retry went on after the cancel"
        row = _db(lambda m: h.job_row(m, job_id))
        assert row.status == "completed" and "retry" not in row.config
        assert row.finish_reason.startswith("تلاش دوباره لغو شد")


class TestWhatARetryCannotChangeIsLeftOut:
    """Review of #58: «همه» retried chat-only listings, found them stored and
    counted them «از قبل با شماره بود» — and forgot their rows."""

    def test_all_leaves_chat_only_out(self, client, person, worker, monkeypatch):
        from app.services import scrape_queue

        async def enqueue(_job_id):
            return None
        quiet = h.token()
        lost, broken, fine = h.token(), h.token(), h.token()
        r = client.post("/api/scraper/start", json={"city": h.CITY, "category": h.CATEGORY,
                                                    "max_items": 10, "download_images": False},
                        headers=person["auth"])
        job_id = r.json()["job_id"]
        worker(job_id, {lost: h.page(lost), broken: h.BROKEN, quiet: h.page(quiet, channel="chat_only"),
                        fine: h.page(fine, phone=h.PHONE.format(721))}, feed=[lost, broken, quiet, fine])
        items = _skipped(client, person, job_id)["items"]
        bulk = {i["divar_id"]: i["retryable_in_bulk"] for i in items}
        assert bulk == {lost: True, broken: True, quiet: False}, bulk
        monkeypatch.setattr(scrape_queue, "enqueue", enqueue)
        r = client.post(f"/api/scraper/jobs/{job_id}/retry", json={}, headers=person["auth"])
        assert r.json()["count"] == 2
        r = client.post(f"/api/scraper/jobs/{job_id}/retry", json={"reason": "chat_only"},
                        headers=person["auth"])
        assert r.status_code == 409, "a bucket a retry cannot change was queued"

    def test_named_it_is_opened_again_not_counted_as_held(self, client, person, worker):
        quiet = h.token()
        lost, broken, fine = h.token(), h.token(), h.token()
        r = client.post("/api/scraper/start", json={"city": h.CITY, "category": h.CATEGORY,
                                                    "max_items": 10, "download_images": False},
                        headers=person["auth"])
        job_id = r.json()["job_id"]
        worker(job_id, {lost: h.page(lost), broken: h.BROKEN, quiet: h.page(quiet, channel="chat_only"),
                        fine: h.page(fine, phone=h.PHONE.format(722))}, feed=[lost, broken, quiet, fine])
        r = client.post(f"/api/scraper/jobs/{job_id}/retry", json={"divar_ids": [quiet]},
                        headers=person["auth"])
        assert r.status_code == 200, r.text
        s = worker(job_id, {quiet: h.page(quiet, channel="chat_only")})
        assert s.opened == [quiet], "a stored chat-only listing was taken for one held with its number"
        row = _listed(client, person, job_id)
        assert "با شماره بود" not in row["finish_reason"], row["finish_reason"]
        items = _skipped(client, person, job_id)["items"]
        assert [(i["divar_id"], i["reason"]) for i in items if i["divar_id"] == quiet] == [(quiet, "chat_only")]


class TestEveryRowOfAListingIsTakenOff:
    def test_two_rows_two_counts(self, client, person, worker):
        """Review of #58: a listing with two rows in one run had both rows
        deleted and one count taken off."""
        from app.models.scraping_job import ScrapingJob, SkippedListing
        from sqlalchemy import select
        job_id, lost, broken, _ = _first_run(client, person, worker, {})

        async def _twice(maker):
            async with maker() as s:
                job = (await s.execute(select(ScrapingJob).where(
                    ScrapingJob.job_id == uuid.UUID(job_id)))).scalar_one()
                s.add(SkippedListing(job_id=job.job_id, divar_id=broken, url=h.url_of(broken),
                                     reason="failed", detail="صفحه باز نشد"))
                job.failed_items = (job.failed_items or 0) + 1
                out = dict(job.config["outcome"])
                failed = dict(out.get("failed") or {})
                failed["صفحه باز نشد"] = failed.get("صفحه باز نشد", 0) + 1
                out["failed"] = failed
                job.config = {**job.config, "outcome": out}
                await s.commit()
        _db(_twice)
        client.post(f"/api/scraper/jobs/{job_id}/retry", json={}, headers=person["auth"])
        worker(job_id, {lost: h.page(lost, phone=h.PHONE.format(731)),
                        broken: h.page(broken, phone=h.PHONE.format(732))})
        row = _db(lambda m: h.job_row(m, job_id))
        assert row.failed_items == 0, row.failed_items
        assert not (row.config["outcome"].get("failed") or {}), row.config["outcome"]
