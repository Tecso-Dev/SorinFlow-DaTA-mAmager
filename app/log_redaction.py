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
import re
from typing import Any

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
        record["message"] = redact(record["message"])
    except Exception:
        # A logging path that can raise is worse than one that leaks: this runs
        # inside every log call, including the ones reporting a failure.
        pass
    return True
