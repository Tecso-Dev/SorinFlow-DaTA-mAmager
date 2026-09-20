"""
One phone, two SIMs, both on Divar.

Sobhan's phone holds two SIM cards and he wants codes for both numbers to
reach the scraper. The app has bound one rule pair to each slot since 3.1.0
and reads `account2` off the setup QR; the server knew one SIM per device,
built the QR with one account, and refused the second number's very first
login code because no Divar session existed for it yet.
"""
import os
import sys
import asyncio
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_dualsim.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.services import forwarder as F            # noqa: E402
from app.models.forwarder import ForwarderDevice   # noqa: E402
from tests.test_forwarder_devices import _DB, _Cookie, _dev, _sig, RAW   # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RAW2 = b'{"kind":"login","account":"09029315496","code":"112233"}'


class TestTheRow:

    def test_the_second_sim_lives_on_the_device_and_is_migrated(self):
        assert hasattr(ForwarderDevice, "sim_phone2")
        d = ForwarderDevice(); d.sim_phone, d.sim_phone2 = "09125005495", None
        assert d.sims() == ["09125005495"]
        d.sim_phone2 = "09029315496"
        assert d.sims() == ["09125005495", "09029315496"]
        db = (ROOT / "app/database.py").read_text(encoding="utf-8")
        assert "ADD COLUMN IF NOT EXISTS sim_phone2 VARCHAR(20)" in db and "_migrate_forwarder_sim2," in db


class TestADeviceMayAnswerForTheSimsInsideIt:

    @pytest.mark.asyncio
    async def test_a_fresh_second_sim_gets_its_first_login_code_through(self):
        """No session exists for the number yet — that is what the code is for."""
        d = _dev(user_id=7); d.sim_phone, d.sim_phone2 = "09125005495", "09029315496"
        db = _DB(devices=[d], cookies=[_Cookie("09125005495", owner=7)]); db._want_device = "abcd1234"
        dev, how = await F.authenticate(db, device_id="abcd1234", raw=RAW2, signature=_sig("s" * 64, RAW2),
                                        plain="", account="09029315496", legacy_secret=None)
        assert dev is d and how == "device"

    @pytest.mark.asyncio
    async def test_a_number_that_is_neither_a_session_nor_a_sim_is_still_refused(self):
        d = _dev(user_id=7); d.sim_phone, d.sim_phone2 = "09125005495", "09029315496"
        db = _DB(devices=[d], cookies=[_Cookie("09125005495", owner=7)]); db._want_device = "abcd1234"
        with pytest.raises(F.ForwarderAuthError) as e:
            await F.authenticate(db, device_id="abcd1234", raw=RAW, signature=_sig("s" * 64),
                                 plain="", account="09058432452", legacy_secret=None)
        assert e.value.status == 403

    @pytest.mark.asyncio
    async def test_a_colleagues_session_beats_a_sim_claim(self):
        """Typing somebody else's number as «my SIM 2» must not let this phone
        answer their prompts."""
        d = _dev(user_id=7); d.sim_phone, d.sim_phone2 = "09125005495", "09058432452"
        db = _DB(devices=[d], cookies=[_Cookie("09058432452", owner=99)]); db._want_device = "abcd1234"
        with pytest.raises(F.ForwarderAuthError) as e:
            await F.authenticate(db, device_id="abcd1234", raw=RAW, signature=_sig("s" * 64),
                                 plain="", account="09058432452", legacy_secret=None)
        assert e.value.status == 403

    @pytest.mark.asyncio
    async def test_sims_compare_by_digits(self):
        d = _dev(user_id=7); d.sim_phone, d.sim_phone2 = None, "+98 902 931 5496"
        db = _DB(devices=[d], cookies=[]); db._want_device = "abcd1234"
        assert await F.owns_divar_account(db, 7, "09029315496", d)


