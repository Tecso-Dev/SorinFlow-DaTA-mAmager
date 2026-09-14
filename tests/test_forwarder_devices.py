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
