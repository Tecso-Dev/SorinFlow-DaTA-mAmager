"""
Automatic OTP intake from a phone-side SMS forwarder.

The waiting browser needs a six-digit code that arrives on a SIM in somebody's
pocket. A forwarder app POSTs each Divar SMS here within seconds and the code
goes onto the same rail the panel uses — otp_store.submit, same event — so the
browser types it without anyone opening the panel.

The parts that can go wrong quietly are the parts tested: a forged POST, a
code handed to the wrong account's prompt, a late first SMS overwriting a
fresh resend, Persian digits, and the two endpoints being 401ed by the API-key
gate in production the way the login endpoints once were.
"""
import asyncio
import hashlib
import hmac
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_oi.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.api.routes import scraper as R          # noqa: E402
from app.scraper import otp_store                # noqa: E402

SECRET = "test-secret-please-ignore"


class _Req:
    """Only what the routes touch: body(), headers, client.host."""
    def __init__(self, body: dict, headers=None, host="10.0.0.9"):
        self._raw = json.dumps(body).encode("utf-8")
        self.headers = {k: v for k, v in (headers or {}).items()}
        class _C: pass
        self.client = _C(); self.client.host = host
    async def body(self): return self._raw


def _signed(body: dict, secret=SECRET):
    raw = json.dumps(body).encode("utf-8")
    return _Req(body, {"X-Signature": hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()})


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    """No redis, no database, a known secret."""
    otp_store._store.clear(); otp_store._sent.clear(); otp_store._login_codes.clear()
    monkeypatch.setattr(R.settings, "otp_inbound_secret", SECRET, raising=False)
    async def _no_rl(request, limit=20): return None
    monkeypatch.setattr(R, "_forwarder_rate_limit", _no_rl)
    from app.services import sms_log
    recorded = []
    async def _rec(*a, **k): recorded.append((a, k)); return True
    monkeypatch.setattr(sms_log, "record", _rec)
    yield recorded


class TestParsing:

    @pytest.mark.parametrize("text,code", [
        ("کد امنیتی دریافت اطلاعات تماس دیوار:\nCode: 523969", "523969"),
        ("Code:593154", "593154"),
        ("Code: ۵۲۳۹۶۹", "523969"),                     # Persian digits
        ("Code: ٥٢٣٩٦٩", "523969"),                     # Arabic-Indic digits
        ("کد تایید دیوار: 771122 برای دیگران نفرستید.", "771122"),   # no «Code:» at all
        ("no code here 12345 and 1234567", None),        # 5 and 7 digits are not it
    ])
    def test_code_extraction(self, text, code):
        assert R.extract_otp_code(text) == code

    @pytest.mark.parametrize("text,kind", [
        ("کد امنیتی دریافت اطلاعات تماس دیوار: Code: 1", "contact"),
        ("کد تایید دیوار: Code: 1", "login"),
        ("کد تأیید دیوار: Code: 1", "login"),            # hamza spelling
        ("something else entirely", None),
    ])
    def test_kind_detection(self, text, kind):
        assert R.detect_otp_kind(text) == kind


class TestAuth:

    @pytest.mark.asyncio
    async def test_a_good_signature_passes(self):
        req = _signed({"kind": "test"})
        await R._verify_forwarder(req, await req.body())

    @pytest.mark.asyncio
    async def test_a_bad_signature_is_401(self):
        from fastapi import HTTPException
        req = _Req({"kind": "test"}, {"X-Signature": "00" * 32})
        with pytest.raises(HTTPException) as e:
            await R._verify_forwarder(req, await req.body())
        assert e.value.status_code == 401

    @pytest.mark.asyncio
    async def test_no_headers_is_401(self):
        from fastapi import HTTPException
        req = _Req({"kind": "test"})
        with pytest.raises(HTTPException) as e:
            await R._verify_forwarder(req, await req.body())
        assert e.value.status_code == 401

    @pytest.mark.asyncio
    async def test_the_plain_secret_header_is_accepted(self):
        req = _Req({"kind": "test"}, {"X-OTP-Secret": SECRET})
        await R._verify_forwarder(req, await req.body())

    @pytest.mark.asyncio
    async def test_unconfigured_is_503_not_401(self, monkeypatch):
        """«feature off» must not read as «your credentials are wrong»."""
        from fastapi import HTTPException
        monkeypatch.setattr(R.settings, "otp_inbound_secret", "", raising=False)
        req = _Req({"kind": "test"}, {"X-OTP-Secret": "anything"})
        with pytest.raises(HTTPException) as e:
            await R._verify_forwarder(req, await req.body())
        assert e.value.status_code == 503

    def test_the_signature_is_over_the_raw_body(self):
        """Re-serialising JSON changes byte order and whitespace; the check
        must hash what came over the wire."""
        import inspect
        src = inspect.getsource(R.otp_inbound)
        assert "raw = await request.body()" in src
        assert "_verify_forwarder(request, raw)" in src


