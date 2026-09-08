"""
The SMS panel's own event log.

crm_sms_logs records messages that were sent. It cannot answer the questions
that get asked when SMS stops working — «چرا پیامک نرفت؟», «کی تنظیمات را عوض
کرد؟» — because the reason for a failure lives in a response blob and a
settings change was never recorded at all.

The rules that matter are the two inherited from job_log, and they are the
reason this file exists rather than a stray db.add() at each call site:
its own session, and it never raises.
"""
import ast
import inspect
import re
from pathlib import Path

import pytest


def _code_only(src):
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef, ast.Module)):
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                node.body.pop(0)
    return ast.unparse(tree)


class TestItCannotTakeDownWhatItDescribes:

    def test_record_uses_its_own_session(self):
        """A caller is mid-transaction when it logs a send. A failed INSERT on
        that session aborts it and the send dies with it — a logging problem
        turned into a delivery problem."""
        from app.services import sms_log
        src = _code_only(inspect.getsource(sms_log.record))
        assert "async_session_maker()" in src, \
            "record() writes on somebody else's session"

    def test_record_never_raises(self):
        from app.services import sms_log
        src = _code_only(inspect.getsource(sms_log.record))
        assert "except Exception" in src and "return False" in src

    @pytest.mark.asyncio
    async def test_a_broken_database_is_false_not_an_exception(self, monkeypatch):
        from app.services import sms_log

        def boom(*a, **kw):
            raise RuntimeError("database is on fire")

        monkeypatch.setattr(sms_log, "async_session_maker", boom)
        assert await sms_log.record(sms_log.SEND, "x") is False


class TestEverySendPathIsCovered:
    """Logging at each call site is how one path gets forgotten. Every send
    already funnels through _log(), so the event belongs there."""

    def test_the_shared_helper_records(self):
        src = Path("app/api/routes/sms.py").read_text(encoding="utf-8")
        fn = src.split("async def _log(")[1].split("\nclass ")[0]
        assert "sms_log.record_send" in fn, \
            "_log does not write an event, so each caller would have to"

    def test_settings_changes_are_recorded(self):
        src = _code_only(Path("app/api/routes/sms.py").read_text(encoding="utf-8"))
        fn = src.split("async def put_sms_settings")[1].split("async def ")[0]
        assert "sms_log.record" in fn

    def test_no_secret_value_is_written_to_the_log(self):
        """The panel renders this table. An API key must never reach it."""
        src = _code_only(Path("app/api/routes/sms.py").read_text(encoding="utf-8"))
        fn = src.split("async def put_sms_settings")[1].split("async def ")[0]
        call = fn[fn.find("sms_log.record"):][:600]
        for leak in ("payload.api_key", "_encrypt", "KEY_API_KEY"):
            assert leak not in call, f"{leak} appears in the logged event"


class TestTheRecipientIsMasked:
    """A readable log must not double as a contact export."""

    @pytest.mark.parametrize("raw,expect", [
        ("09121234567", "0912***4567"),
        ("09058432452", "0905***2452"),
    ])
    def test_middle_digits_are_hidden(self, raw, expect):
        from app.services.sms_log import _mask
        assert _mask(raw) == expect

    @pytest.mark.parametrize("raw", ["", None, "123"])
    def test_short_or_missing_is_not_a_crash(self, raw):
        from app.services.sms_log import _mask
        _mask(raw)


class TestTheReasonSurvives:
    """A failure that says only «ناموفق» is the state this replaces."""

    def test_record_send_keeps_the_provider_message(self):
        from app.services import sms_log
        src = _code_only(inspect.getsource(sms_log.record_send))
        assert "response" in src, "the reason for a failure is dropped"

    def test_a_failure_is_not_logged_as_info(self):
        from app.services import sms_log
        src = _code_only(inspect.getsource(sms_log.record_send))
        assert "warning" in src


class TestItIsReadable:

    def test_the_endpoint_exists(self):
        src = Path("app/api/routes/sms.py").read_text(encoding="utf-8")
        assert re.search(r'@router\.get\(\s*["\']/events["\']', src)

    def test_the_model_is_registered_for_create_all(self):
        """A model nobody imports is a table that never gets created."""
        src = Path("app/database.py").read_text(encoding="utf-8")
        assert "sms_log" in src.split("Base.metadata.create_all")[0]

    def test_the_panel_renders_it(self):
        js = Path("frontend/js/app.js").read_text(encoding="utf-8")
        assert "function loadSmsEvents" in js
        assert "/sms/events" in js
        html = Path("frontend/index.html").read_text(encoding="utf-8")
        assert 'id="sms-events-table"' in html

    def test_it_is_loaded_when_the_panel_opens(self):
        js = _code_only(Path("frontend/js/app.js").read_text(encoding="utf-8")) \
            if False else Path("frontend/js/app.js").read_text(encoding="utf-8")
        assert re.search(r"loadSmsMessages\(\);\s*\n\s*loadSmsEvents\(\);", js), \
            "the log is never loaded, so the card renders empty"
