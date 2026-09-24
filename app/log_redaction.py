"""
Redaction for everything written to a log sink.

This is not defence in depth, it is the only defence: container stdout is
persisted to the node's disk by containerd and the file sink writes to a
volume, so anything logged here is at rest on the server and lands in whatever
reads it next. The scraper handles Divar session cookies, customer phone
numbers and a database URL with a password in it — all three were reaching the
log in plain text.

Applied as a loguru `filter=` on both sinks, so a new call site cannot bypass
it by forgetting to mask. Call sites are still fixed where they were obviously
wrong; this is what catches the ones nobody thought about.
"""
import contextvars
import re
from typing import Any

# The request-id middleware (app/main.py) sets this for the life of one
# request; "-" is what every background loop and startup log line carries,
# since none of them run inside a request.
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-")


def inject_request_id(record: dict) -> None:
    """loguru patcher: stamp every record with the request id in scope right
    now, so a line logged three calls deep still carries it without every
    call site threading it through by hand."""
    record["extra"]["request_id"] = request_id_var.get()

# Persian and Arabic-Indic digits appear in scraped Divar text, so a pattern
# written only for 0-9 would miss the numbers that matter most here.
_DIGITS = r"0-9۰-۹٠-٩"

_PATTERNS = [
    # Iranian mobile numbers: keep the operator prefix and the last two digits,
    # which is enough to tell two accounts apart in a log without publishing
    # anyone's number.
    (re.compile(rf"(?<![{_DIGITS}])([{_DIGITS}]{{4}})[{_DIGITS}]{{5}}([{_DIGITS}]{{2}})(?![{_DIGITS}])"),
     r"\1*****\2"),
    # credentials inside a URL — postgresql+asyncpg://user:PASSWORD@host/db
    (re.compile(r"(://[^:/\s]+:)[^@/\s]+(@)"), r"\1***\2"),
    # JWTs, and anything else shaped like one
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+"), "<jwt>"),
    # cookie/token assignments: name=value where the value is long enough to be
    # a secret rather than a flag
    (re.compile(r"(?i)\b(token|session|cookie|secret|password|api[_-]?key|authorization)"
                r"(\s*[=:]\s*)(\"?)([A-Za-z0-9._\-+/=]{12,})"), r"\1\2\3<redacted>"),
    # bare Bearer credentials
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{12,}"), "Bearer <redacted>"),
]


def redact(text: Any) -> str:
    """Mask secrets and personal data in one string."""
    s = str(text)
    for pattern, replacement in _PATTERNS:
        s = pattern.sub(replacement, s)
    return s


def redact_filter(record: dict) -> bool:
    """loguru filter. Rewrites the message in place and always passes the record.

    A filter is used rather than a formatter because it runs before every sink
    and before the JSON serialiser, so stdout and the file cannot disagree about
    what was masked.
    """
    try:
        exc = record.get("exception")
        if exc is not None and exc.type is not None:
            # A sink prints the traceback — the exception's own text included,
            # a driver error's [parameters: …] with it — from the exception
            # object, past this filter. Folded into the message it is redacted
            # like the rest; still no local variables, as with diagnose=False.
            import traceback
            tb = "".join(traceback.format_exception(exc.type, exc.value, exc.traceback))
            record["message"] = f"{record['message']}\n{tb.rstrip()}"
            record["exception"] = None
        record["message"] = redact(record["message"])
    except Exception:
        # A logging path that can raise is worse than one that leaks: this runs
        # inside every log call, including the ones reporting a failure.
        pass
    return True
