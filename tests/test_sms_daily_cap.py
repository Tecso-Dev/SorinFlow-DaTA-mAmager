"""
A daily cap on verification-code SMS, all addresses together. Once the app
sees real addresses, the per-address budgets stop being one accidental
budget for everybody — and many addresses together could empty the credit.
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_sms_cap.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

import app.services.verification as v   # noqa: E402


@pytest.fixture
def sent(monkeypatch):
    import fakeredis.aioredis
    from app.services import backup_service as bk, email_service
    # one store, a client per call: each asyncio.run is a new event loop
    server = fakeredis.FakeServer()

    async def _get_redis():
        return fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    monkeypatch.setattr(v, "get_redis", _get_redis)
    monkeypatch.setattr(v.settings, "auth_sms_daily_cap", 2)
    monkeypatch.setattr(v.settings, "auth_sms_provider", "console")
    out = {"sms": [], "email": [], "alerts": []}

    async def _sms(phone, text, **kw):
        out["sms"].append(phone)
        return {"success": True}

    async def _tpl(db):
        return None

    async def _email(to, subject, html, plain, **kw):
        out["email"].append(to)
        return {"success": True}

    async def _alert(text):
        out["alerts"].append(text)
        return True
    monkeypatch.setattr(v, "send_sms", _sms)
    monkeypatch.setattr(v, "resolve_otp_template", _tpl)
    monkeypatch.setattr(email_service, "send", _email)
    monkeypatch.setattr(bk, "send_text", _alert)
    return out


def _issue(n, **kw):
    async def go():
        res = await v.issue_code("portal_login", f"user{n}", f"0912000000{n}", **kw)
        await asyncio.sleep(0)            # let the alert task run
        return res
    return asyncio.run(go())


class TestTheDailyCap:

    def test_sms_stops_at_the_cap_with_its_own_message_and_one_alert(self, sent):
        _issue(1, channel="sms")
        _issue(2, channel="sms")
        with pytest.raises(v.VerificationError) as e:
            _issue(3, channel="sms")
        assert e.value.message == v.SMS_CAP_MESSAGE and e.value.retry_after > 0
        with pytest.raises(v.VerificationError):
            _issue(4, channel="sms")
        assert len(sent["sms"]) == 2
        assert len(sent["alerts"]) == 1 and "AUTH_SMS_DAILY_CAP" in sent["alerts"][0]

    def test_with_an_address_known_the_code_goes_by_email_instead(self, sent):
        _issue(1, channel="sms")
        _issue(2, channel="sms")
        res = _issue(3, email="u3@example.com")
        assert res.channel == "email" and sent["email"] == ["u3@example.com"]

    def test_zero_means_no_cap(self, sent, monkeypatch):
        monkeypatch.setattr(v.settings, "auth_sms_daily_cap", 0)
        for n in range(1, 5):
            _issue(n, channel="sms")
        assert len(sent["sms"]) == 4 and not sent["alerts"]
