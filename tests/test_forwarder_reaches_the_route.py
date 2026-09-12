"""
The forwarder's two endpoints, through the REAL app, with maintenance on and
no session.

Both live bugs in this feature were in wiring outside the route function,
which is exactly what unit tests of the route cannot see:

  * maintenance mode answered the phone's POSTs with the 503 notice page —
    a phone has no bearer and no bypass cookie;
  * the scraper router's permission dependency answered «Not authenticated»
    before the HMAC check ever ran — a phone is not a user.

Forty-five unit tests were green while both were live. This boots the real
ASGI app and asserts the one thing that proves all four layers are right in
order: an unsigned POST must reach the route and be refused BY THE ROUTE —
401 «bad signature» — not 503, not «Not authenticated», not «Invalid or
missing API key».
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_fr.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

import app.main as m                                   # noqa: E402
from app.services import maintenance as mt             # noqa: E402

SECRET = "integration-secret"


@pytest.fixture
def client(monkeypatch):
    """The real app, production-shaped: API_KEY set, maintenance ON, an
    inbound secret configured, no session of any kind."""
    from starlette.testclient import TestClient
    monkeypatch.setattr(m.settings, "api_key", "prod-like-api-key", raising=False)
    monkeypatch.setattr(m.settings, "otp_inbound_secret", SECRET, raising=False)

    async def _closed(db, **kw):
        return (True, "سایت در حال بروزرسانی می‌باشد", "")   # enabled, message, bypass
    monkeypatch.setattr(mt, "get_state", _closed)

    # No context manager: no lifespan, so no migrations and no background
    # loops. The middlewares are what is under test and they run per request.
    return TestClient(m.app, raise_server_exceptions=True)


class TestThePhoneGetsThroughToTheRoute:

    @pytest.mark.parametrize("path", ["/api/scraper/otp-inbound", "/api/scraper/forwarder-heartbeat"])
    def test_unsigned_is_refused_by_the_route_itself(self, client, path):
        r = client.post(path, json={"kind": "test", "account": "09120000001"})
        assert r.status_code == 401, (r.status_code, r.text)
        assert r.json().get("detail") == "bad signature", r.text
        # the three ways the wiring used to answer instead
        assert "maintenance" not in r.text
        assert "Not authenticated" not in r.text
        assert "API key" not in r.text

    def test_a_correct_secret_is_accepted_through_the_same_wiring(self, client, monkeypatch):
        from app.api.routes import scraper as S
        async def _no_rl(request, limit=20): return None
        monkeypatch.setattr(S, "_forwarder_rate_limit", _no_rl)
        from app.services import sms_log
        async def _rec(*a, **k): return True
        monkeypatch.setattr(sms_log, "record", _rec)
        r = client.post("/api/scraper/otp-inbound",
                        json={"kind": "test", "account": "09120000001", "text": "t"},
                        headers={"X-OTP-Secret": SECRET})
        assert r.status_code == 200, r.text
        assert r.json()["kind"] == "test" and r.json()["matched"] is False

    def test_maintenance_really_was_on_for_that_request(self, client):
        """The control: a path that passes the API-key gate but is not a
        maintenance exemption must get the closed page — otherwise the test
        above proved nothing about maintenance."""
        r = client.get("/dashboard", headers={"Accept": "application/json"})
        assert r.status_code == 503, (r.status_code, r.text[:120])
        assert "maintenance" in r.text or "بروزرسانی" in r.text

    def test_the_panel_read_is_still_gated(self, client):
        """/forwarders is for the panel and must not have ridden along."""
        r = client.get("/api/scraper/forwarders", headers={"X-API-Key": "prod-like-api-key"})
        assert r.status_code in (401, 403, 503), (r.status_code, r.text[:120])
