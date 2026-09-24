"""
«هر حساب فقط می‌تواند از شماره‌های دیوار خودش استفاده کند»

A run started by one colleague was found on the root account's number,
spending its reveals and sending its code prompts to root. The pool rotation
drew from was owner-scoped; four other ways onto a number were not:

  * DivarScraper.initialize() fell back to DIVAR_PHONE_NUMBER — one person's
    number — before it ever consulted the owner's pool;
  * when the owner's own session did not restore, it fell back to «the most
    recently updated valid session» in the whole table, which is the most
    used number: root's;
  * counting and resetting a round of reveals spanned everybody's numbers;
  * a Divar login on somebody else's number, answered by their phone's SMS
    forwarder, moved the number to whoever started the login.

These run against the real schema (Postgres), because every one of them was
a query that looked right.

Also here: the owner's on/off switch for a number (a SIM that is out of
reach must not be rotated onto), and switching a running scrape onto
another of the owner's numbers without stopping it.
"""
import asyncio
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_numbers_personal.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

ROOT_NUM = "09058432452"      # the root account's number, in the incident
JAN_1 = "09146382408"
JAN_OFF = "09146382409"       # switched off by its owner
JAN_ID = "09146382410"        # Divar wants an identity check
JAN_2 = "09146382411"
NOBODYS = "09146382412"       # a session no user has claimed


@pytest.fixture(scope="module")
def client():
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

    # One fakeredis server for the whole module: HTTP calls run inside
    # TestClient's own portal loop, but plenty of tests below also poke
    # otp_store/the login registry directly from a bare asyncio.run() —
    # a different loop every time. _fake_redis.redis_factory hands out a
    # fresh client per get_redis() call bound to whichever loop asked, all
    # sharing this one server, so both styles see the same data.
    from _fake_redis import redis_factory
    get_redis = redis_factory()
    import app.services.verification as v
    from app.scraper import otp_store
    from app.scraper import stealth
    from app.api.routes import auth as auth_routes
    db.get_redis = v.get_redis = otp_store.get_redis = stealth.get_redis = \
        auth_routes.get_redis = get_redis
    from fastapi.testclient import TestClient
    import app.main as m
    with TestClient(m.app) as c:
        yield c
    (cfg.environment, cfg.api_key, cfg.cookies_path, cfg.scrape_scheduler, cfg.match_engine) = saved


def _engine():
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    eng = create_async_engine(os.environ["DATABASE_URL"])
    return eng, async_sessionmaker(eng, expire_on_commit=False)


@pytest.fixture(scope="module")
def people(client):
    """root (سبحان) with one number; a colleague (جان‌نثار) with four —
    one usable, one switched off, one waiting on an identity check, and a
    second usable one to switch to. root's number was touched last, which
    is what made it the old fallback's pick."""
    from app.models.user import User
    from app.models.cookie import Cookie
    from app.auth.jwt import get_password_hash

    async def _go():
        eng, maker = _engine()
        try:
            async with maker() as s:
                root = User(username="np_root", full_name="سبحان", role="root",
                            permissions=[], phone="09120000101", phone_verified=True,
                            hashed_password=get_password_hash("pw123456"), is_active=True)
                jan = User(username="np_jan", full_name="جان‌نثار", role="admin",
                           permissions=["scraper", "divar_auth"], phone="09120000102",
                           phone_verified=True,
                           hashed_password=get_password_hash("pw123456"), is_active=True)
                third = User(username="np_third", full_name="سوم", role="admin",
                             permissions=["scraper", "divar_auth"], phone="09120000103",
                             phone_verified=True,
                             hashed_password=get_password_hash("pw123456"), is_active=True)
                s.add_all([root, jan, third])
                await s.flush()
                jar = [{"name": "sAccessToken", "value": "x.y.z", "domain": ".divar.ir"}]
                now = datetime.now(timezone.utc)
                s.add_all([
                    Cookie(phone_number=JAN_1, owner_user_id=jan.id, cookies=jar, reveals=5),
                    Cookie(phone_number=JAN_OFF, owner_user_id=jan.id, cookies=jar, is_enabled=False),
                    Cookie(phone_number=JAN_ID, owner_user_id=jan.id, cookies=jar,
                           identity_required_at=now),
                    Cookie(phone_number=JAN_2, owner_user_id=jan.id, cookies=jar, reveals=9),
                    Cookie(phone_number=ROOT_NUM, owner_user_id=root.id, cookies=jar, reveals=417),
                ])
                await s.commit()
                return {"root": root.id, "jan": jan.id, "third": third.id}
        finally:
            await eng.dispose()
    return asyncio.run(_go())


