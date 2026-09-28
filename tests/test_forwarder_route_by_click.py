"""
A code labelled with the wrong number, from a dual-SIM phone (issue #37).

The server used to trust the one number the forwarder app puts on a code. On a
dual-SIM phone that label can be wrong: three codes for the number in slot 2
arrived labelled with slot 1's number, each four to seven seconds after the
scraper clicked «اطلاعات تماس» on slot 2's number — and slot 1 had not been
clicked at all. A code nothing was waiting for was held for two minutes under
that wrong label, and the next challenge on slot 1 took it and typed it into
Divar, burning the attempt. Version 3.2.0 of the app, when the phone does not
say which slot an SMS came in on, posts the same SMS twice, once per label.

So the scraper now notes the moment it clicks, and a code goes to the number
the phone's owner just clicked when its label names a number that was not
clicked at all. Never to a number that belongs to anybody else. Every SMS is
used once, and a held code cannot be taken by a challenge that started after
it arrived: a code cannot answer a click that had not happened yet.

The numbers below are made up.
"""
import hashlib
import hmac
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_route_click.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.api.routes import scraper as R          # noqa: E402
from app.models.forwarder import ForwarderDevice  # noqa: E402
from app.scraper import otp_store                # noqa: E402
from _fake_redis import patch_redis              # noqa: E402

LEGACY_SECRET = "legacy-secret-please-ignore"
DEVICE_SECRET = "d" * 64

OWNER, COLLEAGUE = 7, 99
A = "09120000001"   # SIM 1 of the owner's phone
B = "09120000002"   # SIM 2 of the same phone
C = "09120000003"   # a third number of the same owner, not in this phone
X = "09120000009"   # a colleague's number
U = "09120000005"   # a Divar session nobody owns


def d10(p):
    return otp_store._digits(p)


def now_ms():
    return int(time.time() * 1000)


class _Cookie:
    def __init__(self, phone, owner):
        self.phone_number, self.owner_user_id = phone, owner


def _device():
    d = ForwarderDevice()
    d.id, d.device_id, d.secret, d.user_id, d.is_active = 1, "dev00001", DEVICE_SECRET, OWNER, True
    d.sim_phone, d.sim_phone2, d.label = A, B, "phone"
    d.battery = d.network = d.app_version = None
    d.last_seen_at = d.last_code_at = d.warned_at = None
    d.codes_forwarded = 0
    return d


class _DB:
    """Just enough session for device authentication and the owner's numbers."""

    def __init__(self, device, cookies):
        self.device, self.cookies = device, cookies

    async def execute(self, q):
        text = str(q)
        rows = self.cookies if "cookies" in text else [self.device]

        class _R:
            def scalars(self):
                return self

            def all(self):
                return rows

            def first(self):
                return rows[0] if rows else None
        return _R()

    async def commit(self):
        pass

    async def rollback(self):
        pass


class _Req:
    def __init__(self, raw: bytes, headers):
        self._raw, self.headers = raw, headers

        class _C:
            host = "10.0.0.9"
        self.client = _C()

    async def body(self):
        return self._raw


@pytest.fixture(autouse=True)
def wired(monkeypatch):
    patch_redis(monkeypatch, otp_store)
    monkeypatch.setattr(R.settings, "otp_inbound_secret", LEGACY_SECRET, raising=False)

    async def _no_rl(request, limit=20):
        return None
    monkeypatch.setattr(R, "_forwarder_rate_limit", _no_rl)
    from app.services import sms_log
    recorded = []

    async def _rec(*a, **k):
        recorded.append((a, k))
        return True
    monkeypatch.setattr(sms_log, "record", _rec)
    return recorded


@pytest.fixture
def db():
    return _DB(_device(), [
        _Cookie(A, OWNER), _Cookie(B, OWNER), _Cookie(C, OWNER),
        _Cookie(X, COLLEAGUE), _Cookie(U, None),
    ])


def _contact(label, code="523969", stamp=None, kind="contact"):
    s = stamp or now_ms()
    text = ("کد امنیتی دریافت اطلاعات تماس دیوار:\nCode: " if kind == "contact"
            else "کد تایید دیوار: ") + code
    return {"kind": kind, "account": label, "code": code, "text": text,
            "sentStamp": s, "receivedStamp": s}


