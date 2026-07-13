"""
In-process OTP wait/resolve store for Divar contact-info SMS verification.
Background scraper tasks register a wait; the API endpoint resolves it.
"""
import asyncio
import time
from typing import Optional, Dict, Any

_store: Dict[str, Any] = {}

# When the user dismisses an OTP prompt, we stop asking for the rest of the
# run so the scraper doesn't block ~300s on every phone that needs a code.
_cancelled_until: float = 0.0
_CANCEL_WINDOW = 900  # 15 min


def request(key: str, phone_hint: str = "") -> asyncio.Event:
    evt = asyncio.Event()
    _store[key] = {"event": evt, "code": None, "phone_hint": phone_hint, "ts": time.time()}
    return evt


def submit(key: str, code: str) -> bool:
    entry = _store.get(key)
    if not entry or entry["event"].is_set():
        return False
    entry["code"] = code
    entry["event"].set()
    return True


def get_pending() -> list:
    now = time.time()
    return [
        {"key": k, "phone_hint": v["phone_hint"]}
        for k, v in list(_store.items())
        if not v["event"].is_set() and now - v["ts"] < 300
    ]


def pop_code(key: str) -> Optional[str]:
    entry = _store.pop(key, None)
    return entry["code"] if entry else None


def clear(key: str) -> None:
    _store.pop(key, None)


def cancel_all() -> None:
    """User declined OTP: clear pending and suppress new prompts for a while."""
    global _cancelled_until
    _cancelled_until = time.time() + _CANCEL_WINDOW
    _store.clear()


def is_cancelled() -> bool:
    """True while OTP prompts are suppressed (user recently dismissed one)."""
    return time.time() < _cancelled_until


def reset_cancel() -> None:
    """Clear the suppression — called when a fresh scrape job starts."""
    global _cancelled_until
    _cancelled_until = 0.0
