"""
Log redaction.

Container stdout is persisted to the node's disk by containerd, and the file
sink writes to a volume — so anything reaching a log here is at rest on the
server. The scraper handles Divar session cookies, customer phone numbers and a
database URL with a password in it, and all three were being written in plain
text.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.log_redaction import redact, redact_filter, request_id_var, inject_request_id


class TestSecrets:
    def test_a_divar_session_cookie_is_not_logged(self):
        """auth.py logged the first 50 characters of every cookie value, and
        Divar's «token» cookie IS the session."""
        out = redact("Cookie: token = eyJhbGciOiJIUzI1NiJ9.cGF5bG9hZA.c2ln")
        assert "eyJhbGciOiJIUzI1NiJ9" not in out

    def test_database_password_never_survives(self):
        """A connection error traceback prints the URL, password included."""
        out = redact("postgresql+asyncpg://sorinflow:sup3rs3cret@postgres:5432/divar_scraper")
        assert "sup3rs3cret" not in out
        assert "postgres:5432" in out, "masked more than the password"

    def test_bearer_tokens_are_masked(self):
        out = redact("Authorization: Bearer abcdefghijklmnop.qrstuvwx")
        assert "abcdefghijklmnop" not in out

    def test_api_key_assignments_are_masked(self):
        assert "s3cr3tk3yv4lu3" not in redact("api_key=s3cr3tk3yv4lu3abc")


class TestPersonalData:
    def test_ascii_phone_numbers_are_partially_masked(self):
        out = redact("Entering phone number: 09123456789")
        assert "09123456789" not in out
        # enough of it survives to tell two accounts apart in a log
        assert "0912" in out and "89" in out

    def test_persian_digits_are_masked_too(self):
        """Scraped Divar text is full of Persian digits — a pattern written
        only for 0-9 would miss the numbers that matter most."""
        out = redact("شماره: ۰۹۱۲۳۴۵۶۷۸۹")
        assert "۰۹۱۲۳۴۵۶۷۸۹" not in out

    def test_ordinary_numbers_are_left_alone(self):
        """Prices, areas and counts must stay readable."""
        for s in ("saved 1234 properties", "area 85 m2", "job 42 finished"):
            assert redact(s) == s


class TestFilterBehaviour:
    def test_filter_rewrites_the_record_and_passes_it(self):
        rec = {"message": "phone 09123456789"}
        assert redact_filter(rec) is True
        assert "09123456789" not in rec["message"]

    def test_filter_never_raises(self):
        """It runs inside every log call, including the ones reporting a
        failure — a logging path that can raise is worse than one that leaks."""
        class Explodes:
            def __str__(self):
                raise RuntimeError("boom")
        rec = {"message": Explodes()}
        assert redact_filter(rec) is True

    def test_redaction_is_idempotent(self):
        once = redact("token=abcdefghijklmnop and 09123456789")
        assert redact(once) == once


class TestRequestIdPatcher:
    def test_default_outside_a_request_is_a_dash(self):
        rec = {"extra": {}}
        inject_request_id(rec)
        assert rec["extra"]["request_id"] == "-"

    def test_carries_whatever_the_middleware_set(self):
        token = request_id_var.set("abc123")
        try:
            rec = {"extra": {}}
            inject_request_id(rec)
            assert rec["extra"]["request_id"] == "abc123"
        finally:
            request_id_var.reset(token)

    def test_scoped_to_where_it_was_set(self):
        """A reset must not leak into log lines logged after it."""
        token = request_id_var.set("only-here")
        request_id_var.reset(token)
        rec = {"extra": {}}
        inject_request_id(rec)
        assert rec["extra"]["request_id"] == "-"


def test_no_app_module_logs_through_std_logging():
    """auth.py used std logging.getLogger(__name__), so its lines — including
    the [audit] ones — bypassed loguru and the redaction filter entirely: a
    Divar session or a phone number logged there was never masked."""
    import pathlib
    app_dir = pathlib.Path(__file__).resolve().parent.parent / "app"
    offenders = [str(p) for p in app_dir.rglob("*.py")
                if "logging.getLogger" in p.read_text(encoding="utf-8")]
    assert not offenders, f"still logging through std logging: {offenders}"


def test_both_sinks_are_filtered():
    """A sink added without the filter is a hole in the only defence there is."""
    import re
    src = open(os.path.join(os.path.dirname(__file__), "..", "app", "main.py"),
               encoding="utf-8").read()
    adds = re.findall(r"logger\.add\((.*?)\n    \)", src, re.S)
    assert adds, "could not find the logger.add calls"
    for block in adds:
        assert "redact_filter" in block, f"a sink has no redaction filter:\n{block[:200]}"
        assert "diagnose=False" in block, (
            "diagnose=True prints local variables inside tracebacks, which is "
            "how DATABASE_URL and its password reach the log")
