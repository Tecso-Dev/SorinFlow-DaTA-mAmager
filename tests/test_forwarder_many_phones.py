"""
«هر شخص می‌خواهد چند گوشیِ دو سیم‌کارته را با هم اضافه کند، همه را در فهرست
داشته باشد و از همه استفاده کند.»

Several phones per person already worked; what did not:

  * a dual-SIM phone's second number always looked offline — the heartbeat
    was stored under the one account the app reports;
  * the «code needed» alert only looked at SIM 1 of each phone, and told the
    owner of a second-SIM number that no phone was registered for it;
  * nothing stopped one number being registered on two phones (which one
    forwards its code?), or a colleague's number on yours;
  * every panel saw every person's phones in the code dialog.
"""
import asyncio
import hashlib
import hmac
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_many_phones.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

FAKE = None


@pytest.fixture(scope="module")
def client():
    global FAKE
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
    FAKE = fakeredis.aioredis.FakeRedis(decode_responses=True)

    async def _get_redis():
        return FAKE
    db.get_redis = _get_redis
    import app.services.verification as v
    v.get_redis = _get_redis
    from fastapi.testclient import TestClient
    import app.main as m
    with TestClient(m.app) as c:
        yield c
    (cfg.environment, cfg.api_key, cfg.cookies_path, cfg.scrape_scheduler) = saved


@pytest.fixture(scope="module")
def people(client):
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.models.user import User
    from app.models.cookie import Cookie
    from app.auth.jwt import get_password_hash

    async def _go():
        eng = create_async_engine(os.environ["DATABASE_URL"])
        maker = async_sessionmaker(eng, expire_on_commit=False)
        try:
            async with maker() as s:
                a = User(username="mp_a", full_name="الف", role="admin", permissions=["forwarder", "scraper"],
                         hashed_password=get_password_hash("pw123456"), is_active=True)
                b = User(username="mp_b", full_name="ب", role="admin", permissions=["forwarder", "scraper"],
                         hashed_password=get_password_hash("pw123456"), is_active=True)
                s.add_all([a, b])
                await s.flush()
                s.add(Cookie(phone_number="09357770001", owner_user_id=b.id, cookies=[{"name": "x"}]))
                await s.commit()
                return {"a": a.id, "b": b.id}
        finally:
            await eng.dispose()
    return asyncio.run(_go())


def _tok(client, username):
    r = client.post("/api/users/token", data={"username": username, "password": "pw123456"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


class TestManyPhonesEachWithTwoSims:

    def test_two_dual_sim_phones_are_both_listed(self, client, people):
        h = _tok(client, "mp_a")
        for label, s1, s2 in (("گوشی ۱", "09351110001", "09351110002"),
                              ("گوشی ۲", "09351110003", "09351110004")):
            r = client.post("/api/forwarder/devices", headers=h,
                            json={"label": label, "sim_phone": s1, "sim_phone2": s2})
            assert r.status_code == 200, r.text
        d = client.get("/api/forwarder/devices", headers=h).json()
        assert d["count"] == 2
        sims = {x for dv in d["devices"] for x in (dv["sim_phone"], dv["sim_phone2"])}
        assert sims == {"09351110001", "09351110002", "09351110003", "09351110004"}

    def test_one_number_cannot_sit_in_two_phones(self, client, people):
        r = client.post("/api/forwarder/devices", headers=_tok(client, "mp_a"),
                        json={"label": "گوشی ۳", "sim_phone": "09351110002"})
        assert r.status_code == 409, r.text
        assert "گوشی ۱" in r.json()["detail"]

    def test_nor_a_colleagues_number_in_yours(self, client, people):
        h = _tok(client, "mp_a")
        # their Divar session
        r = client.post("/api/forwarder/devices", headers=h,
                        json={"label": "x", "sim_phone": "09357770001"})
        assert r.status_code == 403, r.text
        # their phone's SIM
        client.post("/api/forwarder/devices", headers=_tok(client, "mp_b"),
                    json={"label": "ب", "sim_phone": "09357770009"})
        r = client.post("/api/forwarder/devices", headers=h,
                        json={"label": "x", "sim_phone": "09357770009"})
        assert r.status_code == 403, r.text

    def test_editing_a_phone_keeps_its_own_numbers(self, client, people):
        h = _tok(client, "mp_a")
        dev = next(d for d in client.get("/api/forwarder/devices", headers=h).json()["devices"]
                   if d["label"] == "گوشی ۲")
        r = client.patch(f"/api/forwarder/devices/{dev['id']}", headers=h,
                         json={"sim_phone2": "09351110004", "label": "گوشی دوم"})
        assert r.status_code == 200, r.text
        r = client.patch(f"/api/forwarder/devices/{dev['id']}", headers=h,
                         json={"sim_phone2": "09351110001"})
        assert r.status_code == 409, "moved onto a number another of my phones holds"

    def test_a_heartbeat_marks_both_sims_online(self, client, people):
        h = _tok(client, "mp_a")
        dev = next(d for d in client.get("/api/forwarder/devices", headers=h).json()["devices"]
                   if d["sim_phone"] == "09351110001")
        secret = client.get(f"/api/forwarder/devices/{dev['id']}/config", headers=h).json()["device"]["secret"]
        body = json.dumps({"account": "09351110001", "battery": 80, "network": "wifi",
                           "version": "3.2"}).encode()
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        r = client.post("/api/scraper/forwarder-heartbeat", data=body,
                        headers={"X-Forwarder-Id": dev["device_id"], "X-Signature": sig,
                                 "Content-Type": "application/json"})
        assert r.status_code == 200, r.text
        seen = client.get("/api/scraper/forwarders", headers=h).json()["forwarders"]
        assert {"9351110001", "9351110002"} <= {k[-10:] for k in seen}, \
            "the second SIM of a phone that just checked in reads as offline"

    def test_nobody_else_sees_my_phones(self, client, people):
        seen = client.get("/api/scraper/forwarders", headers=_tok(client, "mp_b")).json()["forwarders"]
        assert not any(k.endswith("351110001") or k.endswith("351110002") for k in seen)


class TestTheAlertLooksAtBothSims:

    def test_the_code_needed_email_checks_every_sim(self):
        import inspect
        from app.scraper.contact_extractor import ContactExtractor
        src = inspect.getsource(ContactExtractor._notify_code_needed)
        assert "for p in d.sims()" in src
        assert "same_phone(d.sim_phone, self.account_phone)" not in src
