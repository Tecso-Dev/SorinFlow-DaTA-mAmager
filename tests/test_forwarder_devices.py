"""
Per-device forwarder authentication.

One OTP_INBOUND_SECRET for the whole installation answers only «somebody who
knows the secret», and with more than one operator that is everybody: one
person's phone could answer another person's Divar prompt, and revoking a lost
handset means re-configuring every other one.

These pin the three checks and the one thing that must NOT change — the phone
configured before any of this existed keeps working.
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_fwd.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.services import forwarder as F            # noqa: E402
from app.models.forwarder import ForwarderDevice   # noqa: E402

RAW = b'{"kind":"contact","account":"09058432452","code":"445566"}'


def _dev(secret="s" * 64, user_id=7, active=True, did="abcd1234"):
    d = ForwarderDevice()
    d.device_id, d.secret, d.user_id, d.is_active = did, secret, user_id, active
    d.battery = d.network = d.app_version = None
    d.last_seen_at = d.last_code_at = d.warned_at = None
    d.codes_forwarded = 0
    return d


def _sig(secret, raw=RAW):
    import hashlib, hmac
    return hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


class _DB:
    """Just enough session for resolve_device / owns_divar_account."""
    def __init__(self, devices=(), cookies=()):
        self._devices, self._cookies = list(devices), list(cookies)

    async def execute(self, q):
        text = str(q)
        rows = self._cookies if "cookies" in text else self._devices
        outer = self

        class _R:
            def scalars(self): return self
            def all(self): return rows
            def first(self):
                # resolve_device filters by device_id; emulate it
                want = getattr(outer, "_want_device", None)
                if want is None:
                    return rows[0] if rows else None
                return next((d for d in rows if d.device_id == want), None)
        return _R()


class _Cookie:
    def __init__(self, phone, owner=None):
        self.phone_number, self.owner_user_id = phone, owner


class TestPhoneNumbersAreComparedByDigits:
    """A number reaches us from a SIM, a pasted cookie jar and a form, and
    those three spell it differently."""

    @pytest.mark.parametrize("a,b", [
        ("09058432452", "+989058432452"),
        ("09058432452", "9058432452"),
        ("۰۹۰۵۸۴۳۲۴۵۲".translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")), "09058432452"),
    ])
    def test_the_same_phone_written_differently(self, a, b):
        assert F.same_phone(a, b)

    @pytest.mark.parametrize("a,b", [
        ("09058432452", "09053833026"),
        ("09058432452", ""),
        (None, "09058432452"),
    ])
    def test_different_or_missing_is_not_a_match(self, a, b):
        assert not F.same_phone(a, b)


class TestSignatureVerification:

    def test_a_correct_hmac_passes(self):
        assert F.verify_signature("k" * 64, RAW, _sig("k" * 64), "")

    def test_a_wrong_hmac_fails(self):
        assert not F.verify_signature("k" * 64, RAW, _sig("other"), "")

    def test_the_plain_secret_header_is_accepted(self):
        """A stock forwarder that cannot sign still has to work."""
        assert F.verify_signature("k" * 64, RAW, "", "k" * 64)

    def test_a_wrong_plain_secret_fails(self):
        assert not F.verify_signature("k" * 64, RAW, "", "nope")

    def test_no_secret_configured_never_passes(self):
        assert not F.verify_signature("", RAW, _sig("anything"), "anything")

    def test_a_body_changed_in_transit_fails(self):
        """The signature is over the RAW bytes, so editing the code invalidates it."""
        tampered = RAW.replace(b"445566", b"999999")
        assert not F.verify_signature("k" * 64, tampered, _sig("k" * 64, RAW), "")


class TestWhichDeviceAndWhose:

    @pytest.mark.asyncio
    async def test_a_known_signed_device_authenticates(self):
        d = _dev()
        db = _DB(devices=[d], cookies=[_Cookie("09058432452", owner=7)])
        db._want_device = "abcd1234"
        dev, how = await F.authenticate(
            db, device_id="abcd1234", raw=RAW, signature=_sig("s" * 64),
            plain="", account="09058432452", legacy_secret=None)
        assert dev is d and how == "device"

    @pytest.mark.asyncio
    async def test_an_unknown_device_is_401_not_404(self):
        """A 404 would confirm which device ids exist to an unauthenticated caller."""
        db = _DB(devices=[])
        db._want_device = "nope"
        with pytest.raises(F.ForwarderAuthError) as e:
            await F.authenticate(db, device_id="nope", raw=RAW, signature="x",
                                 plain="", account="09058432452", legacy_secret=None)
        assert e.value.status == 401 and e.value.detail == "bad signature"

    @pytest.mark.asyncio
    async def test_a_disabled_device_is_refused_the_same_way(self):
        d = _dev(active=False)
        db = _DB(devices=[d]); db._want_device = "abcd1234"
        with pytest.raises(F.ForwarderAuthError) as e:
            await F.authenticate(db, device_id="abcd1234", raw=RAW,
                                 signature=_sig("s" * 64), plain="",
                                 account="09058432452", legacy_secret=None)
        assert e.value.status == 401

    @pytest.mark.asyncio
    async def test_one_users_phone_cannot_answer_anothers_account(self):
        """The multi-user version of the bug this whole file exists to stop."""
        d = _dev(user_id=7)
        db = _DB(devices=[d], cookies=[_Cookie("09058432452", owner=99)])
        db._want_device = "abcd1234"
        with pytest.raises(F.ForwarderAuthError) as e:
            await F.authenticate(db, device_id="abcd1234", raw=RAW,
                                 signature=_sig("s" * 64), plain="",
                                 account="09058432452", legacy_secret=None)
        assert e.value.status == 403

    @pytest.mark.asyncio
    async def test_an_unowned_session_is_still_answerable(self):
        """Pre-migration sessions have no owner; refusing them would break an
        install that has not been migrated yet."""
        d = _dev(user_id=7)
        db = _DB(devices=[d], cookies=[_Cookie("09058432452", owner=None)])
        db._want_device = "abcd1234"
        dev, how = await F.authenticate(
            db, device_id="abcd1234", raw=RAW, signature=_sig("s" * 64),
            plain="", account="09058432452", legacy_secret=None)
        assert how == "device"

    @pytest.mark.asyncio
    async def test_an_account_that_exists_for_nobody_is_refused(self):
        d = _dev(user_id=7)
        db = _DB(devices=[d], cookies=[_Cookie("09120000000", owner=7)])
        db._want_device = "abcd1234"
        with pytest.raises(F.ForwarderAuthError) as e:
            await F.authenticate(db, device_id="abcd1234", raw=RAW,
                                 signature=_sig("s" * 64), plain="",
                                 account="09058432452", legacy_secret=None)
        assert e.value.status == 403


class TestTheOldPhoneKeepsWorking:
    """Somebody's phone is configured right now with the global secret. It must
    not stop the day this ships."""

    @pytest.mark.asyncio
    async def test_no_device_id_falls_back_to_the_global_secret(self):
        dev, how = await F.authenticate(
            _DB(), device_id=None, raw=RAW, signature=_sig("global" * 8),
            plain="", account="09058432452", legacy_secret="global" * 8)
        assert dev is None and how == "legacy"

    @pytest.mark.asyncio
    async def test_a_wrong_legacy_secret_is_401(self):
        with pytest.raises(F.ForwarderAuthError) as e:
            await F.authenticate(_DB(), device_id=None, raw=RAW, signature="bad",
                                 plain="", account=None, legacy_secret="global" * 8)
        assert e.value.status == 401

    @pytest.mark.asyncio
    async def test_nothing_configured_at_all_is_503_not_401(self):
        """«feature off» is not «bad credentials», and the app shows them differently."""
        with pytest.raises(F.ForwarderAuthError) as e:
            await F.authenticate(_DB(), device_id=None, raw=RAW, signature="x",
                                 plain="", account=None, legacy_secret=None)
        assert e.value.status == 503


class TestHealthSeparatesOnlineFromDelivering:
    """A phone can heartbeat perfectly and never forward a code — a wrong text
    filter does exactly that. Reporting one as the other sends somebody to
    check their internet when the problem is a rule in the app."""

    def _at(self, **kw):
        return datetime.now(timezone.utc) - timedelta(**kw)

    def test_never_configured(self):
        assert F.health(_dev())["state"] == "never_seen"

    def test_offline_when_the_heartbeat_is_stale(self):
        d = _dev(); d.last_seen_at = self._at(minutes=30)
        h = F.health(d)
        assert h["state"] == "offline" and h["online"] is False

    def test_online_but_silent_is_its_own_state(self):
        d = _dev(); d.last_seen_at = self._at(minutes=1)
        h = F.health(d)
        assert h["state"] == "no_codes_yet" and h["online"] is True

    def test_healthy_once_a_code_has_actually_arrived(self):
        d = _dev(); d.last_seen_at = self._at(minutes=1); d.last_code_at = self._at(hours=2)
        assert F.health(d)["state"] == "ok"

    def test_disabled_outranks_everything(self):
        d = _dev(active=False); d.last_seen_at = self._at(minutes=1)
        assert F.health(d)["state"] == "disabled"

    def test_every_state_has_persian_the_owner_can_act_on(self):
        for d in (_dev(), _dev(active=False)):
            assert F.health(d)["message_fa"].strip()


class TestTheOwnerIsToldWhatToFix:
    """«forwarder offline» tells somebody nothing they can act on. The email
    has to name the thing to check, in the order a person would check it."""

    @pytest.mark.parametrize("state", ["offline", "no_codes_yet"])
    def test_each_failure_names_a_concrete_step(self, state):
        from app.services.forwarder_watch import _fa_diagnosis
        what, how = _fa_diagnosis(state)
        assert what.strip() and how.strip()
        assert "۱)" in how, "no numbered steps to follow"

    def test_offline_talks_about_the_phone_not_the_rules(self):
        from app.services.forwarder_watch import _fa_diagnosis
        _, how = _fa_diagnosis("offline")
        assert "اینترنت" in how and "باتری" in how

    def test_silent_but_online_talks_about_the_rules_not_the_phone(self):
        """This is the case that sends people to check their internet when the
        problem is a text filter."""
        from app.services.forwarder_watch import _fa_diagnosis
        _, how = _fa_diagnosis("no_codes_yet")
        assert "فیلتر متن" in how
        assert "اینترنت" not in how

    def test_rcs_is_mentioned_because_it_is_not_an_sms(self):
        """A Divar message delivered over RCS fires no SMS broadcast at all,
        and nothing in the app can see it."""
        from app.services.forwarder_watch import _fa_diagnosis
        _, how = _fa_diagnosis("no_codes_yet")
        assert "RCS" in how


class TestTheWatchNeverCriesWolf:

    def _dev(self, **kw):
        d = _dev()
        for k, v in kw.items():
            setattr(d, k, v)
        return d

    def test_a_device_that_never_delivered_is_not_an_outage(self):
        """Registered an hour ago and never configured is an unfinished setup,
        not a failure — and the panel already says so."""
        import inspect
        from app.services import forwarder_watch
        src = inspect.getsource(forwarder_watch.sweep)
        assert "not d.last_code_at" in src

    def test_it_waits_before_complaining_about_a_new_device(self):
        import inspect
        from app.services import forwarder_watch
        src = inspect.getsource(forwarder_watch.sweep)
        assert "age < 3600" in src

    def test_one_email_per_outage_not_one_per_check(self):
        import inspect
        from app.services import forwarder_watch
        src = inspect.getsource(forwarder_watch.sweep)
        assert "warned_at" in src and "REWARN_HOURS" in src

    def test_a_working_device_clears_its_warning(self):
        import inspect
        from app.services import forwarder
        src = inspect.getsource(forwarder.note_seen)
        assert "warned_at = None" in src, "the next outage would be silent"

    @pytest.mark.asyncio
    async def test_a_broken_database_does_not_raise(self, monkeypatch):
        from app.services import forwarder_watch

        def boom(*a, **k):
            raise RuntimeError("db gone")
        monkeypatch.setattr(forwarder_watch, "async_session_maker", boom)
        assert await forwarder_watch.sweep() == {"checked": 0, "warned": 0}


class TestTheCodeNeededEmailDiagnosesThePhone:
    """It used to say only «a code is needed» — the one thing the reader can
    already see. What they cannot see is that their handset went offline."""

    def test_it_looks_up_the_device_for_that_account(self):
        import inspect
        from app.scraper.contact_extractor import ContactExtractor
        src = inspect.getsource(ContactExtractor._notify_code_needed)
        assert "same_phone(d.sim_phone, self.account_phone)" in src

    def test_it_says_when_no_phone_is_registered_at_all(self):
        import inspect
        from app.scraper.contact_extractor import ContactExtractor
        src = inspect.getsource(ContactExtractor._notify_code_needed)
        assert "هیچ گوشی‌ای برای این شماره ثبت نشده" in src

    def test_a_healthy_forwarder_points_at_resend_instead(self):
        import inspect
        from app.scraper.contact_extractor import ContactExtractor
        src = inspect.getsource(ContactExtractor._notify_code_needed)
        assert "ارسال دوباره کد" in src

    def test_describing_the_forwarder_cannot_break_the_alert(self):
        import inspect
        from app.scraper.contact_extractor import ContactExtractor
        src = inspect.getsource(ContactExtractor._notify_code_needed)
        blk = src[src.index("fw_line = \"\""):]
        assert "except Exception" in blk[:blk.index("# EVERY admin")]


class TestTheAlertGoesToWhoeverCanAnswerIt:
    """Rotation moves between accounts mid-run and the code goes to whichever
    SIM is now active, so the person who can answer is that account's owner —
    not whoever happens to be an admin. With ten accounts across three people,
    mailing every admin every time is how an alert becomes noise."""

    def _src(self):
        import inspect
        from app.scraper.contact_extractor import ContactExtractor
        return inspect.getsource(ContactExtractor._notify_code_needed)

    def test_it_resolves_the_owner_of_the_active_account(self):
        src = self._src()
        assert "owner_user_id" in src
        assert "same_phone(c.phone_number, self.account_phone)" in src

    def test_the_owner_wins_over_the_admin_list(self):
        src = self._src()
        assert "if owner_to:" in src
        assert src.index("if owner_to:") < src.index("to = recipients[0]")

    def test_admins_remain_the_fallback_for_an_unowned_account(self):
        """Every account was unowned before ownership existed; an alert with
        no recipient is the same as no alert."""
        src = self._src()
        assert "recipients[0] if recipients else None" in src

    def test_resolving_the_owner_cannot_break_the_alert(self):
        src = self._src()
        blk = src[src.index("owner_to = []"):src.index("# EVERY admin")]
        assert "except Exception" in blk


class TestTheSetupGuideIsActuallyUsable:
    """A new user's first click is this endpoint. It 500'd on the live server
    because the payload template is made OF %placeholders% — %text%, %sim%,
    %battery% — and formatting it with % read those as format specifiers."""

    def _config(self):
        """Build the config the route builds, without the HTTP layer."""
        import inspect, re
        from app.api.routes import forwarder as R
        src = inspect.getsource(R.device_config)
        tpl_src = src[src.index("tpl = ("):src.index("headers = {")]
        ns = {"acct": "09058432452"}
        exec(compile(tpl_src.strip(), "<tpl>", "exec"), ns)
        return ns["tpl"]

    def test_the_template_builds_without_a_format_error(self):
        tpl = self._config()
        assert "__KIND__" in tpl, "the substitution token is gone"

    def test_both_kinds_substitute_cleanly(self):
        tpl = self._config()
        for kind in ("contact", "login"):
            out = tpl.replace("__KIND__", kind)
            assert f'"kind":"{kind}"' in out
            assert "__KIND__" not in out

    def test_the_placeholders_the_app_needs_survive(self):
        """These are the app's, not Python's — they must reach the phone intact."""
        tpl = self._config().replace("__KIND__", "contact")
        for ph in ("%text%", "%sim%", "%sentStamp%", "%receivedStamp%",
                   "%battery%", "%network%", "%Regex="):
            assert ph in tpl, f"{ph} was eaten"

    def test_percent_formatting_is_never_used_on_it(self):
        """The bug itself, pinned: `tpl % kind` raises on this string."""
        import inspect
        from app.api.routes import forwarder as R
        src = inspect.getsource(R.device_config)
        assert "tpl %" not in src

    def test_the_filled_template_is_valid_json_once_the_app_substitutes(self):
        import json, re
        out = self._config().replace("__KIND__", "contact")
        out = re.sub(r"%Regex=[^%]*%", "523969", out)
        for ph, v in (("%text%", "Code: 523969"), ("%sim%", "1"),
                      ("%sentStamp%", "1700000000000"), ("%receivedStamp%", "1700000000500"),
                      ("%battery%", "91"), ("%network%", "LTE")):
            out = out.replace(ph, v)
        body = json.loads(out)
        assert body["kind"] == "contact" and body["code"] == "523969"


class TestTheSetupQrCarriesTheRightThing:
    """Eight fields typed into a phone is where setup goes wrong. The QR is
    the same configuration in one scan — which makes what it carries, and
    when it changes, the thing to get right."""

    def _payload_src(self):
        import inspect
        from app.api.routes import forwarder as R
        return inspect.getsource(R.device_config)

    def test_it_is_built_with_urlencode_not_string_concatenation(self):
        """A secret or a phone number in a URL needs escaping; hand-built
        query strings are how a `+` or `&` silently corrupts one."""
        src = self._payload_src()
        assert "urlencode(" in src

    def test_the_scheme_is_the_apps(self):
        assert 'sorinflow://setup?' in self._payload_src()

    @pytest.mark.parametrize("key", ["server", "account", "device", "secret"])
    def test_every_field_the_app_needs_is_present(self, key):
        assert f'"{key}"' in self._payload_src()

    def test_it_carries_the_devices_own_secret_never_the_global_one(self):
        """One QR configures one phone for one user's accounts. The global
        OTP_INBOUND_SECRET would hand over every account at once."""
        src = self._payload_src()
        assert "row.secret" in src
        assert "_inbound_secret" not in src and "otp_inbound_secret" not in src

    def test_the_account_is_digits_only(self):
        """It reaches us from a SIM, a form and a cookie jar, spelled three
        ways; the app matches on digits."""
        assert 'ch.isdigit()' in self._payload_src()

    def test_it_is_computed_per_request_not_stored(self):
        """A stored copy goes stale exactly when it matters — after a rotate,
        which is what somebody does when a handset is lost."""
        from app.models.forwarder import ForwarderDevice
        cols = {c.name for c in ForwarderDevice.__table__.columns}
        assert "setup_payload" not in cols

    def test_rotating_the_secret_changes_what_the_qr_would_carry(self):
        """The property that makes re-scanning work."""
        from app.models.forwarder import new_secret
        a, b = new_secret(), new_secret()
        assert a != b and len(a) == 64

    def test_the_panel_renders_it_client_side(self):
        """The guide is fetched with a bearer token via apiCall, and an
        <img src> cannot send one — so there is no server-side image route."""
        js = Path("frontend/js/app.js").read_text(encoding="utf-8")
        assert "new QRCode(" in js and "_fwSetupPayload" in js
        api = Path("app/api/routes/forwarder.py").read_text(encoding="utf-8")
        assert "qr.png" not in api and "image/png" not in api

    def test_it_degrades_to_the_link_when_the_library_is_missing(self):
        js = Path("frontend/js/app.js").read_text(encoding="utf-8")
        blk = js[js.index("const qr = document.getElementById('fw-setup-qrcode')"):]
        assert "typeof QRCode !== 'undefined'" in blk[:600]
        assert "else {" in blk[:900], "no fallback; the phone could not be set up at all"

    def test_the_manual_fields_survive_as_the_fallback(self):
        """The generic upstream SMS Forwarder app cannot scan this."""
        js = Path("frontend/js/app.js").read_text(encoding="utf-8")
        assert "_fwCopyRow(" in js
        assert "برنامهٔ عمومی SMS Forwarder" in js

    def test_a_rotate_re_renders_the_guide(self):
        js = Path("frontend/js/app.js").read_text(encoding="utf-8")
        fn = js[js.index("async function fwRotate"):]
        fn = fn[:fn.index("\nasync function")]
        assert "fwGuide(id)" in fn, "the QR on screen would be one the server no longer accepts"


class TestPersianIsNotRenderedInMonospace:
    """«اطلاعات تماس» came out as «ا ط ل ا ع ا ت» — disconnected letters — in
    the one field the reader has to copy exactly. Monospace faces carry no
    Arabic shaping."""

    def test_the_field_defaults_to_the_panels_face(self):
        css = Path("frontend/css/style.css").read_text(encoding="utf-8")
        blk = css[css.index(".fw-copy input {"):css.index(".fw-copy input.is-code")]
        assert "var(--font-fa)" in blk
        assert "--bs-font-monospace" not in blk

    def test_monospace_is_opt_in_for_ascii_values(self):
        css = Path("frontend/css/style.css").read_text(encoding="utf-8")
        assert ".fw-copy input.is-code" in css
        blk = css[css.index(".fw-copy input.is-code"):]
        assert "--bs-font-monospace" in blk[:200]

    def test_the_class_is_chosen_by_the_value_not_the_label(self):
        js = Path("frontend/js/app.js").read_text(encoding="utf-8")
        fn = js[js.index("function _fwCopyRow"):]
        fn = fn[:fn.index("\nfunction ")]
        assert "test(String(value))" in fn
        assert "is-code" in fn

    def test_persian_values_read_right_to_left(self):
        js = Path("frontend/js/app.js").read_text(encoding="utf-8")
        fn = js[js.index("function _fwCopyRow"):]
        fn = fn[:fn.index("\nfunction ")]
        assert "persian ? 'rtl' : 'ltr'" in fn
