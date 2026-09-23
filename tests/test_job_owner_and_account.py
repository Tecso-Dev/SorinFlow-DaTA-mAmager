"""
«in this section show who started the scraper and the user who does the scrape»

The jobs table said what was scraped and how far, never by whom: the run's
owner was on the config since ownership landed, and the Divar account only
when the run was started ON a number. Now the scraper writes the account it
is on (and every one it rotated through) to the row, and the list resolves
the owner's name.
"""
import os
import sys
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_job_owner.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

ROOT = Path(__file__).resolve().parent.parent
JS = (ROOT / "frontend/js/app.js").read_text(encoding="utf-8")
HTML = (ROOT / "frontend/index.html").read_text(encoding="utf-8")


class TestTheNote:

    def _scraper(self, phone):
        from app.scraper.divar_scraper import DivarScraper
        return SimpleNamespace(active_phone=phone, _note_account=DivarScraper._note_account)

    def test_the_first_account_lands_on_an_automatic_run(self):
        job = SimpleNamespace(divar_phone=None, accounts_used=None)
        s = self._scraper("09121110001")
        s._note_account(s, job)
        assert job.divar_phone == "09121110001" and job.accounts_used == ["09121110001"]

    def test_rotation_appends_and_moves_the_current_one(self):
        job = SimpleNamespace(divar_phone="09121110001", accounts_used=["09121110001"])
        s = self._scraper("09121110002")
        s._note_account(s, job)
        assert job.divar_phone == "09121110002" and job.accounts_used == ["09121110001", "09121110002"]
        # back to the first: current moves, the list does not grow
        s = self._scraper("09121110001")
        s._note_account(s, job)
        assert job.divar_phone == "09121110001" and job.accounts_used == ["09121110001", "09121110002"]

    def test_no_account_means_no_change(self):
        job = SimpleNamespace(divar_phone="09121110001", accounts_used=["09121110001"])
        s = self._scraper(None)
        s._note_account(s, job)
        assert job.divar_phone == "09121110001" and job.accounts_used == ["09121110001"]


class TestTheShape:

    def test_the_scraper_notes_at_start_and_after_rotation(self):
        src = (ROOT / "app/scraper/divar_scraper.py").read_text(encoding="utf-8")
        start = src[src.index('job.status = "running"\n            job.started_at'):][:400]
        assert "self._note_account(job)" in start
        # both places a run may rotate: the skipped-listing path and the main loop
        assert src.count("await self.maybe_rotate_account()\n") == 2
        for i in range(2):
            at = src.index("await self.maybe_rotate_account()\n", 0 if i == 0 else src.index("await self.maybe_rotate_account()\n") + 1)
            assert "self._note_account(job)" in src[at:at + 120]

    def test_the_column_is_a_guarded_revision(self):
        model = (ROOT / "app/models/scraping_job.py").read_text(encoding="utf-8")
        assert "accounts_used = Column(JSON)" in model
        rev = (ROOT / "migrations/versions/0003_job_accounts_used.py").read_text(encoding="utf-8")
        assert 'revision = "0003"' in rev and 'down_revision = "0002"' in rev
        assert '"accounts_used" not in' in rev and "op.add_column" in rev

    def test_the_list_resolves_the_owner(self):
        src = (ROOT / "app/api/routes/scraper.py").read_text(encoding="utf-8")
        fn = src[src.index('@router.get("/jobs", response_model=ScrapingJobList)'):src.index("async def _job_uuid_from")]
        assert 'get("owner_user_id")' in fn and "owner_name=owners.get(" in fn and "accounts_used=j.accounts_used or []" in fn

    def test_the_table_has_the_column(self):
        head = HTML[HTML.index("تسک‌های اسکرپینگ"):HTML.index('<tbody id="jobs-table">')]
        assert "<th title=\"چه کسی اسکرپ را شروع کرد، و با کدام حساب دیوار\">کاربر / حساب</th>" in head
        import re
        assert len(re.findall(r"<th[ >]", head)) == 10
        start = JS.index("function _renderJobsTable")
        fn = JS[start:start + 8000]
        assert 'colspan="10"' in fn and "job.owner_name" in fn and "job.divar_phone" in fn
        assert "job.accounts_used.length - 1" in fn, "a rotated run says how many more"


# ── through the real app (Postgres) ──────────────────────────────────────────

@pytest.fixture(scope="module")
def client():
    import fakeredis.aioredis
    import app.database as db
    from app.config import get_settings
    if not str(db.engine.url).startswith("postgresql"):
        pytest.skip("needs Postgres — see test_auth_roles.py", allow_module_level=True)
    cfg = get_settings()
    saved = (cfg.environment, cfg.api_key, cfg.cookies_path, cfg.scrape_scheduler, cfg.match_engine)
    cfg.environment, cfg.api_key = "test", ""
    cfg.cookies_path = "/tmp/sorinflow-test-cookies"
    cfg.scrape_scheduler = False
    cfg.match_engine = False
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
    (cfg.environment, cfg.api_key, cfg.cookies_path, cfg.scrape_scheduler, cfg.match_engine) = saved


