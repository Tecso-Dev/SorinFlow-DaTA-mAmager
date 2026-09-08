"""
Proving the number on file is actually yours.

The panel has shown «تأیید نشده» beside a phone since the badge was added, and
there was no way to make it go away from inside the panel — the only route was
an UPDATE against the database. This is the missing half.

The rule that matters, and the reason most of these tests exist: a phone is
verified by answering the phone. verification.py already warns about this in
verify_code's own docstring — "a code read out of an inbox proves the address
and says nothing about the number it was nominally sent to... how a phone
nobody had ever answered ended up flagged verified in the panel".
"""
import ast
import re
from pathlib import Path

import pytest

USERS = Path("app/api/routes/users.py")
APP_JS = Path("frontend/js/app.js")
INDEX = Path("frontend/index.html")


def _code_only(path):
    src = path.read_text(encoding="utf-8")
    if path.suffix == ".py":
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef, ast.Module)):
                if (node.body and isinstance(node.body[0], ast.Expr)
                        and isinstance(node.body[0].value, ast.Constant)
                        and isinstance(node.body[0].value.value, str)):
                    node.body.pop(0)
        return ast.unparse(tree)
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"//[^\n]*", "", src)


def _fn(name):
    return _code_only(USERS).split(f"async def {name}")[1].split("\nasync def ")[0]


class TestOnlyTheSmsRouteVerifiesAPhone:
    """The whole point. An emailed code proves an inbox."""

    def test_the_code_is_requested_over_sms_explicitly(self):
        fn = _fn("request_phone_code")
        assert "channel='sms'" in fn or 'channel="sms"' in fn, (
            "the code is sent on the automatic channel, which falls back to "
            "email — and an emailed code cannot verify a phone")

    def test_the_delivered_channel_is_checked_before_trusting_the_code(self):
        """verify_code returns the route the code actually travelled. Ignoring
        it is exactly the bug its docstring describes."""
        fn = _fn("confirm_phone_code")
        assert "verify_code" in fn
        assert "sms" in fn, "the returned channel is never compared to sms"
        assert "phone_verified = True" in fn

    def test_verification_is_not_granted_before_the_channel_check(self):
        fn = _fn("confirm_phone_code")
        i_check = fn.find("!= 'sms'")
        if i_check == -1:
            i_check = fn.find('!= "sms"')
        i_set = fn.find("phone_verified = True")
        assert i_check != -1, "no channel check at all"
        assert i_check < i_set, "the flag is set before the channel is checked"


class TestYouCanOnlyVerifyYourOwn:

    def test_both_endpoints_act_on_the_caller(self):
        """No user id in the path: an admin marking somebody else's number
        verified would be asserting something they cannot know."""
        src = USERS.read_text(encoding="utf-8")
        for path in ("/me/phone/request", "/me/phone/verify"):
            assert f'"{path}"' in src
        for name in ("request_phone_code", "confirm_phone_code"):
            fn = _fn(name)
            assert "current_user" in fn
            assert "user_id" not in fn

    def test_a_number_already_on_another_account_is_refused(self):
        """User.phone is unique, so an unguarded clash is a 500 at flush."""
        fn = _fn("request_phone_code")
        assert "409" in fn


class TestChangingTheNumberResetsTheClaim:
    """An unverified number nobody can replace is a dead end — the code would
    go to whoever owns the mistyped number."""

    def test_a_new_number_can_be_supplied(self):
        fn = _fn("request_phone_code")
        assert "normalize_mobile" in fn, "the number is not normalised"

    def test_changing_it_clears_the_verified_flag(self):
        fn = _fn("request_phone_code")
        assert "phone_verified = False" in fn, (
            "a changed number keeps the old number's verification")


class TestItDoesNotClaimToHaveSentWhatItDidNot:

    def test_a_failed_delivery_is_not_reported_as_sent(self):
        """«ارسال شد» over a message that never sent leaves somebody waiting
        for an SMS that is not coming."""
        fn = _fn("request_phone_code")
        assert "503" in fn and "channel" in fn

    def test_throttling_surfaces_as_429(self):
        fn = _fn("request_phone_code")
        assert "429" in fn


class TestThePanelCanActuallyReachIt:

    def test_the_functions_exist_and_are_called_from_the_markup(self):
        js = _code_only(APP_JS)
        html = INDEX.read_text(encoding="utf-8")
        for f in ("loadPhoneState", "requestPhoneCode", "confirmPhoneCode"):
            assert f"function {f}" in js, f"{f} is not defined"
        assert "requestPhoneCode()" in html and "confirmPhoneCode()" in html

    def test_the_state_loads_when_the_modal_opens(self):
        js = _code_only(APP_JS)
        assert "loadPhoneState()" in js.split("function open2FAModal")[1][:800]

    def test_the_code_field_accepts_persian_digits(self):
        """Every other code field in this panel does; one that does not
        rejects a correctly-typed code and blames the code."""
        js = _code_only(APP_JS)
        assert "_wireOtp('phone-code'" in js

    def test_the_field_length_matches_the_code_the_server_makes(self):
        from app.config import get_settings
        n = max(4, min(8, get_settings().auth_code_length))
        html = INDEX.read_text(encoding="utf-8")
        m = re.search(r'<input[^>]*id="phone-code"[^>]*?maxlength="(\d+)"', html, re.S)
        assert m and int(m.group(1)) == n
