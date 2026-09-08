"""
One-time codes must go out on the template channel.

Kavenegar's verify/lookup carries no sender of its own. `send_verify` was
written for exactly that reason — its docstring says templates "do not need an
approved sender line" — and then nothing ever called it. Every login code took
the plain-send path instead, which does need a sender, and on an account whose
only line is international and shared every one of them came back

    [412] شماره فرستنده نامعتبر است

pointing the reader at a sender field that was filled in correctly.
"""
import ast
import inspect
import re

import pytest


def _code_only(mod):
    """Module source with comments and docstrings stripped — several tests in
    this repo have been fooled by prose describing the opposite of the code."""
    tree = ast.parse(inspect.getsource(mod))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef, ast.Module)):
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                node.body.pop(0)
    return ast.unparse(tree)


class TestTheTemplateChannelIsActuallyWired:

    def test_send_verify_has_a_caller(self):
        """It had none. A helper nothing calls is not a feature."""
        from app.services import verification
        assert "send_verify" in _code_only(verification), (
            "verification.py does not call send_verify, so one-time codes "
            "still go out on the plain-send path")

    def test_the_template_is_preferred_over_plain_send(self):
        from app.services import verification
        src = _code_only(verification)
        i_tpl = src.find("send_verify")
        i_plain = src.find("send_sms(phone")
        assert i_tpl != -1 and i_plain != -1
        assert i_tpl < i_plain, "plain send is tried before the template"

    def test_verify_is_given_the_raw_code_not_the_rendered_message(self):
        """verify/lookup takes a token that it substitutes into the approved
        template. Handing it the whole sentence would send the sentence."""
        from app.services import verification
        src = _code_only(verification)
        m = re.search(r"send_verify\(([^)]*)\)", src)
        assert m, "no send_verify call found"
        args = [a.strip() for a in m.group(1).split(",")]
        assert args[1] == "code", f"send_verify's token argument is {args[1]!r}, not the raw code"

    def test_resolve_otp_template_exists_and_falls_back_to_empty(self):
        from app.services.sms_service import resolve_otp_template
        assert inspect.iscoroutinefunction(resolve_otp_template)

    @pytest.mark.asyncio
    async def test_no_template_configured_is_empty_not_a_crash(self):
        """db=None is the plumbing path and must not query."""
        from app.services.sms_service import resolve_otp_template
        assert await resolve_otp_template(None) == ""


class TestThe412SaysWhatIsActuallyWrong:
    """Kavenegar's own wording is «شماره فرستنده نامعتبر است», which sends the
    reader to a field that was already correct. The same status also means the
    line cannot address the destination."""

    def test_it_mentions_the_international_line(self):
        from app.services.sms_service import STATUS_FA
        msg = STATUS_FA[412]
        assert "بین‌المللی" in msg or "اشتراکی" in msg

    def test_it_names_the_way_out(self):
        from app.services.sms_service import STATUS_FA
        assert "الگو" in STATUS_FA[412]


class TestATemplateCannotBecomeASinglePointOfFailure:
    """A Kavenegar template is a live dependency on someone else's review
    queue — it sits «در حال بررسی» before approval and can be rejected or
    withdrawn afterwards. Preferring it is right; depending on it is not."""

    def test_a_failing_template_falls_back_to_plain_send(self):
        from app.services import verification
        src = _code_only(verification)
        fn = src.split("async def _sms")[1].split("async def _email")[0]
        assert "send_verify" in fn and "send_sms" in fn, "one of the two legs is gone"
        assert "try" in fn, "the template call is not guarded"

    def test_the_fallback_is_reached_on_an_unsuccessful_result_too(self):
        """Not only on an exception: send_verify returns a dict and a failed
        send is a falsy 'success', not a raise."""
        from app.services import verification
        src = _code_only(verification)
        fn = src.split("async def _sms")[1].split("async def _email")[0]
        assert "success" in fn, "the fallback only triggers on an exception"


