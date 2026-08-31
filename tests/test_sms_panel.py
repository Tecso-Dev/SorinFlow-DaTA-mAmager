"""
پیامک — the SMS panel.

The tests that matter here are the ones covering things that fail *silently*:
a provider that reports success in the body of a 200, a phone number typed with
Persian digits, an API key that reaches the browser, and a broadcast that goes
to more people than the person approved.
"""
import json
import pytest


class TestNumberNormalisation:
    """Numbers arrive from copy-paste, from Divar, and from people typing.

    A number that fails to normalise is not sent to, which is a silent
    non-delivery — the worst kind of bug in a messaging system.
    """

    @pytest.mark.parametrize("raw,expected", [
        ("09121234567", "09121234567"),
        ("9121234567", "09121234567"),
        ("+989121234567", "09121234567"),
        ("989121234567", "09121234567"),
        ("0912 123 4567", "09121234567"),
        ("0912-123-4567", "09121234567"),
        ("(0912) 1234567", "09121234567"),
        # Persian and Arabic-Indic digits: what a Persian keyboard produces,
        # and what pasting from Divar produces.
        ("۰۹۱۲۱۲۳۴۵۶۷", "09121234567"),
        ("٠٩١٢١٢٣٤٥٦٧", "09121234567"),
    ])
    def test_accepted_forms(self, raw, expected):
        from app.api.routes.sms import normalize_mobile
        assert normalize_mobile(raw) == expected

    @pytest.mark.parametrize("raw", [
        "", None, "0912123456",           # too short
        "091212345678",                   # too long
        "02144556677",                    # a landline
        "8121234567",                     # does not start 9
        "not a number",
    ])
    def test_rejected_forms(self, raw):
        from app.api.routes.sms import normalize_mobile
        assert normalize_mobile(raw) is None


class TestKavenegarErrorsAreNotSuccesses:
    """Kavenegar answers every call with HTTP 200 and puts the outcome in the
    body. The previous version of this module checked only the HTTP status, so
    an invalid number, an empty balance and a rejected sender line were all
    recorded as delivered."""

    def _resp(self, payload, status=200):
        import httpx
        return httpx.Response(status_code=status,
                              content=json.dumps(payload).encode(),
                              headers={"content-type": "application/json"},
                              request=httpx.Request("POST", "https://api.kavenegar.com/"))

    def test_a_200_carrying_an_error_status_raises(self):
        from app.services.sms_service import _unwrap, SmsError
        r = self._resp({"return": {"status": 411, "message": "receptor is invalid"},
                        "entries": None})
        with pytest.raises(SmsError) as e:
            _unwrap(r)
        assert e.value.status == 411

    def test_out_of_credit_is_reported_in_persian(self):
        from app.services.sms_service import _unwrap, SmsError
        r = self._resp({"return": {"status": 418, "message": "credit is not enough"},
                        "entries": None})
        with pytest.raises(SmsError) as e:
            _unwrap(r)
        assert "اعتبار" in e.value.message

    def test_a_real_success_returns_entries(self):
        from app.services.sms_service import _unwrap
        r = self._resp({"return": {"status": 200, "message": "ok"},
                        "entries": [{"messageid": 123, "cost": 120}]})
        assert _unwrap(r)[0]["messageid"] == 123

    def test_a_single_entry_object_is_still_a_list(self):
        """Kavenegar returns a bare object for some endpoints and a list for
        others; callers index [0] either way."""
        from app.services.sms_service import _unwrap
        r = self._resp({"return": {"status": 200, "message": "ok"},
                        "entries": {"remaincredit": 50000}})
        assert _unwrap(r)[0]["remaincredit"] == 50000

    def test_a_non_json_body_does_not_explode(self):
        from app.services.sms_service import _unwrap, SmsError
        import httpx
        r = httpx.Response(status_code=200, content=b"<html>gateway</html>",
                           request=httpx.Request("POST", "https://api.kavenegar.com/"))
        with pytest.raises(SmsError):
            _unwrap(r)


class TestApiKeyHandling:
    def test_encryption_round_trips(self):
        from app.services.sms_service import _encrypt, _decrypt
        secret = "6B1F2C4A-live-key-9F3E"
        assert _decrypt(_encrypt(secret)) == secret

    def test_an_undecryptable_value_reads_as_absent(self):
        """A rotated SECRET_KEY must force re-entry, not raise inside a send."""
        from app.services.sms_service import _decrypt
        assert _decrypt("not-a-fernet-token") == ""

    def test_the_mask_identifies_without_revealing(self):
        from app.services.sms_service import mask_key
        m = mask_key("ABCD1234567890EFGH")
        assert m.startswith("ABCD") and m.endswith("EFGH")
        assert "1234567890" not in m

    def test_a_short_key_is_fully_hidden(self):
        from app.services.sms_service import mask_key
        assert set(mask_key("abc123")) == {"*"}

    def test_the_settings_route_never_returns_the_key(self):
        """The whole key must not appear in the response model at all — the
        field is named api_key_masked precisely so a future edit cannot make
        `api_key` real by accident."""
        import inspect
        from app.api.routes import sms as r
        src = inspect.getsource(r.get_sms_settings)
        assert "api_key_masked" in src
        assert "mask_key" in src


