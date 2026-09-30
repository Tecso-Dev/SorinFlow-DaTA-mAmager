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


class TestTheWebPushIsGone:
    """Kavenegar's web push was dropped in phase 4. It put a third-party
    <script> on the public landing page and on the customer portal — two
    origins that also carry sign-in — and a service worker at the origin root
    that imported more code from the same CDN. Whoever controls that CDN
    controlled those pages, and the panel never used the feature.

    These check the removal instead of the wiring, so it cannot drift back in
    a copy-paste."""

    def test_no_page_loads_the_push_sdk(self):
        from pathlib import Path
        for page in ("frontend/landing.html", "frontend/portal.html", "frontend/index.html"):
            assert "cdn.kavenegar.com" not in Path(page).read_text(encoding="utf-8"), \
                f"{page} still loads a third-party script from Kavenegar's CDN"

    def test_the_service_worker_file_is_gone_and_its_path_only_unregisters(self):
        """The file is gone, but the path still answers — with a worker whose
        only job is to remove itself. A service worker outlives the page that
        registered it, so returning visitors still have the old one installed
        at scope "/", and a 404 there leaves that registration in place in
        more than one browser."""
        from pathlib import Path
        from starlette.testclient import TestClient
        import app.main as m
        assert not Path("frontend/kvn-push-sw.js").exists(), "the push service worker file is back"
        # the body it actually serves, not what the source says about it
        r = TestClient(m.app).get("/kvn-push-sw.js")
        assert r.status_code == 200
        body = r.text
        assert "unregister()" in body, "the retired worker does not unregister itself"
        assert "importScripts" not in body and "kavenegar" not in body.lower(), \
            "the retired worker still reaches for the CDN"
        assert r.headers.get("cache-control") == "no-store", \
            "a cached tombstone would outlive its own purpose"

    def test_the_csp_no_longer_allows_that_cdn(self):
        """A leftover host in the policy is not harmless: it is standing
        permission for the next script that points there."""
        import app.main as m
        assert "cdn.kavenegar.com" not in m._CSP_REPORT_ONLY

    def test_kavenegar_is_still_available_as_an_sms_gateway(self):
        """Only the web push went. Sending an SMS through Kavenegar is a
        different thing and is still a provider people choose."""
        from app.services import sms_service
        assert "kavenegar" in open(sms_service.__file__, encoding="utf-8").read().lower()


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