class TestTheServiceWorkerIsReachable:
    """A service worker only controls pages at or below its own path, so
    Kavenegar's has to answer at the origin root. And it is fetched with no
    credentials — left out of the API-key allowlist it 401s in production and
    works locally, exactly as the login endpoints did."""

    def test_the_file_exists_and_is_what_kavenegar_generated(self):
        from pathlib import Path
        sw = Path("frontend/kvn-push-sw.js")
        assert sw.exists(), "the service worker file is missing"
        assert "cdn.kavenegar.com/sdk/sw.js" in sw.read_text(encoding="utf-8")

    def test_it_is_served_from_the_origin_root(self):
        import re
        src = open("app/main.py", encoding="utf-8").read()
        assert re.search(r'@app\.(get|api_route)\(\s*["\']/kvn-push-sw\.js["\']', src), \
            "no root route serves the service worker"

    def test_head_is_allowed_too(self):
        """A checker asking «does this file exist» often sends HEAD, and
        @app.get registers GET alone — so HEAD answered 405 on a file that
        served perfectly over GET."""
        import re
        src = open("app/main.py", encoding="utf-8").read()
        m = re.search(r'@app\.api_route\(\s*["\']/kvn-push-sw\.js["\'][^)]*\)', src)
        assert m and "HEAD" in m.group(0), "HEAD is not accepted for the service worker"

    def test_it_is_in_the_api_key_allowlist(self):
        src = open("app/main.py", encoding="utf-8").read()
        pub = src.split("public_paths = {")[1].split("}")[0]
        assert "/kvn-push-sw.js" in pub, \
            "the service worker would 401 in production, where API_KEY is set"

    def test_the_scope_header_is_sent(self):
        src = open("app/main.py", encoding="utf-8").read()
        fn = src.split("async def kavenegar_push_service_worker")[1][:900]
        assert "Service-Worker-Allowed" in fn

    def test_the_sdk_is_on_the_public_pages_and_not_the_admin_panel(self):
        from pathlib import Path
        for page in ("frontend/landing.html", "frontend/portal.html"):
            assert "cdn.kavenegar.com/sdk/page.js" in Path(page).read_text(encoding="utf-8"), \
                f"{page} does not load the push SDK"
        panel = Path("frontend/index.html").read_text(encoding="utf-8")
        assert "cdn.kavenegar.com" not in panel, \
            "the admin panel should not load a third-party script into an authenticated session"


class TestTheTestButtonTestsTheConfiguredRoute:
    """It always used plain send, which needs a sender line that can address
    the destination. On this account no such line exists, so the button
    answered «[412] شماره فرستنده نامعتبر است» permanently — including after
    the template that makes login codes work was configured. A test that can
    never pass reports a broken system that is not broken."""

    def test_it_prefers_the_template(self):
        src = open("app/api/routes/sms.py", encoding="utf-8").read()
        fn = src.split("async def sms_test")[1].split("\n@router")[0]
        assert "resolve_otp_template" in fn, "the test never asks whether a template exists"
        assert "send_verify" in fn, "the test cannot exercise the template route"

    def test_plain_send_is_still_there_for_accounts_with_a_real_line(self):
        src = open("app/api/routes/sms.py", encoding="utf-8").read()
        fn = src.split("async def sms_test")[1].split("\n@router")[0]
        assert "send_sms" in fn

    def test_the_response_names_the_route(self):
        """«ناموفق» alone points the reader at the sender field, which is the
        wrong place when the template route was the one that ran."""
        src = open("app/api/routes/sms.py", encoding="utf-8").read()
        fn = src.split("async def sms_test")[1].split("\n@router")[0]
        assert '"via"' in fn or "'via'" in fn

    def test_the_panel_shows_it(self):
        js = open("frontend/js/app.js", encoding="utf-8").read()
        fn = js.split("async function sendSmsTest")[1].split("\nasync function ")[0]
        assert "d.via" in fn, "the panel discards the route the server reported"
