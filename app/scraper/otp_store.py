"""
In-process OTP wait/resolve store for Divar contact-info SMS verification.
Background scraper tasks register a wait; the API endpoint resolves it.
"""
import asyncio
import time
from typing import Optional, Dict, Any

_store: Dict[str, Any] = {}


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
        if not v["event"].is_set() and now - v["ts"] < 90
    ]


def pop_code(key: str) -> Optional[str]:
    entry = _store.pop(key, None)
    return entry["code"] if entry else None


def clear(key: str) -> None:
    _store.pop(key, None)
