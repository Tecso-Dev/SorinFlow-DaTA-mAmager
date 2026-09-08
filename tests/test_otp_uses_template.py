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