class TestBroadcastGuards:
    def test_a_changed_audience_refuses_the_send(self):
        """confirm_count is what the panel showed the person. If the real
        audience has grown since, the send is refused rather than quietly
        reaching people who were never counted."""
        import inspect
        from app.api.routes import sms as r
        src = inspect.getsource(r.sms_broadcast)
        assert "confirm_count" in src
        assert "409" in src

    def test_broadcast_is_super_admin_only(self):
        import inspect
        from app.api.routes import sms as r
        sig = inspect.signature(r.sms_broadcast)
        assert "_super_admin" in inspect.getsource(r.sms_broadcast).split("\n")[2] or \
               sig.parameters["user"].default is not None

    def test_audiences_are_a_fixed_set_not_a_query(self):
        """Letting the panel compose its own recipient query is how a message
        reaches the wrong list once and never lives it down."""
        from app.api.routes.sms import AUDIENCES
        import inspect
        from app.api.routes import sms
        # A fixed, named set — the point is that the browser cannot compose the
        # recipient query, not that the list never grows.
        assert isinstance(AUDIENCES, dict) and AUDIENCES
        assert {"staff", "visitors", "contacts"} <= set(AUDIENCES)
        src = inspect.getsource(sms._audience_numbers)
        # every branch is an explicit elif on a known key, ending in a refusal
        assert 'raise HTTPException(400, f"گروه نامعتبر' in src


class TestPanelWiring:
    """A route file with no importer, or a section registered in four of five
    places, parses and tests clean while doing nothing. This repo has shipped
    that exact bug before."""

    def test_the_router_is_registered_and_gated(self):
        import inspect
        from app.api import routes
        src = inspect.getsource(routes)
        assert 'prefix="/sms"' in src
        assert '_perm("sms")' in src

    def test_the_permission_exists_in_the_catalog(self):
        from app.auth.permissions import PERMISSIONS
        assert PERMISSIONS.get("sms") == "پیامک"

    def test_the_section_is_registered_everywhere_the_panel_needs_it(self):
        from pathlib import Path
        js = Path("frontend/js/app.js").read_text(encoding="utf-8")
        html = Path("frontend/index.html").read_text(encoding="utf-8")

        assert 'id="nav-link-sms"' in html, "sidebar link"
        assert 'id="section-sms"' in html, "section container"
        assert "'nav-link-sms'" in js, "NAV_PERMISSION"
        assert "sms: 'sms'" in js, "SECTION_PERMISSION"
        assert "sms:        { title:" in js, "SECTION_META"
        assert "case 'sms':" in js, "showSection dispatch"

    def test_every_sms_endpoint_the_ui_calls_exists_on_the_server(self):
        """The UI and the router drifting apart is a 404 nobody notices until a
        person clicks the button."""
        import re
        from pathlib import Path
        import app.main as m

        js = Path("frontend/js/app.js").read_text(encoding="utf-8")
        called = set()
        for match in re.finditer(r"apiCall\(\s*[`'\"](/sms/[^`'\"?]+)", js):
            called.add(match.group(1).rstrip("/"))

        served = {r.path[len("/api"):] for r in m.app.routes
                  if getattr(r, "path", "").startswith("/api/sms")}
        missing = called - served
        assert not missing, f"UI calls SMS endpoints that do not exist: {sorted(missing)}"


class TestPersianCharacterBilling:
    def test_the_ui_counts_seventy_not_one_sixty(self):
        """Persian text is billed at 70 characters per SMS part, not 160.
        Showing the Latin figure understates a broadcast's cost by more than
        double, which is a money bug rather than a cosmetic one."""
        from pathlib import Path
        js = Path("frontend/js/app.js").read_text(encoding="utf-8")
        assert "/ 70)" in js


class TestFailedSendsAreStillRecorded:
    """A broadcast that fails for everyone must leave a trace.

    send_bulk used to return an empty `results` list when no API key was
    configured, and the caller writes one log row per result — so a broadcast
    that reached nobody showed "failed: 40" once on screen and then vanished.
    The history is where someone looks a day later.
    """

    @pytest.mark.asyncio
    async def test_no_api_key_still_yields_one_result_per_recipient(self, monkeypatch):
        from app.services import sms_service as sms

        async def no_creds(db=None):
            return "", ""
        monkeypatch.setattr(sms, "resolve_credentials", no_creds)

        numbers = ["09121234567", "09121234568", "09121234569"]
        out = await sms.send_bulk(numbers, "سلام")

        assert out["success"] is False
        assert out["failed"] == 3
        # one row per recipient, so the caller's log loop records all three
        assert len(out["results"]) == 3
        assert {r["receptor"] for r in out["results"]} == set(numbers)
        assert all(r["ok"] is False and r["error"] for r in out["results"])