def _tok(client, username):
    r = client.post("/api/users/token", data={"username": username, "password": "pw123456"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _with_scraper(owner, fn, **attrs):
    """Run `fn(scraper)` against a DivarScraper on a real DB session."""
    from app.scraper.divar_scraper import DivarScraper

    async def _go():
        eng, maker = _engine()
        try:
            async with maker() as s:
                sc = DivarScraper(db_session=s, proxy_enabled=False, headless=True)
                sc.owner_user_id = owner
                for k, v in attrs.items():
                    setattr(sc, k, v)
                return await fn(sc)
        finally:
            await eng.dispose()
    return asyncio.run(_go())


# ── which numbers a run may use ─────────────────────────────────────────────

class TestTheRunsPool:

    def test_only_the_owners_usable_numbers(self, people):
        async def fn(sc):
            return await sc._load_rotation_pool()
        pool = _with_scraper(people["jan"], fn)
        assert ROOT_NUM not in pool, "a colleague's run can reach the root account's number"
        assert JAN_OFF not in pool, "a number its owner switched off is still rotated onto"
        assert JAN_ID not in pool
        assert pool == [JAN_1, JAN_2], "least spent first"

    def test_a_run_nobody_owns_gets_only_the_numbers_nobody_owns(self, people):
        """It used to get the whole table, so every internally started run
        was a way onto everybody's numbers — root's first, as the most used."""
        from app.models.cookie import Cookie

        async def fn(sc):
            row = Cookie(phone_number=NOBODYS, cookies=[{"name": "x", "value": "y"}])
            sc.db_session.add(row)
            await sc.db_session.commit()
            try:
                return (await sc._load_rotation_pool(),
                        await sc._account_usable(ROOT_NUM),
                        await sc._account_usable(NOBODYS))
            finally:
                await sc.db_session.delete(row)
                await sc.db_session.commit()
        pool, root_usable, nobodys_usable = _with_scraper(None, fn)
        assert pool == [NOBODYS]
        assert not root_usable and nobodys_usable

    def test_a_named_number_must_be_the_owners_own_and_on(self, people):
        async def fn(sc):
            return {p: await sc._account_usable(p) for p in (ROOT_NUM, JAN_OFF, JAN_ID, JAN_1)}
        got = _with_scraper(people["jan"], fn)
        assert got == {ROOT_NUM: False, JAN_OFF: False, JAN_ID: False, JAN_1: True}

    def test_the_round_counts_only_the_runs_own_numbers(self, people):
        async def fn(sc):
            return await sc._unspent_account_count(10)
        # JAN_1 (5) and JAN_2 (9) have budget left; root's number is not his
        assert _with_scraper(people["jan"], fn) == 2

    def test_a_new_round_resets_only_the_runs_own_numbers(self, people):
        from app.models.cookie import Cookie
        from sqlalchemy import select

        async def fn(sc):
            await sc._rest_all_accounts()
            rows = (await sc.db_session.execute(select(Cookie))).scalars().all()
            got = {r.phone_number: r.reveals for r in rows}
            # put the counts back for the other tests
            for r in rows:
                r.reveals = {JAN_1: 5, JAN_2: 9, ROOT_NUM: 417}.get(r.phone_number, 0)
            await sc.db_session.commit()
            return got
        got = _with_scraper(people["jan"], fn)
        assert got[ROOT_NUM] == 417, "one person's new round wiped the root account's counter"
        assert got[JAN_1] == 0 and got[JAN_2] == 0


# ── initialize(): the incident itself ───────────────────────────────────────

class _FakePlaywright:
    async def start(self):
        return self


def _initialize(owner, monkeypatch, *, named=None, restorable=()):
    """initialize() with the browser stubbed out. Returns (ok, the numbers it
    tried to restore, the number it settled on)."""
    import app.scraper.divar_scraper as ds
    monkeypatch.setattr(ds, "async_playwright", lambda: _FakePlaywright())
    # The configured default names the root account's number, as .env.example did.
    monkeypatch.setattr(ds.settings, "divar_phone_number", ROOT_NUM)
    tried = []

    async def fn(sc):
        async def _open(account, proxy=None):
            sc.context = object()
        sc._open_browser_for = _open

        async def _restore(phone):
            tried.append(phone)
            return phone in restorable
        sc.auth.restore_session = _restore
        ok = await sc.initialize(phone_number=named)
        return ok, sc.active_phone
    ok, active = _with_scraper(owner, fn)
    return ok, tried, active


class TestInitialize:

    def test_the_configured_default_is_not_the_owners_number(self, people, monkeypatch):
        ok, tried, active = _initialize(people["jan"], monkeypatch, restorable={JAN_1})
        assert ROOT_NUM not in tried
        assert ok and active == JAN_1

    def test_a_failed_restore_falls_back_to_the_owners_other_number(self, people, monkeypatch):
        ok, tried, active = _initialize(people["jan"], monkeypatch, restorable={JAN_2})
        assert tried == [JAN_1, JAN_2]
        assert ok and active == JAN_2

    def test_and_never_to_somebody_elses(self, people, monkeypatch):
        """The incident: the colleague's own number would not restore, and the
        run carried on — silently — on the most recently used session in the
        table, which was the root account's."""
        ok, tried, active = _initialize(people["jan"], monkeypatch, restorable={ROOT_NUM})
        assert ROOT_NUM not in tried
        assert not ok and active is None

    def test_when_none_of_the_owners_numbers_opens_the_run_log_says_so(self, people, monkeypatch):
        """It said nothing there, and the panel read «مرورگر اسکرپر بالا نیامد: نامشخص»."""
        import app.scraper.divar_scraper as ds
        lines = []

        async def _log(self, message, *, level="info", **extra):
            lines.append((level, message))
        monkeypatch.setattr(ds.DivarScraper, "_log_run", _log)
        ok, tried, active = _initialize(people["jan"], monkeypatch, restorable=set())
        assert not ok and tried == [JAN_1, JAN_2]
        assert any(lvl == "error" and JAN_1 in msg for lvl, msg in lines), lines

    def test_a_named_number_that_is_not_the_owners_is_ignored(self, people, monkeypatch):
        ok, tried, active = _initialize(people["jan"], monkeypatch, named=ROOT_NUM,
                                        restorable={ROOT_NUM, JAN_1})
        assert ROOT_NUM not in tried and active == JAN_1

    def test_a_named_number_that_is_switched_off_is_ignored(self, people, monkeypatch):
        ok, tried, active = _initialize(people["jan"], monkeypatch, named=JAN_OFF,
                                        restorable={JAN_OFF, JAN_1})
        assert JAN_OFF not in tried and active == JAN_1

    def test_an_ownerless_internal_run_keeps_the_configured_default(self, people, monkeypatch):
        ok, tried, active = _initialize(None, monkeypatch, restorable={ROOT_NUM})
        assert tried[0] == ROOT_NUM and active == ROOT_NUM


# ── switching a running scrape onto another number ──────────────────────────

class _Auth:
    def __init__(self, ok=True):
        self.ok, self.restored = ok, []

    def browser_alive(self):
        return True

    async def restore_session(self, phone):
        self.restored.append(phone)
        return self.ok

    async def get_current_cookies(self):
        return []


def _switch(owner, request, *, every=None, active=JAN_1, ok=True):
    from app.scraper import otp_store
    job = f"switch-test-{request}-{every}-{ok}"

    async def _setup():
        await otp_store.cancel_all(job)          # the old number's prompts were dismissed
        await otp_store.request_switch(job, request)
    asyncio.run(_setup())

    async def fn(sc):
        sc.auth = _Auth(ok)

        async def _noop(*a, **k):
            return None
        sc._persist_active_session = _noop
        sc._human_like_delay = _noop
        changed = await sc.maybe_rotate_account()
        return changed, sc.active_phone, sc.auth.restored
    changed, now, restored = _with_scraper(
        owner, fn, active_phone=active, _job_id_str=job, _rotate_every_override=every)

    async def _read():
        return await otp_store.is_cancelled(job), await otp_store.has_switch(job)
    suppressed, pending = asyncio.run(_read())
    return changed, now, restored, suppressed, pending


class TestSwitchingMidRun:

    def test_to_a_named_number_of_ones_own(self, people):
        changed, now, restored, suppressed, pending = _switch(people["jan"], JAN_2)
        assert changed and now == JAN_2 and restored == [JAN_2]
        assert not suppressed, "the new number's codes stay suppressed by the old one's dismissal"
        assert not pending, "the request is consumed once"

    def test_to_the_next_one_even_when_rotation_is_pinned(self, people):
        """rotate_every = 0 pins against ROTATION; a person saying «this phone
        is not in my hand» is not rotation."""
        changed, now, _r, _s, _p = _switch(people["jan"], None, every=0)
        assert changed and now == JAN_2

    def test_never_onto_somebody_elses(self, people):
        changed, now, restored, _s, _p = _switch(people["jan"], ROOT_NUM)
        assert not changed and now == JAN_1 and restored == []

    def test_never_onto_a_switched_off_number(self, people):
        changed, now, restored, _s, _p = _switch(people["jan"], JAN_OFF)
        assert not changed and now == JAN_1 and restored == []

    def test_a_session_that_will_not_restore_leaves_the_run_where_it_was(self, people):
        changed, now, restored, _s, _p = _switch(people["jan"], JAN_2, ok=False)
        assert not changed and now == JAN_1 and restored == [JAN_2]


# ── through the app ─────────────────────────────────────────────────────────

def _job(owner, status="running", phone=JAN_1):
    from app.models.scraping_job import ScrapingJob

    async def _go():
        eng, maker = _engine()
        try:
            async with maker() as s:
                j = ScrapingJob(status=status, divar_phone=phone,
                                config={"city": "urmia", "category": "rent-apartment",
                                        "owner_user_id": owner})
                s.add(j)
                await s.commit()
                return str(j.job_id)
        finally:
            await eng.dispose()
    return asyncio.run(_go())


def _finish_all_runs():
    """Earlier tests leave runs «running»; the launcher caps those at three
    and the switch refuses a number a live run is on."""
    from app.models.scraping_job import ScrapingJob
    from sqlalchemy import update

    async def _go():
        eng, maker = _engine()
        try:
            async with maker() as s:
                await s.execute(update(ScrapingJob).where(
                    ScrapingJob.status.in_(("running", "paused", "pending"))).values(status="completed"))
                await s.commit()
        finally:
            await eng.dispose()
    asyncio.run(_go())


def _cookie_id(phone):
    from app.models.cookie import Cookie
    from sqlalchemy import select

    async def _go():
        eng, maker = _engine()
        try:
            async with maker() as s:
                return (await s.execute(select(Cookie.id).where(
                    Cookie.phone_number == phone))).scalar_one()
        finally:
            await eng.dispose()
    return asyncio.run(_go())


class TestTheDivarLoginIsNotAWayIn:

    def test_nobody_starts_a_login_on_somebody_elses_number(self, client, people):
        r = client.post("/api/auth/login", json={"phone_number": ROOT_NUM},
                        headers=_tok(client, "np_jan"))
        assert r.status_code == 403, r.text

    def test_nobody_finishes_somebody_elses_login(self, client, people):
        from app.api.routes import auth as auth_routes
        phone = "09129990001"
        auth_routes.auth_instances[phone] = object()
        asyncio.run(auth_routes._note_login_started(phone, people["root"]))
        try:
            r = client.post(f"/api/auth/verify?phone_number={phone}", json={"code": "123456"},
                            headers=_tok(client, "np_jan"))
            assert r.status_code == 403, r.text
        finally:
            auth_routes.auth_instances.pop(phone, None)
            asyncio.run(auth_routes._clear_login_started(phone))

    def test_a_forwarded_login_code_is_only_the_owners(self, client, people):
        from app.scraper import otp_store
        asyncio.run(otp_store.put_login_code(ROOT_NUM, "654321"))
        r = client.get(f"/api/scraper/login-code/{ROOT_NUM}", headers=_tok(client, "np_jan"))
        assert r.status_code == 200 and r.json()["code"] is None
        r = client.get(f"/api/scraper/login-code/{ROOT_NUM}", headers=_tok(client, "np_root"))
        assert r.json()["code"] == "654321"

    def test_nobody_names_somebody_elses_number_as_their_own(self, client, people):
        r = client.patch("/api/users/me/divar-phone", json={"divar_phone": ROOT_NUM},
                         headers=_tok(client, "np_jan"))
        assert r.status_code == 403, r.text

    def test_a_run_cannot_start_on_somebody_elses_number(self, client, people):
        r = client.post("/api/scraper/start", headers=_tok(client, "np_jan"),
                        json={"city": "urmia", "category": "rent-apartment", "divar_phone": ROOT_NUM})
        assert r.status_code == 403, r.text

    def test_or_on_a_number_its_owner_switched_off(self, client, people):
        r = client.post("/api/scraper/start", headers=_tok(client, "np_jan"),
                        json={"city": "urmia", "category": "rent-apartment", "divar_phone": JAN_OFF})
        assert r.status_code == 409, r.text


class TestTheOnOffSwitch:

    def test_the_list_says_whether_each_number_is_on(self, client, people):
        rows = client.get("/api/auth/cookies?mine=1", headers=_tok(client, "np_jan")).json()["cookies"]
        by = {c["phone_number"]: c["is_enabled"] for c in rows}
        assert by[JAN_1] is True and by[JAN_OFF] is False
        assert ROOT_NUM not in by

    def test_the_owner_switches_it_and_a_run_on_it_is_moved(self, client, people):
        from app.scraper import otp_store
        job = _job(people["jan"], phone=JAN_2)
        cid = _cookie_id(JAN_2)
        r = client.patch(f"/api/auth/cookies/{cid}", json={"enabled": False},
                         headers=_tok(client, "np_jan"))
        assert r.status_code == 200 and r.json()["is_enabled"] is False
        assert job in r.json()["moved_jobs"]
        req = asyncio.run(otp_store.take_switch(job))
        assert req and req["phone"] is None and req["reason"] == "disabled"
        r = client.patch(f"/api/auth/cookies/{cid}", json={"enabled": True},
                         headers=_tok(client, "np_jan"))
        assert r.json()["is_enabled"] is True
        _finish_all_runs()

    def test_a_colleague_cannot_switch_it(self, client, people):
        cid = _cookie_id(JAN_1)
        r = client.patch(f"/api/auth/cookies/{cid}", json={"enabled": False},
                         headers=_tok(client, "np_third"))
        assert r.status_code == 403


class TestTheSwitchEndpoint:

    def test_the_owner_asks_for_another_of_their_numbers(self, client, people):
        from app.scraper import otp_store
        _finish_all_runs()
        job = _job(people["jan"])
        r = client.post(f"/api/scraper/jobs/{job}/switch-account", json={"phone": JAN_2},
                        headers=_tok(client, "np_jan"))
        assert r.status_code == 200, r.text
        assert asyncio.run(otp_store.take_switch(job))["phone"] == JAN_2

    def test_or_simply_the_next_one(self, client, people):
        from app.scraper import otp_store
        job = _job(people["jan"])
        r = client.post(f"/api/scraper/jobs/{job}/switch-account", json={},
                        headers=_tok(client, "np_jan"))
        assert r.status_code == 200, r.text
        assert asyncio.run(otp_store.take_switch(job))["phone"] is None

    def test_not_onto_somebody_elses_number(self, client, people):
        job = _job(people["jan"])
        r = client.post(f"/api/scraper/jobs/{job}/switch-account", json={"phone": ROOT_NUM},
                        headers=_tok(client, "np_jan"))
        assert r.status_code == 403

    def test_not_onto_one_that_is_off(self, client, people):
        job = _job(people["jan"])
        r = client.post(f"/api/scraper/jobs/{job}/switch-account", json={"phone": JAN_OFF},
                        headers=_tok(client, "np_jan"))
        assert r.status_code == 403

    def test_not_somebody_elses_run(self, client, people):
        job = _job(people["jan"])
        for who in ("np_third", "np_root"):
            r = client.post(f"/api/scraper/jobs/{job}/switch-account", json={},
                            headers=_tok(client, who))
            assert r.status_code == 403, who

    def test_not_a_finished_run(self, client, people):
        job = _job(people["jan"], status="completed")
        r = client.post(f"/api/scraper/jobs/{job}/switch-account", json={},
                        headers=_tok(client, "np_jan"))
        assert r.status_code == 409


class TestSomebodyElsesRun:

    def test_a_colleague_cannot_cancel_it(self, client, people):
        job = _job(people["jan"])
        r = client.post(f"/api/scraper/jobs/{job}/cancel", headers=_tok(client, "np_third"))
        assert r.status_code == 403
        r = client.post(f"/api/scraper/jobs/{job}/cancel", headers=_tok(client, "np_jan"))
        assert r.status_code == 200


# ── the pieces that could not be exercised end to end ───────────────────────

class TestTheShape:

    def test_an_otp_wait_stops_when_a_switch_is_asked_for(self):
        import inspect
        from app.scraper.contact_extractor import ContactExtractor
        src = inspect.getsource(ContactExtractor._handle_sms_otp_if_present)
        loop = src[src.index("while waited < timeout"):]
        assert "otp_store.has_switch(" in loop
        # …and leaving for a switch is not an unanswered prompt
        assert "not got_code and not switched" in src

    def test_a_failed_rotation_puts_the_browser_back(self):
        import inspect
        from app.scraper.divar_scraper import DivarScraper
        src = inspect.getsource(DivarScraper.maybe_rotate_account)
        tail = src[src.index("Every candidate failed"):]
        assert "await self._return_to_active_browser()" in tail

    def test_the_login_starts_in_an_empty_profile_of_its_own(self):
        import inspect
        from app.scraper.auth import DivarAuth
        src = inspect.getsource(DivarAuth.login_with_phone)
        assert 'account=f"login{digits}"' in src and "clear_cookies()" in src

    def test_a_run_with_no_number_does_not_inherit_a_session(self):
        import inspect
        from app.scraper.divar_scraper import DivarScraper
        src = inspect.getsource(DivarScraper._open_browser_for)
        assert "if not account" in src and "clear_cookies()" in src

    def test_the_pre_run_sweep_is_the_owners(self):
        import inspect
        from app.api.routes import scraper as sr
        assert "owner_user_id=owner_user_id" in inspect.getsource(sr.run_scraping_job)

    def test_the_column_is_a_guarded_revision(self):
        from pathlib import Path
        rev = (Path(__file__).resolve().parent.parent
               / "migrations/versions/0009_cookie_enabled.py").read_text(encoding="utf-8")
        assert 'revision = "0009"' in rev and 'down_revision = "0008"' in rev
        assert '"is_enabled" not in' in rev and "server_default=sa.true()" in rev


class TestThePanel:
    """The scrape form, the code prompt and the jobs table."""

    @staticmethod
    def _js():
        from pathlib import Path
        return (Path(__file__).resolve().parent.parent / "frontend/js/app.js").read_text(encoding="utf-8")

    @staticmethod
    def _html():
        from pathlib import Path
        return (Path(__file__).resolve().parent.parent / "frontend/index.html").read_text(encoding="utf-8")

    def test_each_number_has_its_own_switch_in_the_form(self):
        js, html = self._js(), self._html()
        assert 'id="scraper-account-list"' in html
        fn = js[js.index("function _renderScraperAccountList("):js.index("async function toggleDivarNumber(")]
        assert 'role="switch"' in fn and "toggleDivarNumber(" in fn
        assert "esc(c.phone_number)" in fn
        tog = js[js.index("async function toggleDivarNumber("):js.index("async function switchJobAccount(")]
        assert "`/auth/cookies/${id}`" in tog and "method: 'PATCH'" in tog

    def test_a_switched_off_number_cannot_be_picked(self):
        js = self._js()
        assert "c.is_enabled !== false" in js[js.index("function _divarUsable("):][:200]
        fn = js[js.index("async function loadScraperAccounts("):js.index("let _myDivarAccounts")]
        assert "_divarUsable(c) ? '' : ' disabled'" in fn

    def test_automatic_means_the_servers_least_spent_pick(self):
        """«خودکار — کم‌مصرف‌ترین» used to send the primary/newest session."""
        js = self._js()
        fn = js[js.index("async function executeBulkScraping("):][:1200]
        assert "picked ? { phone_number: picked } : null" in fn
        assert "_getActiveSession()" not in fn

    def test_the_code_prompt_offers_another_number(self):
        html = self._html()
        modal = html[html.index('id="divarOtpModal"'):html.index('id="twoFAModal"')]
        assert 'onclick="switchFromOtp()"' in modal
        js = self._js()
        fn = js[js.index("async function switchFromOtp("):][:900]
        assert "key.split(':')[0]" in fn and "switchJobAccount(jobId" in fn

    def test_a_running_job_of_ones_own_can_switch(self):
        js = self._js()
        fn = js[js.index("function _renderJobsTable("):js.index("const _dismissedOtpKeys")]
        assert "job.owner_user_id === _currentUser?.id" in fn
        assert "switchJobAccount('${job.job_id}'" in fn
        sw = js[js.index("async function switchJobAccount("):js.index("async function switchFromOtp(")]
        assert "/switch-account`" in sw and "_divarUsable(c)" in sw

    def test_the_sessions_list_escapes_the_number(self):
        js = self._js()
        fn = js[js.index("async function loadCookies("):js.index("async function deleteCookie(")]
        assert "${esc(cookie.phone_number)}" in fn
        assert "<strong>${cookie.phone_number}</strong>" not in fn


# ── what the review of this change found ────────────────────────────────────

class TestAFailedSwitchLeavesTheRunABrowser:
    """_open_browser_for closes the old profile before opening the next. When
    the next one would not open («already open in this process»), the run was
    left with no browser at all, and every later listing failed on it."""

    def test_the_runs_own_profile_is_opened_again(self):
        from app.scraper.divar_scraper import DivarScraper
        import app.scraper.divar_scraper as ds

        s = DivarScraper.__new__(DivarScraper)
        s.playwright = object()
        s.proxy_enabled = False
        s.active_phone = "A"
        s.context = object()
        s._browser_account = "A"
        s._rotation_pool = ["A", "B"]
        s._reveals_since_rotation = 0
        s._force_rotate = True
        s._rotate_every_override = 5
        opened = []

        async def _open(account, proxy=None):
            s.context = None
            s._browser_account = ds._NO_BROWSER
            opened.append(account)
            if account == "B":
                raise RuntimeError("profile … is already open in this process")
            s.context, s._browser_account = object(), account
        s._open_browser_for = _open

        class _A:
            def browser_alive(self):
                return True

            async def restore_session(self, phone):
                return True
        s.auth = _A()

        async def _noop(*a, **k):
            return None

        async def _pool():
            return ["A", "B"]

        async def _zero(*a, **k):
            return 0
        s._load_rotation_pool = _pool
        s._persist_active_session = _noop
        s._mark_account_spent = _noop
        s._account_reveals = _zero
        s._human_like_delay = _noop

        changed = asyncio.run(s.maybe_rotate_account())
        assert not changed and s.active_phone == "A"
        assert opened == ["B", "A"], "the run's own profile was never reopened"
        assert s.context is not None

    def test_the_real_opener_forgets_the_account_before_it_opens(self):
        """The stand-in above mirrors this: the moment the old profile closes,
        the browser is nobody's — so a failed open cannot read as «still on A»."""
        import inspect
        from app.scraper.divar_scraper import DivarScraper
        src = inspect.getsource(DivarScraper._open_browser_for)
        assert src.index("self._browser_account = _NO_BROWSER") < src.index("await open_browser(")


class TestStaleRequestsAndPools:

    def test_an_empty_pool_is_not_replaced_by_the_cached_one(self):
        from app.scraper.divar_scraper import DivarScraper
        s = DivarScraper.__new__(DivarScraper)
        s._rotation_pool = ["A", "B"]
        s._reveals_since_rotation = 0
        s._force_rotate = True
        s._rotate_every_override = 5
        s.active_phone = "A"
        tried = []

        class _A:
            def browser_alive(self):
                return True

            async def restore_session(self, phone):
                tried.append(phone)
                return True
        s.auth = _A()

        async def _empty():
            return []

        async def _zero(*a, **k):
            return 0
        s._load_rotation_pool = _empty
        s._account_reveals = _zero
        assert asyncio.run(s.maybe_rotate_account()) is False
        assert tried == [], "rotated onto a number the database no longer offers"
        assert s._rotation_pool == []

    def test_move_off_a_number_the_run_already_left_is_dropped(self, monkeypatch):
        from app.scraper import otp_store
        from app.scraper.divar_scraper import DivarScraper
        from _fake_redis import patch_redis
        # This test needs no Postgres and takes no `client`/`people` fixture,
        # so it must not depend on the module's `client` fixture having run
        # first to patch otp_store's Redis — under sqlite `client` skips
        # itself before it gets there, leaving the real (unreachable here)
        # get_redis singleton bound to whatever event loop last touched it.
        patch_redis(monkeypatch, otp_store)
        s = DivarScraper.__new__(DivarScraper)
        s.active_phone = "09121110002"
        s._job_id_str = "stale-switch"
        tried = []

        class _A:
            def browser_alive(self):
                return True

            async def restore_session(self, phone):
                tried.append(phone)
                return True
        s.auth = _A()

        async def _pool():
            return ["09121110002", "09121110003"]
        s._load_rotation_pool = _pool
        asyncio.run(otp_store.request_switch("stale-switch", None, reason="disabled",
                                             from_phone="09121110001"))
        assert asyncio.run(s.maybe_rotate_account()) is False
        assert tried == [] and s.active_phone == "09121110002"


class TestTheReviewThroughTheApp:

    def test_a_forwarder_sim_is_a_claim_even_before_a_session(self, client, people):
        """A fresh SIM in the colleague's phone forwards its own login code;
        nobody else may start that login or read the code."""
        from app.models.forwarder import ForwarderDevice
        from app.scraper import otp_store
        fresh = "09146382499"

        async def _go():
            eng, maker = _engine()
            try:
                async with maker() as s:
                    s.add(ForwarderDevice(device_id="np-fw-1", user_id=people["jan"],
                                          secret="x" * 40, sim_phone=fresh, is_active=True))
                    await s.commit()
            finally:
                await eng.dispose()
        asyncio.run(_go())
        r = client.post("/api/auth/login", json={"phone_number": fresh},
                        headers=_tok(client, "np_third"))
        assert r.status_code == 403, r.text
        asyncio.run(otp_store.put_login_code(fresh, "112233"))
        r = client.get(f"/api/scraper/login-code/{fresh}", headers=_tok(client, "np_third"))
        assert r.json()["code"] is None
        r = client.get(f"/api/scraper/login-code/{fresh}", headers=_tok(client, "np_jan"))
        assert r.json()["code"] == "112233"

    def test_a_saved_config_on_a_switched_off_number_runs_on_automatic(self, client, people):
        """A resume or a schedule replays what was saved; a number switched off
        since must not stop it at the door with a choice nobody can make."""
        from fastapi import HTTPException
        from app.api.routes.scraper import _launch_job
        from app.schemas import ScrapingJobCreate
        from app.models.user import User
        from sqlalchemy import select

        class _BG:
            def __init__(self):
                self.calls = []

            def add_task(self, fn, *a):
                self.calls.append(a)

        async def _go(interactive):
            eng, maker = _engine()
            try:
                async with maker() as s:
                    jan = (await s.execute(select(User).where(User.id == people["jan"]))).scalar_one()
                    bg = _BG()
                    cfg = ScrapingJobCreate(city="urmia", category="rent-apartment", divar_phone=JAN_OFF)
                    try:
                        resp = await _launch_job(cfg, bg, s, jan, interactive=interactive)
                    except HTTPException as e:
                        return e.status_code, None, None
                    return 200, resp.divar_phone, bg.calls[0][6]
            finally:
                await eng.dispose()
        _finish_all_runs()
        assert asyncio.run(_go(True))[0] == 409
        status, row_phone, run_phone = asyncio.run(_go(False))
        assert status == 200 and row_phone is None and run_phone is None

    def test_resume_and_schedules_replay_rather_than_start(self):
        import inspect
        from app.api.routes import scraper as sr
        from app.services import scrape_scheduler
        assert "interactive=False" in inspect.getsource(sr.resume_scraping_job)
        assert "interactive=False" in inspect.getsource(scrape_scheduler.fire)

    def test_refresh_leaves_a_number_a_run_is_on_alone(self, client, people):
        from app.scraper import stealth

        async def _hold():
            return await stealth._acquire_profile_lock(JAN_1)
        rkey, token, fallback = asyncio.run(_hold())
        try:
            r = client.post(f"/api/auth/refresh?phone_number={JAN_1}", headers=_tok(client, "np_jan"))
            assert r.status_code == 200 and r.json()["in_use"] is True, r.text
        finally:
            fs_key = str(stealth.profile_dir(JAN_1))
            asyncio.run(stealth._release_profile_lock(fs_key, rkey, token, fallback))

    def test_no_switch_onto_a_number_another_run_is_on(self, client, people):
        _finish_all_runs()
        _job(people["jan"], phone=JAN_2)
        job = _job(people["jan"], phone=JAN_1)
        r = client.post(f"/api/scraper/jobs/{job}/switch-account", json={"phone": JAN_2},
                        headers=_tok(client, "np_jan"))
        assert r.status_code == 409, r.text
        _finish_all_runs()

    def test_an_admin_cannot_name_a_colleagues_number_for_somebody(self, client, people):
        r = client.patch(f"/api/users/{people['third']}", json={"divar_phone": JAN_1},
                         headers=_tok(client, "np_root"))
        assert r.status_code == 403, r.text


class TestRootsRegistry:
    """«یک بخش فقط برای root که همهٔ شماره‌ها را با صاحبشان نشان دهد و بتوان
    مالکیت را اصلاح کرد.» The boot-time backfill gave every unclaimed session
    to the first super admin; this is where root puts each number back."""

    MISFILED = "09146382477"

    def _misfile(self, people):
        """A colleague's number recorded under root, the colleague having
        said it is theirs — and a run of root's on it right now."""
        from app.models.cookie import Cookie
        from app.models.user import User
        from sqlalchemy import update

        async def _go():
            eng, maker = _engine()
            try:
                async with maker() as s:
                    s.add(Cookie(phone_number=self.MISFILED, owner_user_id=people["root"],
                                 cookies=[{"name": "sAccessToken", "value": "a.b.c"}]))
                    await s.execute(update(User).where(User.id == people["jan"]).values(divar_phone=self.MISFILED))
                    await s.execute(update(User).where(User.id == people["root"]).values(divar_phone=self.MISFILED))
                    await s.commit()
            finally:
                await eng.dispose()
        asyncio.run(_go())

    def test_only_root_sees_it(self, client, people):
        for who in ("np_jan", "np_third"):
            assert client.get("/api/auth/registry", headers=_tok(client, who)).status_code == 403

    def test_root_sees_every_number_with_its_owner_and_a_hint(self, client, people):
        self._misfile(people)
        _finish_all_runs()
        d = client.get("/api/auth/registry", headers=_tok(client, "np_root")).json()
        by = {r["phone_number"]: r for r in d["numbers"]}
        assert by[ROOT_NUM]["owner_user_id"] == people["root"]
        assert by[JAN_1]["owner_user_id"] == people["jan"]
        row = by[self.MISFILED]
        assert row["owner_user_id"] == people["root"]
        assert row["suggested_owner"]["id"] == people["jan"] and row["suggested_owner"]["why"] == "divar_phone"
        assert {u["id"] for u in d["users"]} >= {people["root"], people["jan"], people["third"]}

    def test_root_gives_it_back_and_the_old_owner_lets_go(self, client, people):
        from app.scraper import otp_store
        from app.models.user import User
        from sqlalchemy import select
        job = _job(people["root"], phone=self.MISFILED)
        rows = client.get("/api/auth/registry", headers=_tok(client, "np_root")).json()["numbers"]
        cid = next(r["id"] for r in rows if r["phone_number"] == self.MISFILED)
        # nobody else may
        r = client.patch(f"/api/auth/registry/{cid}/owner", json={"owner_user_id": people["jan"]},
                         headers=_tok(client, "np_jan"))
        assert r.status_code == 403
        r = client.patch(f"/api/auth/registry/{cid}/owner", json={"owner_user_id": people["jan"]},
                         headers=_tok(client, "np_root"))
        assert r.status_code == 200 and r.json()["changed"] is True, r.text
        assert job in r.json()["moved_jobs"]
        req = asyncio.run(otp_store.take_switch(job))
        assert req and req["from_phone"] == self.MISFILED
        mine = client.get("/api/auth/cookies?mine=1", headers=_tok(client, "np_jan")).json()["cookies"]
        assert self.MISFILED in {c["phone_number"] for c in mine}, "the colleague still cannot see their number"

        async def _root_primary():
            eng, maker = _engine()
            try:
                async with maker() as s:
                    return (await s.execute(select(User.divar_phone).where(User.id == people["root"]))).scalar_one()
            finally:
                await eng.dispose()
        assert asyncio.run(_root_primary()) is None, "root's primary still points at a number that is not theirs"
        _finish_all_runs()

    def test_not_to_a_portal_visitor(self, client, people):
        from app.models.user import User
        from app.auth.jwt import get_password_hash

        async def _go():
            eng, maker = _engine()
            try:
                async with maker() as s:
                    v = User(username="np_visitor", role="visitor", hashed_password=get_password_hash("x"),
                             is_active=True)
                    s.add(v)
                    await s.commit()
                    return v.id
            finally:
                await eng.dispose()
        vid = asyncio.run(_go())
        cid = _cookie_id(JAN_1)
        r = client.patch(f"/api/auth/registry/{cid}/owner", json={"owner_user_id": vid},
                         headers=_tok(client, "np_root"))
        assert r.status_code == 400

    def test_the_panel_shows_it_to_root_only(self):
        js, html = TestThePanel._js(), TestThePanel._html()
        assert 'id="numbers-registry-card"' in html
        fn = js[js.index("async function loadNumbersRegistry("):js.index("async function saveNumberOwner(")]
        assert "_currentUser?.role !== 'root'" in fn and "esc(r.phone_number)" in fn
        assert "loadNumbersRegistry();" in js[js.index("case 'auth':"):][:200]