class TestMatchingByAccount:

    def test_by_account_never_latest(self):
        otp_store.request("jobA:1", "09120000001")
        time.sleep(0.01)
        otp_store.request("jobB:1", "09120000002")     # newer, different account
        hit = otp_store.find_pending_for_account("09120000001")
        assert hit and hit[0] == "jobA:1"

    @pytest.mark.parametrize("stored,incoming", [
        ("09120000001", "+989120000001"),
        ("09120000001", "00989120000001"),
        ("۰۹۱۲۰۰۰۰۰۰۱", "09120000001"),
    ])
    def test_prefix_and_digit_forms_are_the_same_phone(self, stored, incoming):
        otp_store.request("k:1", stored)
        assert otp_store.find_pending_for_account(incoming) is not None

    def test_no_account_no_match(self):
        otp_store.request("k:1", "09120000001")
        assert otp_store.find_pending_for_account("") is None
        assert otp_store.find_pending_for_account("09999999999") is None

    def test_an_answered_request_is_not_a_candidate(self):
        otp_store.request("k:1", "09120000001")
        otp_store.submit("k:1", "111111")
        assert otp_store.find_pending_for_account("09120000001") is None


class TestInboundReleasesTheWaitingBrowser:
    """The integration the whole feature is for."""

    @pytest.mark.asyncio
    async def test_a_matching_contact_code_fires_the_event(self, _quiet):
        evt = otp_store.request("job:ad1", "09120000001")
        body = {"kind": "contact", "account": "09120000001", "code": "",
                "text": "کد امنیتی دریافت اطلاعات تماس دیوار:\nCode: 523969",
                "sentStamp": int(time.time() * 1000), "receivedStamp": int(time.time() * 1000)}
        out = await R.otp_inbound(_signed(body))
        assert out["matched"] is True and out["kind"] == "contact"
        assert evt.is_set(), "the extractor's wait would not have woken"
        assert otp_store.pop_code("job:ad1") == "523969"
        assert otp_store.pop_sent_stamp("job:ad1") is not None
        assert _quiet and _quiet[-1][1].get("matched_key") == "job:ad1"

    @pytest.mark.asyncio
    async def test_a_stale_code_is_refused(self):
        """A late FIRST SMS must not land on a fresh resend. The request's
        clock restarts on resend; a stamp older than it is the old SMS."""
        evt = otp_store.request("job:ad1", "09120000001")
        otp_store._store["job:ad1"]["ts"] = time.time()          # «resent just now»
        body = {"kind": "contact", "account": "09120000001", "code": "523969",
                "sentStamp": int((time.time() - 120) * 1000)}    # sent two minutes ago
        out = await R.otp_inbound(_signed(body))
        assert out["matched"] is False and out["reason"] == "stale_code"
        assert not evt.is_set()

    @pytest.mark.asyncio
    async def test_no_pending_for_that_account_is_logged_not_guessed(self, _quiet):
        otp_store.request("job:ad1", "09120000002")             # a DIFFERENT account waits
        body = {"kind": "contact", "account": "09120000001", "code": "523969",
                "sentStamp": int(time.time() * 1000)}
        out = await R.otp_inbound(_signed(body))
        assert out["matched"] is False and out["reason"] == "no_pending_for_account"
        assert not otp_store._store["job:ad1"]["event"].is_set(), "handed to the wrong account"

    @pytest.mark.asyncio
    async def test_kind_is_inferred_from_text_when_missing(self):
        otp_store.request("job:ad1", "09120000001")
        body = {"account": "09120000001", "text": "کد امنیتی دریافت اطلاعات تماس دیوار: Code: 111222",
                "sentStamp": int(time.time() * 1000)}
        out = await R.otp_inbound(_signed(body))
        assert out["kind"] == "contact" and out["matched"] is True

    @pytest.mark.asyncio
    async def test_a_login_code_is_parked_and_consumed_once(self):
        body = {"kind": "login", "account": "09120000001", "code": "593154"}
        out = await R.otp_inbound(_signed(body))
        assert out["reason"] == "parked_for_login"
        assert otp_store.take_login_code("+989120000001") == "593154"
        assert otp_store.take_login_code("09120000001") is None

    def test_a_parked_login_code_expires(self):
        otp_store.put_login_code("0912", "1")
        otp_store._login_codes["0912"] = ("1", time.time() - otp_store.LOGIN_CODE_TTL - 1)
        assert otp_store.take_login_code("0912") is None

    @pytest.mark.asyncio
    async def test_the_code_is_masked_in_the_log(self, _quiet):
        otp_store.request("job:ad1", "09120000001")
        body = {"kind": "contact", "account": "09120000001", "code": "523969",
                "sentStamp": int(time.time() * 1000)}
        await R.otp_inbound(_signed(body))
        logged = _quiet[-1][1].get("code")
        assert logged == "****69", logged


