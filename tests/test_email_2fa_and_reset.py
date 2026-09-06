"""
The email second factor and the self-service password reset.

Four bugs, all found before the change shipped, all of which would have hurt
the one person the feature exists for — the owner, locked out, with the
database as the only way back.

Each of these is asserted on executable code rather than on a comment: several
tests in this repository have been fooled by matching prose that described the
opposite of what the code did.
"""
import ast
import re
from pathlib import Path

USERS = Path("app/api/routes/users.py")
MAIN = Path("app/main.py")
APP_JS = Path("frontend/js/app.js")


def _code_only(path):
    """Source with comments and docstrings removed."""
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
    # NOT re.S on the line-comment arm: with DOTALL, `//.*` swallows the
    # rest of the file from the first comment onward.
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"//[^\n]*", "", src)


class TestEveryNameUsedIsBound:
    """`logger` was called on the line AFTER the new password was committed.
    The password changed, the handler raised NameError, the panel reported
    «کد نادرست است», and the account's password had silently rotated."""

    def test_logger_is_imported_where_it_is_used(self):
        src = USERS.read_text(encoding="utf-8")
        if "logger." not in _code_only(USERS):
            return  # nothing uses it; nothing to import
        assert re.search(r"^from loguru import logger$", src, re.M), \
            "users.py calls logger.* but never imports it"

    def test_no_undefined_globals_in_users_module(self):
        """The general form of the same bug."""
        tree = ast.parse(USERS.read_text(encoding="utf-8"))
        bound = set(dir(__builtins__)) | {"__name__", "__file__", "__doc__"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                bound |= {(a.asname or a.name.split(".")[0]) for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                bound |= {(a.asname or a.name) for a in node.names}
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bound.add(node.name)
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        bound.add(t.id)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                bound.add(node.target.id)
        import builtins
        bound |= set(dir(builtins))
        assert "logger" in bound, "logger is used but not bound at module level"


class TestTheStrongerFactorWins:
    """Email 2FA ran BEFORE TOTP, so switching it on silently retired the
    authenticator. Combined with the new email password reset, that made read
    access to the mailbox equal to root on the panel."""

    def test_email_branch_defers_to_a_configured_authenticator(self):
        code = _code_only(USERS)
        m = re.search(r"if \(?getattr\(user, [\"']email_2fa_enabled[\"'].*?:",
                      code, re.S)
        assert m, "the email-2FA login branch is gone or was rewritten"
        assert "totp_enabled" in m.group(0), (
            "the email second factor does not check totp_enabled, so it "
            "overrides an authenticator instead of deferring to it")

    def test_totp_branch_is_still_reachable(self):
        code = _code_only(USERS)
        assert "user.totp_enabled and user.totp_secret" in code


class TestPublicLoginPathsReachTheirRoutes:
    """The API-key middleware is an EXACT-match allowlist and API_KEY is empty
    locally and set in production, so a missing entry passes every local test
    and 401s the live panel. The endpoints omitted were the ones needed to get
    back IN, while the toggle that locks you out worked fine."""

    UNAUTHENTICATED = [
        "/api/users/token",
        "/api/users/token/verify-totp",
        "/api/users/token/verify-email",
        "/api/users/password-reset/request",
        "/api/users/password-reset/confirm",
    ]

    def test_every_unauthenticated_endpoint_is_allowlisted(self):
        code = _code_only(MAIN)
        missing = [p for p in self.UNAUTHENTICATED
                   if f'"{p}"' not in code and f"'{p}'" not in code]
        assert not missing, (
            f"these are called with no Authorization header and would 401 in "
            f"production, where API_KEY is set: {missing}")

    def test_the_frontend_only_calls_allowlisted_paths_before_login(self):
        """Whatever the login screen fetches must be in that set — it has no
        token to send."""
        js = _code_only(APP_JS)
        called = set(re.findall(r"['\"`](/api/users/(?:token[^'\"`]*|password-reset/[^'\"`]+))['\"`]", js))
        code = _code_only(MAIN)
        for path in called:
            assert f'"{path}"' in code or f"'{path}'" in code, \
                f"{path} is fetched pre-login but not allowlisted"


class TestBackToLoginFullyResetsTheForm:
    """The reset form's step is read off reset-code-wrap, but the help text and
    the button label were left describing step 2 — a form asking for a code
    with no field to type it into, whose button re-sends a code."""

    def test_it_restores_both_labels_it_can_change(self):
        js = _code_only(APP_JS)
        fn = js.split("function backToLogin")[1].split("\nfunction ")[0]
        assert "reset-help" in fn, "backToLogin leaves the step-2 help text up"
        assert "reset-btn-label" in fn, "backToLogin leaves the step-2 button label up"

    def test_it_clears_both_pending_sessions(self):
        js = _code_only(APP_JS)
        fn = js.split("function backToLogin")[1].split("\nfunction ")[0]
        assert "_totpSession = null" in fn and "_emailSession = null" in fn


class TestEveryCodeFieldTakesPersianDigits:
    """This panel is Persian and most of its users type on a Persian keyboard.
    An unwired field sends ۱۲۳۴۵, the server rejects it, and the message says
    the code is wrong — which points the user at the code rather than at the
    keyboard. _wireOtp already existed; the new fields simply were not given it."""

    CODE_INPUTS = ["login-totp-code", "login-email-code", "reset-code"]

    def test_every_code_input_is_wired(self):
        js = _code_only(APP_JS)
        missing = [i for i in self.CODE_INPUTS if f"_wireOtp('{i}'" not in js]
        assert not missing, f"code fields with no Persian-digit handling: {missing}"

    def test_the_field_length_matches_the_code_the_server_makes(self):
        """maxlength drives the auto-submit (`value.length === maxLength`), so a
        field one digit too long simply never fires it."""
        from app.config import get_settings
        n = max(4, min(8, get_settings().auth_code_length))
        html = Path("frontend/index.html").read_text(encoding="utf-8")
        for eid in ("login-email-code", "reset-code"):
            m = re.search(r'<input[^>]*id="%s"[^>]*?maxlength="(\d+)"' % eid, html, re.S)
            assert m, f"{eid} has no maxlength"
            assert int(m.group(1)) == n, (
                f"{eid} accepts {m.group(1)} digits but generate_code() makes {n}")


class TestShowLoginPageResetsEveryStep:
    """backToLogin was fixed for this; showLoginPage — the path a logout takes —
    was not, so it hid step 2 and the register form but left the email and reset
    steps visible, stacking two forms on the login page."""

    def test_it_hides_all_the_non_default_steps(self):
        js = _code_only(APP_JS)
        fn = js.split("function showLoginPage")[1].split("\nfunction ")[0]
        for step in ("login-step-2", "login-step-register",
                     "login-step-email", "login-step-reset"):
            assert step in fn, f"showLoginPage leaves {step} on screen"

    def test_it_drops_both_pending_sessions(self):
        js = _code_only(APP_JS)
        fn = js.split("function showLoginPage")[1].split("\nfunction ")[0]
        assert "_totpSession = null" in fn and "_emailSession = null" in fn