def _seed():
    """Two people, and three runs: one each, and one nobody's (an internal
    run) — with a rotated run among them."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.models.user import User
    from app.models.scraping_job import ScrapingJob
    from app.auth.jwt import get_password_hash

    async def _go():
        eng = create_async_engine(os.environ["DATABASE_URL"])
        maker = async_sessionmaker(eng, expire_on_commit=False)
        try:
            async with maker() as s:
                boss = User(username="jo_boss", full_name="سبحان عظیم‌زاده", role="super_admin", permissions=["scraper"],
                            hashed_password=get_password_hash("pw123456"), is_active=True)
                other = User(username="jo_other", full_name="رسا جان‌نثار", role="admin", permissions=["scraper"],
                             hashed_password=get_password_hash("pw123456"), is_active=True)
                s.add_all([boss, other])
                await s.flush()
                s.add(ScrapingJob(status="completed", divar_phone="09058432452", accounts_used=["09058432452"],
                                  config={"category": "اجاره مغازه", "owner_user_id": boss.id}))
                s.add(ScrapingJob(status="completed", divar_phone="09125005495",
                                  accounts_used=["09125005495", "09146382408"],
                                  config={"category": "اجاره آپارتمان", "owner_user_id": other.id}))
                internal = ScrapingJob(status="failed", config={"category": "خرید"})
                s.add(internal)
                await s.commit()
                return {"boss": boss.id, "other": other.id, "internal": internal.id}
        finally:
            await eng.dispose()
    return asyncio.run(_go())


def _tok(client, username):
    r = client.post("/api/users/token", data={"username": username, "password": "pw123456"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


class TestThroughTheApp:

    def test_every_run_says_who_and_on_which_account(self, client):
        ids = _seed()
        items = client.get("/api/scraper/jobs?limit=100", headers=_tok(client, "jo_boss")).json()["items"]
        by_owner = {i["owner_user_id"]: i for i in items if i["owner_user_id"] in ids.values()}
        mine = by_owner[ids["boss"]]
        assert mine["owner_name"] == "سبحان عظیم‌زاده" and mine["divar_phone"] == "09058432452"
        assert mine["accounts_used"] == ["09058432452"]
        theirs = by_owner[ids["other"]]
        assert theirs["owner_name"] == "رسا جان‌نثار" and theirs["divar_phone"] == "09125005495"
        assert theirs["accounts_used"] == ["09125005495", "09146382408"], "a rotated run names every account"
        internal = next(i for i in items if i["id"] == ids["internal"])
        assert internal["owner_name"] is None and internal["divar_phone"] is None and internal["accounts_used"] == []


class TestARunCanBeRemovedFromTheList:
    """Test runs piled up with no way to clear one, so the only tidy-up left
    was a hand-written DELETE against production."""

    def test_the_endpoint_refuses_a_run_that_is_still_going(self):
        src = (ROOT / "app/api/routes/scraper.py").read_text(encoding="utf-8")
        body = src.split('@router.delete("/jobs/{job_id}")')[1].split("@router.")[0]
        assert "_FINISHED_JOB_STATUSES" in body, \
            "a live scraper keeps writing rows against the job it is running"
        assert "اول لغوش کنید" in body

    def test_the_children_go_first_or_the_database_refuses(self):
        src = (ROOT / "app/api/routes/scraper.py").read_text(encoding="utf-8")
        body = src.split('@router.delete("/jobs/{job_id}")')[1].split("@router.")[0]
        assert "ScrapingLog" in body and "SkippedListing" in body, \
            "both point at job_id with no cascade"
        assert "Property" not in body, "a listing is not the run's to delete"

    def test_only_the_owner_or_a_full_access_role_may_delete(self):
        src = (ROOT / "app/api/routes/scraper.py").read_text(encoding="utf-8")
        body = src.split('@router.delete("/jobs/{job_id}")')[1].split("@router.")[0]
        assert "FULL_ACCESS_ROLES" in body and "owner_user_id" in body
        assert "Depends(get_current_user)" in body

    def test_the_panel_offers_it_only_on_a_finished_run(self):
        js = (ROOT / "frontend/js/app.js").read_text(encoding="utf-8")
        assert "deleteJob(" in js and "method: 'DELETE'" in js
        row = js.split('<td class="job-actions">')[1].split("</td>")[0]
        assert "deleteJob" in row and "'completed', 'failed', 'cancelled'" in row
        confirm = js.split("async function deleteJob(")[1].split("}\n")[0]
        assert "askConfirm" in confirm and "آگهی‌ها" in confirm, \
            "no native confirm, and it says what survives the delete"