class TestTheBrowserAsksAgainInsideDivarsWindow:

    def test_auto_resend_twice_at_most_without_extending_the_wait(self):
        import inspect
        from app.scraper.contact_extractor import ContactExtractor
        src = inspect.getsource(ContactExtractor._handle_sms_otp_if_present)
        code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
        assert "_auto_resends < 2" in code
        assert "otp_store.restart_clock(self.otp_key)" in code
        # the automatic branch must not zero `waited` (the operator's manual
        # resend does; that one is a person's decision)
        auto = code[code.index("if waited >= _next_auto"):code.index("take_resend")]
        assert "waited = 0" not in auto

    def test_delivery_latency_is_observed_when_a_forwarder_sent_the_code(self):
        import inspect
        from app.scraper.contact_extractor import ContactExtractor
        src = inspect.getsource(ContactExtractor._handle_sms_otp_if_present)
        assert "pop_sent_stamp" in src and "otp_delivery_seconds.observe" in src

    def test_the_histogram_exists(self):
        from app import metrics
        assert hasattr(metrics, "otp_delivery_seconds")


class TestItIsReachableInProduction:
    """The API-key gate is an exact-match allowlist and API_KEY is empty
    locally — the trap that 401ed the login endpoints in production."""

    def test_both_machine_endpoints_are_allowlisted(self):
        src = open("app/main.py", encoding="utf-8").read()
        pub = src.split("public_paths = {")[1].split("}")[0]
        assert "/api/scraper/otp-inbound" in pub
        assert "/api/scraper/forwarder-heartbeat" in pub

    def test_the_secret_reaches_the_pod(self):
        y = open("k8s/04-backend.yaml", encoding="utf-8").read()
        assert "key: OTP_INBOUND_SECRET, optional: true" in y

    def test_the_panel_says_auto_or_manual(self):
        html = open("frontend/index.html", encoding="utf-8").read()
        js = open("frontend/js/app.js", encoding="utf-8").read()
        assert 'id="otp2-mode"' in html
        assert "function _otp2SetMode" in js and "خودکار" in js and "دستی" in js

    def test_otp_pending_carries_the_forwarder_map(self):
        import inspect
        src = inspect.getsource(R.get_otp_pending)
        assert "list_forwarders" in src

    def test_the_readme_documents_it(self):
        text = open("README.md", encoding="utf-8").read()
        for needle in ("/api/scraper/otp-inbound", "/api/scraper/forwarder-heartbeat",
                       "OTP_INBOUND_SECRET", "X-Signature", "sorinflow_otp_delivery_seconds"):
            assert needle in text, needle
