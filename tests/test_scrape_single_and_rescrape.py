"""
«اسکرپ تکی» and «بازاسکرپ», end to end (#32).

Both are the main way a listing without a number gets one, and since they
became jobs (a job of one, a job of many) nothing had run them: the tests
read their source. Here each goes the whole way — the HTTP request, the job
row, the worker's run_scraping_job, the scraper's loop and the database —
with only the browser replaced (tests/_scrape_harness.py), and the jobs
table is read back through the API the panel uses.

The table's fields for #29 are checked here too, through the same API:
finish_reason, the reason line under «تازه», «کاربر حذف‌شده», and
«ناقص» being continuable.
"""
import asyncio
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

import _scrape_harness as h  # noqa: E402

PASSWORD = "pw-single-123456"


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
    # The app's own queue worker would run the queued job a second time, on
    # a real browser. The `worker` fixture below is the only one here.
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


def _db(fn):
    """Run fn(maker) on a fresh engine, from a sync test."""
    async def _go():
        eng, maker = await h.open_db()
        try:
            return await fn(maker)
        finally:
            await eng.dispose()
    return asyncio.run(_go())


@pytest.fixture(scope="module")
def person(client):
    """Somebody allowed to scrape, with a verified number."""
    from app.auth.jwt import get_password_hash
    from app.models.user import User
    tag = random.randint(100, 999)

    async def _mk(maker):
        async with maker() as s:
            u = User(username=f"single_{tag}", full_name=f"کاربر آزمایشی {tag}", role="admin",
                     permissions=["scraper"], hashed_password=get_password_hash(PASSWORD),
                     is_active=True, phone=f"09120009{tag}", phone_verified=True)
            s.add(u)
            await s.commit()
            return u.id, u.username, u.full_name
    uid, username, name = _db(_mk)
    r = client.post("/api/users/token", data={"username": username, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"id": uid, "name": name, "auth": {"Authorization": f"Bearer {r.json()['access_token']}"}}


@pytest.fixture
def worker(monkeypatch):
    """What the worker process does with a queued job — run_scraping_job
    with the row's own config — over a fake browser. Returns
    run(job_id, pages) → the fake scraper that ran."""
    from app.api.routes import scraper as sr
    from app.services import scrape_queue
    h.quiet(monkeypatch)

    async def _sweep(**_kw):
        return {"alive": 0, "dead": 0, "unknown": 0}
    monkeypatch.setattr("app.services.divar_session.sweep", _sweep)

    def run(job_id, pages):
        made = []

        def build(db_session, proxy_enabled=False, headless=True):
            made.append(h.FakeScraper(db_session, pages=pages))
            return made[-1]
        monkeypatch.setattr(sr, "DivarScraper", build)

        async def _go(maker):
            row = await h.job_row(maker, job_id)
            await sr.run_scraping_job(**scrape_queue.job_kwargs(row))
        _db(_go)
        return made[0]
    return run


def _listed(client, person, job_id):
    items = client.get("/api/scraper/jobs?limit=100", headers=person["auth"]).json()["items"]
    return next(i for i in items if i["job_id"] == job_id)


class TestSingleScrape:
    def test_a_new_listing_is_a_job_of_one_that_saves_it_with_its_number(self, client, person, worker):
        tok = h.token()
        r = client.post("/api/scraper/scrape-single", json={"url": h.url_of(tok)}, headers=person["auth"])
        assert r.status_code == 200, r.text
        job_id = r.json()["job_id"]
        assert r.json()["status"] == "pending"

        s = worker(job_id, {tok: h.page(tok, phone=h.PHONE.format(601))})
        assert s.opened == [tok] and s.revealed == [tok]
        prop = _db(lambda m: h.property_row(m, tok))
        assert prop.phone_number == h.PHONE.format(601)

        row = _listed(client, person, job_id)
        assert row["status"] == "completed" and row["category_name"] == "اسکرپ تکی"
        assert (row["new_items"], row["updated_items"], row["failed_items"]) == (1, 0, 0)
        assert (row["scraped_items"], row["total_items"], row["progress"]) == (1, 1, 100.0)
        assert row["divar_count"] is None, "an explicit list has no Divar count"
        assert row["reason_line"] is None, "nothing to explain: it got what it was asked for"
        assert row["owner_name"] == person["name"] and row["owner_deleted"] is False

    def test_a_stored_listing_without_a_number_gets_it_and_leaves_the_list(self, client, person, worker):
        tok = h.token()
        _db(lambda m: h.stored(m, tok))
        from app.services import skipped_listings

        async def _owed(maker):
            await skipped_listings.record(await h.new_job(maker), divar_id=tok, url=h.url_of(tok),
                                          reason="no_phone", detail="ذخیره شد ولی شماره گرفته نشد")
        _db(_owed)
        job_id = client.post("/api/scraper/scrape-single", json={"url": h.url_of(tok)},
                             headers=person["auth"]).json()["job_id"]
        worker(job_id, {tok: h.page(tok, phone=h.PHONE.format(602))})

        assert _db(lambda m: h.property_row(m, tok)).phone_number == h.PHONE.format(602)
        assert _db(lambda m: h.skipped_rows(m, divar_id=tok)) == [], "still offered for a retry"
        row = _listed(client, person, job_id)
        assert (row["new_items"], row["updated_items"]) == (0, 1), "an update is «بروز», not «تازه»"
        assert row["reason_line"] == "1 بروز"

    def test_no_number_is_a_failure_that_says_what_to_do(self, client, person, worker):
        tok = h.token()
        job_id = client.post("/api/scraper/scrape-single", json={"url": h.url_of(tok)},
                             headers=person["auth"]).json()["job_id"]
        worker(job_id, {tok: h.page(tok)})

        row = _listed(client, person, job_id)
        assert (row["new_items"], row["failed_items"]) == (0, 1)
        assert row["reason_line"] == "1 بدون شماره"
        (skip,) = _db(lambda m: h.skipped_rows(m, job_id=job_id))
        assert skip.reason == "no_phone"
        # nothing retries an explicit list by itself, so it must not promise that
        # «تلاش دوباره» in the run's own list tries it again in the same run (#58)
        assert "تلاش دوباره" in skip.detail and "اجرای بعدی" not in skip.detail

    def test_not_a_listing_link_is_refused(self, client, person):
        r = client.post("/api/scraper/scrape-single", json={"url": "https://divar.ir/s/urmia"},
                        headers=person["auth"])
        assert r.status_code == 400


class TestRescrape:
    def test_a_list_is_one_job_and_every_listing_lands_somewhere(self, client, person, worker):
        new, held, gone = h.token(), h.token(), h.token()
        _db(lambda m: h.stored(m, held, phone=h.PHONE.format(603)))
        r = client.post("/api/scraper/rescrape", headers=person["auth"], json={
            "urls": [h.url_of(new), h.url_of(held), h.url_of(gone), h.url_of(new),
                     "https://example.com/not-divar"],
            "label": "بدون شماره"})
        assert r.status_code == 200, r.text
        job_id = r.json()["job_id"]

        s = worker(job_id, {new: h.page(new, phone=h.PHONE.format(604)),
                            held: h.page(held, phone=h.PHONE.format(605)),
                            gone: h.GONE})
        # a listing named by hand is opened whatever the table holds, once
        assert s.opened == [new, held, gone]
        row = _listed(client, person, job_id)
        assert row["category_name"] == "بدون شماره" and row["status"] == "completed"
        assert (row["new_items"], row["updated_items"], row["failed_items"]) == (1, 1, 0)
        assert (row["scraped_items"], row["total_items"]) == (3, 3)
        assert row["reason_line"] == "1 بروز، 1 در دیوار حذف شده"
        assert [(k.divar_id, k.reason) for k in _db(lambda m: h.skipped_rows(m, job_id=job_id))] \
            == [(gone, "deleted")]
        assert _db(lambda m: h.property_row(m, held)).phone_number == h.PHONE.format(605)

    def test_nothing_valid_is_refused(self, client, person):
        r = client.post("/api/scraper/rescrape", headers=person["auth"],
                        json={"urls": ["https://example.com/x", "not a link"]})
        assert r.status_code == 400


class TestTheJobsTableFields:
    """#29, through GET /api/scraper/jobs and /jobs/{id}."""

    def _seed(self, **kw):
        from app.models.scraping_job import ScrapingJob

        async def _go(maker):
            async with maker() as s:
                j = ScrapingJob(**kw)
                s.add(j)
                await s.commit()
                return str(j.job_id)
        return _db(_go)

    def test_a_deleted_owner_is_named_as_one(self, client, person):
        gone = self._seed(status="completed", config={"city": "urmia", "category": "rent-apartment",
                                                      "owner_user_id": 987654321})
        nobody = self._seed(status="completed", config={"city": "urmia", "category": "rent-apartment"})
        mine = self._seed(status="completed", config={"city": "urmia", "category": "rent-apartment",
                                                      "owner_user_id": person["id"]})
        assert _listed(client, person, gone)["owner_deleted"] is True
        assert _listed(client, person, gone)["owner_name"] is None
        assert _listed(client, person, nobody)["owner_deleted"] is False
        assert _listed(client, person, mine)["owner_name"] == person["name"]

    def test_the_finish_reason_reaches_the_table(self, client, person):
        job_id = self._seed(status="completed", finish_reason="آگهی بیشتری پیدا نشد — 2 از 50 درخواستی",
                            config={"city": "urmia", "category": "rent-apartment", "max_items": 50})
        assert _listed(client, person, job_id)["finish_reason"].startswith("آگهی بیشتری پیدا نشد")
        one = client.get(f"/api/scraper/jobs/{job_id}", headers=person["auth"]).json()
        assert one["finish_reason"].startswith("آگهی بیشتری پیدا نشد")

    def test_the_single_view_and_the_list_agree(self, client, person):
        job_id = self._seed(status="completed", new_items=2, updated_items=1, total_items=24,
                            scraped_items=24, divar_count=251,
                            config={"city": "urmia", "category": "rent-apartment", "max_items": 50,
                                    "owner_user_id": person["id"],
                                    "outcome": {"examined": 24, "duplicate": 3,
                                                "skipped": {"posted": 16}, "failed": {"بدون شماره": 2}}})
        listed = _listed(client, person, job_id)
        one = client.get(f"/api/scraper/jobs/{job_id}", headers=person["auth"]).json()
        for k in ("owner_name", "owner_deleted", "reason_line", "finish_reason", "divar_count",
                  "total_items", "scraped_items", "progress", "can_resume"):
            assert listed[k] == one[k], k
        assert listed["reason_line"] == "16 تاریخ انتشار، 3 تکراری، 2 بدون شماره"
        assert (listed["scraped_items"], listed["total_items"], listed["divar_count"]) == (24, 24, 251)

    def test_a_partial_run_can_be_continued_and_a_completed_one_cannot(self, client, person):
        cfg = {"city": "urmia", "category": "rent-apartment", "owner_user_id": person["id"]}
        for status, resumable in (("partial", True), ("failed", True), ("cancelled", True),
                                  ("completed", False), ("running", False)):
            job_id = self._seed(status=status, config=cfg)
            assert _listed(client, person, job_id)["can_resume"] is resumable, status
        assert _listed(client, person, self._seed(status="failed"))["can_resume"] is False, \
            "a run from before the config was kept has nothing to continue with"