class TestBroadcastButtonState:
    def test_it_starts_disabled_and_only_pickaudience_enables_it(self):
        """Enabled before a group is chosen, the button cannot show how many
        people it is about to message — and that count is the confirmation."""
        from pathlib import Path
        js = Path("frontend/js/app.js").read_text(encoding="utf-8")
        load = js.split("async function loadSms(")[1].split("\nasync function")[0]
        assert "bulkBtn.disabled = true" in load
        pick = js.split("function pickAudience(")[1].split("\nasync function")[0]
        assert "_smsAudienceCount" in pick and "btn.disabled" in pick


class TestPlaceholdersAreNotMistakenForValues:
    """Grey placeholder text renders close enough to a filled field that the
    form looks complete when it is empty. It happened on the email panel and
    again on this one — the sender number read as 10004346 while the box was
    blank, so every send went out with no sender."""

    def test_sms_placeholders_are_marked_as_examples(self):
        from pathlib import Path
        html = Path("frontend/index.html").read_text(encoding="utf-8")
        for field in ("sms-sender", "sms-otp-template", "sms-signature", "sms-test-to"):
            i = html.index(f'id="{field}"')
            block = html[i:i + 400]
            if "placeholder=" in block:
                ph = block.split('placeholder="')[1].split('"')[0]
                assert ph.startswith("مثلاً"), \
                    f"{field} placeholder '{ph}' reads like a real value"

    def test_a_failed_credit_lookup_states_the_reason(self):
        """It rendered a bare em dash with the cause hidden in a title
        attribute. Nobody hovers a blank number to find out why it is blank."""
        from pathlib import Path
        js = Path("frontend/js/app.js").read_text(encoding="utf-8")
        fn = js.split("async function loadSmsCredit(")[1].split("\nasync function")[0]
        assert "stat-label" in fn and "text-danger" in fn
        assert "d.error" in fn


class TestSpecCompliance:
    """Checked against kavenegar.com/rest.html, not against their Python SDK,
    which is what this client was originally written from."""

    @pytest.mark.asyncio
    async def test_a_broadcast_without_a_sender_line_is_refused_up_front(self, monkeypatch):
        """sms/send treats sender as optional and falls back to the account
        default; sendarray does not — it takes parallel arrays and 419 is a
        length mismatch. So an unconfigured line failed every chunk and blamed
        the provider."""
        from app.services import sms_service as sms

        async def key_but_no_sender(db=None):
            return "A-REAL-KEY", ""
        monkeypatch.setattr(sms, "resolve_credentials", key_but_no_sender)

        out = await sms.send_bulk(["09121112233", "09121112234"], "سلام")
        assert out["success"] is False
        assert out["failed"] == 2
        assert "فرستنده" in out["error"], "the error must name the missing field"
        assert len(out["results"]) == 2      # still one log row per recipient

    @pytest.mark.asyncio
    async def test_delivery_status_chunks_at_the_500_id_ceiling(self, monkeypatch):
        """«در هر بار اجرای این متد می‌توانید از وضعیت ۵۰۰ پیامک با خبر شوید»,
        and 414 is the error past it. Over the limit the whole batch is lost,
        not just the excess."""
        from app.services import sms_service as sms

        seen = []

        async def fake_call(api_key, action, method, params):
            ids = params["messageid"].split(",")
            seen.append(len(ids))
            return [{"messageid": i, "status": 10} for i in ids]

        async def creds(db=None):
            return "k", "10004346"
        monkeypatch.setattr(sms, "_call", fake_call)
        monkeypatch.setattr(sms, "resolve_credentials", creds)

        out = await sms.delivery_status(list(range(1200)))
        assert seen == [500, 500, 200], f"expected 500-id chunks, got {seen}"
        assert len(out) == 1200, "every id must come back, not just the last chunk"

    def test_the_error_table_says_what_to_do(self):
        from app.services.sms_service import STATUS_FA, DELIVERY_FA
        # the spec names a cause for 422; ours used to be circular
        assert "کاراکتر نامناسب" in STATUS_FA[422]
        # 426 is «سرویس پیشرفته», not a "commercial account" that does not exist
        assert "سرویس پیشرفته" in STATUS_FA[426]
        # status 100 is the 48h reporting window expiring, not a bad id
        assert "۴۸" in DELIVERY_FA[100]

    def test_throttling_stops_the_run_like_an_empty_balance_does(self):
        import inspect
        from app.services import sms_service as sms
        src = inspect.getsource(sms.send_bulk)
        assert "in (418, 429, 451)" in src