class TestTheQrAndTheGuide:

    def _src(self):
        import inspect
        from app.api.routes import forwarder as R
        return inspect.getsource(R.device_config)

    def test_the_qr_carries_account2_only_when_there_is_one(self):
        src = self._src()
        assert 'payload["account2"]' in src and "if acct2:" in src
        assert '"device": row.device_id, "secret": row.secret' in src

    def test_the_manual_guide_gets_a_second_rule_pair_bound_to_slot_2(self):
        src = self._src()
        assert '"rules_sim2"' in src and '"sim_slot": 2' in src
        assert 'tpl2.replace("__KIND__", "contact")' in src and 'tpl2.replace("__KIND__", "login")' in src

    def test_the_events_log_shows_both_sims_traffic(self):
        import inspect
        from app.api.routes import forwarder as R
        assert "for p in row.sims()" in inspect.getsource(R.device_events)

    def test_the_panel_asks_for_and_shows_both(self):
        js = (ROOT / "frontend/js/app.js").read_text(encoding="utf-8")
        assert "sim_phone2: sim2.trim() || null" in js
        assert "function _fwSimsCell" in js and "fwEditPhone(${dv.id}, 2)" in js
        assert "c.rules_sim2" in js and "SIM 2 ←" in js
        html = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
        assert "سیم‌کارت‌ها</th>" in html


# ── through the real app (Postgres) ──────────────────────────────────────────

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
    cfg.scrape_scheduler = False
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


def _seed_user(username):
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.models.user import User
    from app.auth.jwt import get_password_hash

    async def _go():
        eng = create_async_engine(os.environ["DATABASE_URL"])
        maker = async_sessionmaker(eng, expire_on_commit=False)
        try:
            async with maker() as s:
                s.add(User(username=username, full_name="رسا", role="admin", permissions=["forwarder", "divar_auth"],
                           hashed_password=get_password_hash("pw123456"), is_active=True, divar_phone="09146382408"))
                await s.commit()
        finally:
            await eng.dispose()
    asyncio.run(_go())


def _tok(client, username):
    r = client.post("/api/users/token", data={"username": username, "password": "pw123456"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


class TestThroughTheApp:

    def test_one_phone_two_sims_end_to_end(self, client):
        import hashlib, hmac, json
        _seed_user("ds_rasa")
        h = _tok(client, "ds_rasa")
        r = client.post("/api/forwarder/devices", headers=h,
                        json={"label": "گوشی رسا", "sim_phone": "09125005495", "sim_phone2": "۰۹۰۲۹۳۱۵۴۹۶"})
        assert r.status_code == 200, r.text
        dev = r.json()
        assert dev["sim_phone"] == "09125005495" and dev["sim_phone2"] == "09029315496", "Persian digits normalised"
        assert client.post("/api/forwarder/devices", headers=h,
                           json={"label": "x", "sim_phone": "09125005495", "sim_phone2": "09125005495"}).status_code == 400

        cfg = client.get(f"/api/forwarder/devices/{dev['id']}/config", headers=h).json()
        q = parse_qs(urlparse(cfg["setup_payload"]).query)
        assert q["account"] == ["09125005495"] and q["account2"] == ["09029315496"]
        assert cfg["accounts"] == ["09125005495", "09029315496"]
        assert [x["sim_slot"] for x in cfg["rules_sim2"]] == [2, 2]
        assert '"account":"09029315496"' in cfg["rules_sim2"][0]["template"]

        # the second SIM's first-ever login code arrives before any Divar session exists for it
        body = json.dumps({"kind": "login", "account": "09029315496", "code": "445566",
                           "text": "کد تایید: 445566"}).encode()
        sig = hmac.new(dev["secret"].encode(), body, hashlib.sha256).hexdigest()
        r = client.post("/api/scraper/otp-inbound", data=body,
                        headers={"X-Forwarder-Id": dev["device_id"], "X-Signature": sig,
                                 "Content-Type": "application/json"})
        assert r.status_code == 200, r.text
        assert r.json()["reason"] == "parked_for_login"
        # a number that is in neither the sessions nor the phone is still refused
        body = json.dumps({"kind": "login", "account": "09111111111", "code": "1"}).encode()
        sig = hmac.new(dev["secret"].encode(), body, hashlib.sha256).hexdigest()
        assert client.post("/api/scraper/otp-inbound", data=body,
                           headers={"X-Forwarder-Id": dev["device_id"], "X-Signature": sig,
                                    "Content-Type": "application/json"}).status_code == 403

        # the second SIM can be taken out again
        r = client.patch(f"/api/forwarder/devices/{dev['id']}", headers=h, json={"sim_phone2": ""})
        assert r.status_code == 200 and r.json()["sim_phone2"] is None
        cfg = client.get(f"/api/forwarder/devices/{dev['id']}/config", headers=h).json()
        assert "account2" not in parse_qs(urlparse(cfg["setup_payload"]).query) and cfg["rules_sim2"] == []
