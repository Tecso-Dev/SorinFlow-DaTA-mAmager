"""
«کاربرها شماره‌های خود را تأیید نمی‌کنند؛ هر جایی که کاربر نیاز به استفاده از
شماره دارد و شماره‌اش را تأیید نکرده، جلوی فعالیتش را بگیر و پاپ‌آپ تأیید شماره
را بیاور و کد تأیید را برایش ارسال کن. فقط اکانت root می‌تواند به صورت دستی و با
تاگل، شماره و ایمیل کاربران را تأیید کند.»

Two halves, tested through the real app on Postgres:

  * the gate — scraping, logging a Divar number in, importing one, and
    registering an SMS forwarder answer 403 with a machine-readable detail
    until the caller's own phone is verified; the panel turns that into the
    popup. root is exempt, and the gate stands aside when SMS cannot be sent
    at all (a gate nobody can pass is an outage);
  * root's switch — PATCH /users/{id}/verification, root only, and never
    for a phone or address that is not there. And an admin edit that changes
    a phone or address takes the tick away, since the new one is unproven.
"""
import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_phone_gate.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

ROOT = Path(__file__).resolve().parent.parent
JS = (ROOT / "frontend/js/app.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def client():
    import fakeredis.aioredis
    import app.database as db
    from app.config import get_settings
    if not str(db.engine.url).startswith("postgresql"):
        pytest.skip("needs Postgres — see test_auth_roles.py", allow_module_level=True)
    cfg = get_settings()
    saved = (cfg.environment, cfg.api_key, cfg.cookies_path, cfg.scrape_scheduler,
             cfg.match_engine, cfg.auth_sms_provider)
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
    (cfg.environment, cfg.api_key, cfg.cookies_path, cfg.scrape_scheduler,
     cfg.match_engine, cfg.auth_sms_provider) = saved


@pytest.fixture
def sms_ready():
    """SMS can be sent (the console provider), so the gate is live."""
    from app.config import get_settings
    cfg = get_settings()
    was = cfg.auth_sms_provider
    cfg.auth_sms_provider = "console"
    yield
    cfg.auth_sms_provider = was


def _engine():
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    eng = create_async_engine(os.environ["DATABASE_URL"])
    return eng, async_sessionmaker(eng, expire_on_commit=False)


@pytest.fixture(scope="module")
def people(client):
    from app.models.user import User
    from app.auth.jwt import get_password_hash

    def mk(username, role, **kw):
        return User(username=username, full_name=username, role=role,
                    hashed_password=get_password_hash("pw123456"), is_active=True,
                    permissions=["scraper", "divar_auth", "forwarder"], **kw)

    async def _go():
        eng, maker = _engine()
        try:
            async with maker() as s:
                rows = {
                    "root": mk("pg_root", "root"),                               # no phone at all
                    "sa": mk("pg_sa", "super_admin", phone="09120000201", phone_verified=False),
                    "ok": mk("pg_ok", "admin", phone="09120000202", phone_verified=True),
                    "unv": mk("pg_unv", "admin", phone="09120000203", phone_verified=False,
                              email="pg_unv@example.com"),
                    "nophone": mk("pg_nophone", "admin"),
                }
                s.add_all(rows.values())
                await s.commit()
                return {k: v.id for k, v in rows.items()}
        finally:
            await eng.dispose()
    return asyncio.run(_go())


def _tok(client, username):
    r = client.post("/api/users/token", data={"username": username, "password": "pw123456"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _flags(uid):
    from app.models.user import User
    from sqlalchemy import select

    async def _go():
        eng, maker = _engine()
        try:
            async with maker() as s:
                u = (await s.execute(select(User).where(User.id == uid))).scalar_one()
                return u.phone, u.phone_verified, u.email, u.email_verified
        finally:
            await eng.dispose()
    return asyncio.run(_go())


def _set(uid, **kw):
    from app.models.user import User
    from sqlalchemy import update

    async def _go():
        eng, maker = _engine()
        try:
            async with maker() as s:
                await s.execute(update(User).where(User.id == uid).values(**kw))
                await s.commit()
        finally:
            await eng.dispose()
    asyncio.run(_go())


# Every route that leans on the caller's own number, with a body that would
# otherwise get past validation to the handler.
GATED = [
    ("post", "/api/scraper/start", {"city": "urmia", "category": "rent-apartment"}),
    ("post", "/api/scraper/scrape-single", {"url": "https://divar.ir/v/x/AbCd1234"}),
    ("post", "/api/scraper/schedules", {"name": "x", "config": {}, "hour": 8, "minute": 0}),
    ("post", "/api/auth/login", {"phone_number": "09120000299"}),
    ("post", "/api/auth/cookies/import", {"phone_number": "09120000299", "cookies": [{"name": "a"}]}),
    ("post", "/api/forwarder/devices", {"sim_phone": "09120000299"}),
]


class TestTheGate:

    @pytest.mark.parametrize("method,path,body", GATED)
    def test_an_unverified_number_is_refused_with_the_popup_code(self, client, people, sms_ready,
                                                                 method, path, body):
        r = getattr(client, method)(path, json=body, headers=_tok(client, "pg_unv"))
        assert r.status_code == 403, (path, r.text)
        d = r.json()["detail"]
        assert d["code"] == "phone_unverified" and d["phone"] == "09120000203"
        assert "تأیید" in d["message"]

    def test_no_number_at_all_is_asked_for_one(self, client, people, sms_ready):
        r = client.post("/api/scraper/start", json=GATED[0][2], headers=_tok(client, "pg_nophone"))
        assert r.status_code == 403
        d = r.json()["detail"]
        assert d["code"] == "phone_unverified" and d["phone"] is None
        assert "ثبت" in d["message"]

    def test_super_admin_is_not_exempt(self, client, people, sms_ready):
        r = client.post("/api/auth/login", json={"phone_number": "09120000299"},
                        headers=_tok(client, "pg_sa"))
        assert r.status_code == 403 and r.json()["detail"]["code"] == "phone_unverified"

    def test_a_verified_number_passes_the_gate(self, client, people, sms_ready):
        """Past the gate the handler's own checks answer — here, that the
        pasted jar has no session cookie in it."""
        r = client.post("/api/auth/cookies/import", headers=_tok(client, "pg_ok"),
                        json={"phone_number": "09120000298", "cookies": [{"name": "a", "value": "b"}]})
        assert r.status_code == 400, r.text
        assert "phone_unverified" not in r.text

    def test_root_is_exempt(self, client, people, sms_ready):
        g = client.get("/api/users/me/phone-gate", headers=_tok(client, "pg_root")).json()
        assert g["required"] is False

    def test_the_panel_can_ask_up_front(self, client, people, sms_ready):
        g = client.get("/api/users/me/phone-gate", headers=_tok(client, "pg_unv")).json()
        assert g["required"] is True and g["phone"] == "09120000203"
        g = client.get("/api/users/me/phone-gate", headers=_tok(client, "pg_ok")).json()
        assert g["required"] is False

    def test_it_stands_aside_when_no_sms_can_be_sent(self, client, people):
        """phone_verified only ever becomes true over SMS; with no provider
        nobody could pass, and the whole team would be locked out."""
        from app.config import get_settings
        cfg = get_settings()
        was = (cfg.auth_sms_provider, cfg.kavenegar_api_key)
        cfg.auth_sms_provider, cfg.kavenegar_api_key = "kavenegar", ""
        try:
            g = client.get("/api/users/me/phone-gate", headers=_tok(client, "pg_unv")).json()
            assert g["required"] is False
        finally:
            cfg.auth_sms_provider, cfg.kavenegar_api_key = was

    def test_the_popups_own_endpoints_are_not_gated(self, client, people, sms_ready):
        """Otherwise the way out would be behind the door."""
        r = client.post("/api/users/me/phone/request", json={}, headers=_tok(client, "pg_unv"))
        assert r.status_code == 200, r.text
        assert r.json()["sent"] is True

    def test_a_scheduled_run_of_an_unverified_owner_is_skipped(self, client, people, sms_ready):
        from app.models.scrape_schedule import ScrapeSchedule
        from app.services import scrape_scheduler

        async def _go():
            eng, maker = _engine()
            try:
                async with maker() as s:
                    sch = ScrapeSchedule(owner_user_id=people["unv"], name="t", hour=8, minute=0,
                                         config={"city": "urmia", "category": "rent-apartment"},
                                         enabled=True)
                    s.add(sch)
                    await s.commit()
                    return await scrape_scheduler.fire(sch, s)
            finally:
                await eng.dispose()
        got = asyncio.run(_go())
        assert got["status"] == "skipped" and "تأیید" in got["detail"]


class TestRootsSwitch:

    def test_root_marks_a_phone_verified_and_back(self, client, people):
        h = _tok(client, "pg_root")
        r = client.patch(f"/api/users/{people['unv']}/verification",
                         json={"phone_verified": True}, headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["phone_verified"] is True
        assert _flags(people["unv"])[1] is True
        r = client.patch(f"/api/users/{people['unv']}/verification",
                         json={"phone_verified": False, "email_verified": True}, headers=h)
        assert r.status_code == 200
        _p, pv, _e, ev = _flags(people["unv"])
        assert pv is False and ev is True
        _set(people["unv"], email_verified=False)

    @pytest.mark.parametrize("who", ["pg_sa", "pg_ok"])
    def test_nobody_else_may(self, client, people, who):
        r = client.patch(f"/api/users/{people['unv']}/verification",
                         json={"phone_verified": True}, headers=_tok(client, who))
        assert r.status_code == 403
        assert _flags(people["unv"])[1] is False

    def test_not_for_a_phone_or_address_that_is_not_there(self, client, people):
        h = _tok(client, "pg_root")
        for body in ({"phone_verified": True}, {"email_verified": True}):
            r = client.patch(f"/api/users/{people['nophone']}/verification", json=body, headers=h)
            assert r.status_code == 400, body

    def test_an_admin_edit_that_changes_the_phone_takes_the_tick_away(self, client, people):
        _set(people["ok"], phone="09120000202", phone_verified=True)
        h = _tok(client, "pg_root")
        r = client.patch(f"/api/users/{people['ok']}", json={"phone": "۰۹۱۲۰۰۰۰۲۱۲"}, headers=h)
        assert r.status_code == 200, r.text
        phone, pv, _e, _ev = _flags(people["ok"])
        assert phone == "09120000212" and pv is False, "a new number kept the old one's tick"
        _set(people["ok"], phone="09120000202", phone_verified=True)

    def test_an_admin_edit_refuses_a_number_somebody_else_has(self, client, people):
        r = client.patch(f"/api/users/{people['ok']}", json={"phone": "09120000203"},
                         headers=_tok(client, "pg_root"))
        assert r.status_code == 409, r.text

    def test_and_a_number_that_is_not_one(self, client, people):
        r = client.patch(f"/api/users/{people['ok']}", json={"phone": "12345"},
                         headers=_tok(client, "pg_root"))
        assert r.status_code == 400


class TestThePanel:

    def test_the_refusal_opens_the_popup_and_the_action_is_retried(self):
        fn = JS[JS.index("async function apiCall("):JS.index("async function _apiCallOnce(")]
        assert "error.code === 'phone_unverified'" in fn
        assert "await requirePhoneVerified(" in fn
        assert "_phoneGateRetried: true" in fn, "a verified number should not have to click twice"

    def test_the_error_keeps_the_structured_detail(self):
        fn = JS[JS.index("async function _apiCallOnce("):JS.index("function requirePhoneVerified(")]
        assert "err.code =" in fn and "err.status = response.status" in fn
        assert "error.detail.message" in fn, "a structured refusal must read as a sentence, not JSON"

    def test_the_popup_says_what_is_wrong_and_sends_on_request(self):
        """«ارور شماره تأیید نشده و باید تأیید شود بده و با زدن تأیید شماره کد
        برایش ارسال شود» — opening the popup texts nobody."""
        fn = JS[JS.index("function _openPhoneGate("):JS.index("function _markPhoneVerified(")]
        assert "'/users/me/phone/request'" in fn and "'/users/me/phone/verify'" in fn
        assert "if (known) showIntro();" in fn
        assert "if (known) { busy = true; send(null)" not in fn, "the code must not go out on open"
        assert "ok.textContent = 'تأیید شماره'" in fn
        assert "if (step === 'intro') {\n                    await send(null);" in fn
        assert "e.status === 429 && !phone" in fn, "a code sent moments ago is still good — let them type it"

    def test_the_refusal_says_the_number_must_be_verified(self, client, people, sms_ready):
        r = client.post("/api/scraper/start", json={"city": "urmia", "category": "rent-apartment"},
                        headers=_tok(client, "pg_unv"))
        assert "تأیید نشده است و برای این کار باید تأیید شود" in r.json()["detail"]["message"]

    def test_the_sections_that_use_a_number_ask_on_arrival(self):
        sw = JS[JS.index("case 'scraper':"):JS.index("case 'forwarder':") + 200]
        assert sw.count("checkPhoneGate()") == 3

    def test_only_root_is_offered_the_switch(self):
        row = JS[JS.index("function _userRow("):JS.index("function _userRow(") + 5000]
        assert "const canVerify = _currentUser?.role === 'root'" in row
        assert "setUserVerified(" in row
        fn = JS[JS.index("async function setUserVerified("):JS.index("async function nudgeVerify(")]
        assert "/verification`" in fn and "method: 'PATCH'" in fn


class TestTheCodeBelongsToItsNumber:
    """Asking for a code on a NEW number inside the resend cooldown used to
    save the new number anyway — and the code still waiting was the one texted
    to the OLD number. Typing it «verified» a number that received nothing."""

    def test_a_refused_send_does_not_change_the_number(self, client, people, sms_ready):
        _set(people["unv"], phone="09120000203", phone_verified=False)
        h = _tok(client, "pg_unv")
        client.post("/api/users/me/phone/request", json={}, headers=h)   # a code to 0203
        r = client.post("/api/users/me/phone/request", json={"phone": "09120000277"}, headers=h)
        assert r.status_code == 429, r.text
        assert _flags(people["unv"])[0] == "09120000203", \
            "the number changed although no code was sent to it"