async def _from_phone(db, body):
    raw = json.dumps(body).encode("utf-8")
    sig = hmac.new(DEVICE_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return await R.otp_inbound(_Req(raw, {"X-Forwarder-Id": "dev00001", "X-Signature": sig}), db=db)


async def _from_legacy(body):
    raw = json.dumps(body).encode("utf-8")
    sig = hmac.new(LEGACY_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return await R.otp_inbound(_Req(raw, {"X-Signature": sig}), db=None)


async def _challenge(account, key, clicked_ms_ago=0):
    """What the scraper does: click «اطلاعات تماس», then open the prompt."""
    await otp_store.note_click(account, "contact", opens=True, at_ms=now_ms() - clicked_ms_ago)
    return await otp_store.request(key, account)


# ── the three reproductions ──────────────────────────────────────────────────

class TestTheWrongLabel:

    async def test_a_code_labelled_A_after_a_click_on_B_goes_to_B(self, db, wired):
        """Run 44: slot 2 was clicked, slot 1 was not, the code said slot 1."""
        assert await _challenge(B, "job:b1", clicked_ms_ago=4000) is False
        out = await _from_phone(db, _contact(A))
        assert out["matched"] is True, out
        assert await otp_store.wait_code("job:b1", 1) is True, "B's prompt never woke"
        assert await otp_store.pop_code("job:b1") == "523969"
        # and nothing was left behind for A's next challenge to take
        assert await _challenge(A, "job:a1") is False
        details = wired[-1][1]
        assert details["account"] == d10(B) and details["labeled"] == d10(A), details
        assert details["reason"] == "matched" and details["rerouted"] is True

    async def test_a_held_code_with_the_wrong_label_is_not_taken_by_As_next_challenge(self, db):
        """The code beat the browser, as it usually does, so nothing was waiting
        yet. It is held for B — and A's next challenge must come away empty."""
        await otp_store.note_click(B, "contact", opens=True, at_ms=now_ms() - 3000)
        out = await _from_phone(db, _contact(A))
        assert out["reason"] == "parked_early", out
        assert await _challenge(A, "job:a1") is False, "A took B's code"
        assert await otp_store.request("job:b1", B) is True, "B lost its code"
        assert await otp_store.pop_code("job:b1") == "523969"

    async def test_a_code_held_under_the_wrong_label_cannot_answer_a_later_click(self, db):
        """When nothing could say where it belonged — a worker that does not
        note clicks yet — the code is held under its label as before. A code
        cannot answer a click that had not happened when it arrived, so A's
        next challenge still leaves it alone."""
        await otp_store.park_early_code(A, "523969", at_ms=now_ms() - 5000)
        assert await _challenge(A, "job:a1") is False, "a code from before the click was typed"

    async def test_a_code_that_arrived_after_the_click_is_still_taken(self, db):
        """The normal order: click, SMS, code parked, then the prompt opens."""
        await otp_store.note_click(A, "contact", opens=True, at_ms=now_ms() - 6000)
        out = await _from_phone(db, _contact(A))
        assert out["reason"] == "parked_early"
        assert await otp_store.request("job:a1", A) is True
        assert await otp_store.pop_code("job:a1") == "523969"


class TestOneSmsTwoCopies:
    """3.2.0 posts the same SMS once per label when the phone does not say
    which slot it came in on. One SMS answers one prompt."""

    async def test_two_copies_labelled_A_and_B_are_used_once(self, db):
        stamp = now_ms()
        assert await otp_store.request("job:b1", B) is False           # B is waiting
        first = await _from_phone(db, _contact(A, stamp=stamp))       # nothing clicked: held under A
        second = await _from_phone(db, _contact(B, stamp=stamp))
        assert second["matched"] is True, second
        assert first["reason"] == "parked_early", first
        assert await otp_store.request("job:a1", A) is False, \
            "the copy held under A outlived the one B used"

    async def test_the_second_copy_is_discarded_once_the_first_is_used(self, db):
        stamp = now_ms()
        assert await otp_store.request("job:b1", B) is False
        first = await _from_phone(db, _contact(B, stamp=stamp))
        second = await _from_phone(db, _contact(A, stamp=stamp))
        assert first["matched"] is True
        assert second["matched"] is False and second["reason"] == "duplicate", second
        assert await otp_store.request("job:a1", A) is False

    async def test_both_copies_routed_to_B_are_one_code(self, db):
        stamp = now_ms()
        await otp_store.note_click(B, "contact", opens=True, at_ms=now_ms() - 3000)
        assert (await _from_phone(db, _contact(A, stamp=stamp)))["reason"] == "parked_early"
        assert (await _from_phone(db, _contact(B, stamp=stamp)))["reason"] == "duplicate"

    async def test_the_same_post_twice_is_one_code(self, db):
        """The app has sent one SMS as two POSTs 27 ms apart."""
        stamp = now_ms()
        assert await otp_store.request("job:a1", A) is False
        assert (await _from_phone(db, _contact(A, stamp=stamp)))["matched"] is True
        assert (await _from_phone(db, _contact(A, stamp=stamp)))["reason"] == "duplicate"

    async def test_a_different_sms_is_not_a_copy(self, db):
        """A resend is a new SMS with its own stamp, even if the code repeats."""
        await otp_store.request("job:a1", A)
        await _from_phone(db, _contact(A, stamp=now_ms() - 1000))
        await otp_store.pop_code("job:a1")
        await otp_store.request("job:a2", A)
        assert (await _from_phone(db, _contact(A, stamp=now_ms())))["matched"] is True


class TestWhichClickWins:

    async def test_two_clicks_close_together_leave_it_to_the_label(self, db):
        await otp_store.note_click(B, "contact", opens=True, at_ms=now_ms() - 4000)
        await otp_store.note_click(C, "contact", opens=True, at_ms=now_ms() - 2000)
        out = await _from_phone(db, _contact(A))
        assert out["reason"] == "parked_early"
        await otp_store.note_click(A, "contact", opens=True, at_ms=now_ms() - 60000)
        assert await otp_store.request("job:a1", A) is True, "held under its label"

    async def test_the_later_of_two_clicks_far_apart_wins(self, db, wired):
        await otp_store.note_click(B, "contact", opens=True, at_ms=now_ms() - 20000)
        await otp_store.note_click(C, "contact", opens=True, at_ms=now_ms() - 3000)
        await _from_phone(db, _contact(A))
        assert wired[-1][1]["account"] == d10(C)

    async def test_the_label_stands_when_its_own_number_was_clicked_too(self, db, wired):
        """A correct label is never second-guessed: A was clicked, so a code
        that says A may simply have been slow."""
        await otp_store.note_click(A, "contact", opens=True, at_ms=now_ms() - 20000)
        await otp_store.note_click(B, "contact", opens=True, at_ms=now_ms() - 3000)
        await _from_phone(db, _contact(A))
        assert wired[-1][1]["account"] == d10(A)
        assert wired[-1][1].get("labeled") is None

    async def test_no_click_leaves_it_to_the_label(self, db, wired):
        await _from_phone(db, _contact(A))
        assert wired[-1][1]["account"] == d10(A)

    async def test_a_click_older_than_the_window_does_not_count(self, db, wired):
        await otp_store.note_click(B, "contact", opens=True,
                                   at_ms=now_ms() - (otp_store.CLICK_WINDOW + 5) * 1000)
        await _from_phone(db, _contact(A))
        assert wired[-1][1]["account"] == d10(A)


class TestNeverSomebodyElses:

    async def test_a_colleagues_click_never_takes_the_code(self, db, wired):
        assert await _challenge(X, "job:x1", clicked_ms_ago=3000) is False
        out = await _from_phone(db, _contact(A))
        assert out["matched"] is False
        assert await otp_store.wait_code("job:x1", 1) is False, "the colleague's prompt got the code"
        assert wired[-1][1]["account"] == d10(A)

    async def test_nor_a_number_nobody_owns(self, db, wired):
        """A reroute only lands on the phone owner's own numbers."""
        await otp_store.note_click(U, "contact", opens=True, at_ms=now_ms() - 3000)
        await _from_phone(db, _contact(A))
        assert wired[-1][1]["account"] == d10(A)

    async def test_the_colleagues_click_is_not_a_tie_either(self, db, wired):
        await otp_store.note_click(B, "contact", opens=True, at_ms=now_ms() - 4000)
        await otp_store.note_click(X, "contact", opens=True, at_ms=now_ms() - 3000)
        await _from_phone(db, _contact(A))
        assert wired[-1][1]["account"] == d10(B)

    async def test_the_old_path_without_a_device_keeps_the_label(self, wired):
        await otp_store.note_click(B, "contact", opens=True, at_ms=now_ms() - 3000)
        await _from_legacy(_contact(A))
        assert wired[-1][1]["account"] == d10(A)


class TestLoginCodes:
    """«افزودن شماره» in the panel: the login code goes the same way."""

    async def test_a_login_code_labelled_A_after_a_login_on_B_goes_to_B(self, db, wired):
        await otp_store.note_click(B, "login", opens=True, at_ms=now_ms() - 3000)
        out = await _from_phone(db, _contact(A, code="445566", kind="login"))
        assert out["reason"] == "parked_for_login"
        assert await otp_store.take_login_code(A) is None
        assert await otp_store.take_login_code(B) == "445566"
        assert wired[-1][1]["labeled"] == d10(A)

    async def test_a_contact_click_does_not_route_a_login_code(self, db):
        await otp_store.note_click(B, "contact", opens=True, at_ms=now_ms() - 3000)
        await _from_phone(db, _contact(A, code="445566", kind="login"))
        assert await otp_store.take_login_code(A) == "445566"

    async def test_a_login_code_from_before_the_login_started_is_not_used(self, db):
        await otp_store.put_login_code(A, "445566", at_ms=now_ms() - 5000)
        await otp_store.note_click(A, "login", opens=True)
        assert await otp_store.take_login_code(A) is None

    async def test_one_login_sms_is_used_once(self, db):
        stamp = now_ms()
        await _from_phone(db, _contact(A, code="445566", stamp=stamp, kind="login"))
        await _from_phone(db, _contact(B, code="445566", stamp=stamp, kind="login"))
        assert await otp_store.take_login_code(B) == "445566"
        assert await otp_store.take_login_code(A) is None, "the other copy outlived it"

    async def test_a_colleagues_login_never_takes_the_code(self, db):
        await otp_store.note_click(X, "login", opens=True, at_ms=now_ms() - 3000)
        await _from_phone(db, _contact(A, code="445566", kind="login"))
        assert await otp_store.take_login_code(X) is None
        assert await otp_store.take_login_code(A) == "445566"


# ── where the clicks are noted ───────────────────────────────────────────────

class _Stop(BaseException):
    """Ends the flow right at the click; the rest of it is not under test."""


class _Btn:
    def __init__(self, text="", on_click=None, enabled=True):
        self.text, self.on_click, self.enabled = text, on_click, enabled

    async def is_visible(self):
        return True

    async def is_enabled(self):
        return self.enabled

    async def inner_text(self):
        return self.text

    async def scroll_into_view_if_needed(self):
        pass

    async def click(self, *a, **k):
        await self.on_click()


async def _clicks(kind="contact"):
    return await otp_store.recent_clicks(kind, now_ms() + 1000)


class TestTheScraperNotesItsClicks:

    async def test_the_contact_click_is_noted_before_it_happens(self, monkeypatch):
        from app.scraper import contact_extractor as ce
        seen = {}

        async def _at_click():
            seen["clicks"] = await _clicks()
            raise _Stop()

        class _Page:
            async def query_selector(self, sel):
                return _Btn(on_click=_at_click)

        async def _nap(*a, **k):
            return None
        monkeypatch.setattr(ce.asyncio, "sleep", _nap)
        e = ce.ContactExtractor.__new__(ce.ContactExtractor)
        e.page, e.account_phone = _Page(), B
        with pytest.raises(_Stop):
            await e.get_phone_number()
        assert d10(B) in seen["clicks"], "the click was not noted before Divar could text"

    async def test_a_resend_is_noted_too(self, monkeypatch):
        from app.scraper import contact_extractor as ce
        clicked = []

        async def _at_click():
            clicked.append(True)

        class _Page:
            async def query_selector_all(self, sel):
                return [_Btn("ارسال مجدد کد", on_click=_at_click)]

        async def _nap(*a, **k):
            return None
        monkeypatch.setattr(ce.asyncio, "sleep", _nap)
        e = ce.ContactExtractor.__new__(ce.ContactExtractor)
        e.page, e.account_phone = _Page(), B
        assert await e._request_otp_resend() is True and clicked
        assert d10(B) in await _clicks()

    async def test_a_countdown_is_not_a_click(self, monkeypatch):
        from app.scraper import contact_extractor as ce

        class _Page:
            async def query_selector_all(self, sel):
                return [_Btn("ارسال مجدد کد", enabled=False)]
        e = ce.ContactExtractor.__new__(ce.ContactExtractor)
        e.page, e.account_phone = _Page(), B
        assert await e._request_otp_resend() is False
        assert await _clicks() == {}

    async def test_the_login_confirm_is_noted_as_a_login_click(self, monkeypatch):
        from app.scraper import auth as divar_auth
        seen = {}

        async def _at_click():
            seen["login"] = await _clicks("login")
            seen["contact"] = await _clicks("contact")
            raise _Stop()

        class _Input:
            async def fill(self, v):
                pass

            async def type(self, ch, delay=0):
                pass

        class _Page:
            url = "https://divar.ir/login"

            async def goto(self, *a, **k):
                pass

            async def content(self):
                return ""

            async def screenshot(self, *a, **k):
                pass

            async def wait_for_selector(self, sel, timeout=0):
                return _Input()

            async def query_selector_all(self, sel):
                if sel == "button":
                    return [_Btn("بعدی", on_click=_at_click)]
                return []

            async def query_selector(self, sel):
                return None

        async def _nap(*a, **k):
            return None
        monkeypatch.setattr(divar_auth.asyncio, "sleep", _nap)
        a = divar_auth.DivarAuth.__new__(divar_auth.DivarAuth)
        a.page = _Page()

        class _Stealth:
            typing_delay = 0
        a.stealth_config = _Stealth()
        with pytest.raises(_Stop):
            await a.login_with_phone(B)
        assert d10(B) in seen["login"] and d10(B) not in seen["contact"]

