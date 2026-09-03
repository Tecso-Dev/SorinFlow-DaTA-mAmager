"""
In-process OTP wait/resolve store for Divar contact-info SMS verification.
Background scraper tasks register a wait; the API endpoint resolves it.
"""
import asyncio
import time
from typing import Optional, Dict, Any

_store: Dict[str, Any] = {}

# When the user dismisses an OTP prompt, we stop asking for the rest of that
# run so the scraper doesn't block ~300s on every phone that needs a code.
#
# Per job, not global. Up to three scrapes run at once, and a single shared
# flag meant dismissing one prompt silently suppressed phone extraction on
# every other running job for fifteen minutes — with nothing on screen to say
# why those jobs suddenly stopped collecting numbers.
_cancelled_until: Dict[str, float] = {}
_CANCEL_WINDOW = 900  # 15 min

# Unanswered code prompts per job, counted since the last successful reveal.
#
# A challenge belongs to ONE Divar account. Suppressing the whole job on the
# first unanswered prompt threw away the other accounts too — which is the
# entire point of rotation — and a run with three good sessions saved 200
# listings with no phone number on any of them.
_timeouts: Dict[str, int] = {}


def note_timeout(job_id: Optional[str]) -> int:
    """Record one unanswered prompt. Returns the count for this job."""
    if not job_id:
        return 0
    _timeouts[job_id] = _timeouts.get(job_id, 0) + 1
    return _timeouts[job_id]


def strikes(job_id: Optional[str]) -> int:
    """Unanswered prompts for this job since the last successful reveal."""
    return _timeouts.get(job_id, 0) if job_id else 0


def clear_timeouts(job_id: Optional[str]) -> None:
    """A reveal succeeded: the accounts are not all challenged after all."""
    if job_id:
        _timeouts.pop(job_id, None)


def job_of(key) -> str:
    """The job a request key belongs to. Keys are «{job_id}:{divar_id}».

    Coerces rather than requiring a string. ScrapingJob.job_id is a UUID
    column with as_uuid=True, so callers holding the row hand over a
    uuid.UUID and `.split` on it raised AttributeError — swallowed by the
    caller's except, which turned a broken check into a silent one. A UUID
    stringifies to exactly the prefix the keys are built from, so accepting
    both is correct rather than merely forgiving.
    """
    return str(key or "").split(":", 1)[0]


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


def wait_window() -> int:
    """How long a request stays open, from the one setting that decides it.

    This used to be hardcoded to 300 here while the scraper actually gave up at
    otp_wait_timeout (120), so the dashboard kept offering a prompt whose
    request had already been dropped — the code went in and came back "no
    pending OTP request for this key".
    """
    from app.config import get_settings
    return int(getattr(get_settings(), "otp_wait_timeout", 300) or 300)


def get_pending() -> list:
    now = time.time()
    window = wait_window()
    return [
        {
            "key": k,
            "phone_hint": v["phone_hint"],
            # the countdown is the server's to state: the browser cannot know
            # when the request was registered, only when it noticed
            "remaining": max(int(window - (now - v["ts"])), 0),
        }
        for k, v in list(_store.items())
        if not v["event"].is_set() and now - v["ts"] < window
    ]


def pop_code(key: str) -> Optional[str]:
    entry = _store.pop(key, None)
    return entry["code"] if entry else None


def clear(key: str) -> None:
    _store.pop(key, None)


def clear_job(job_id: str) -> int:
    """Drop every request belonging to one job; returns how many.

    Keys are «{job_id}:{divar_id}», so cancelling a job takes its prompts with
    it instead of leaving one open against a scrape that has stopped.
    """
    prefix = f"{job_id}:"
    keys = [k for k in list(_store) if k.startswith(prefix)]
    for k in keys:
        _store.pop(k, None)
    return len(keys)


def cancel_all(job_id: Optional[str] = None) -> int:
    """User declined OTP: drop that job's pending prompts and stop asking it for
    codes for a while. Returns how many pending requests were dropped.

    With no job_id this still suppresses everything, because the dashboard's
    «close» button is not always able to say which job it meant — but the
    caller should pass one whenever it can.
    """
    now = time.time()
    if job_id is None:
        jobs = {job_of(k) for k in _store}
        dropped = len(_store)
        _store.clear()
        for j in jobs:
            _cancelled_until[j] = now + _CANCEL_WINDOW
        return dropped

    prefix = f"{job_id}:"
    keys = [k for k in list(_store) if k.startswith(prefix)]
    for k in keys:
        _store.pop(k, None)
    _cancelled_until[job_id] = now + _CANCEL_WINDOW
    return len(keys)


def is_cancelled(key_or_job: Optional[str] = None) -> bool:
    """True while OTP prompts are suppressed for this job.

    Accepts either a full request key or a bare job id, so callers holding an
    otp_key do not have to split it themselves.
    """
    if not key_or_job:
        return False
    job = job_of(key_or_job)
    until = _cancelled_until.get(job, 0.0)
    if until and time.time() >= until:
        # expired — drop it rather than letting the dict grow for the life of
        # the process
        _cancelled_until.pop(job, None)
        return False
    return time.time() < until


def reset_cancel(job_id: Optional[str] = None) -> None:
    """Clear the suppression — called when a fresh scrape job starts."""
    if job_id is None:
        _cancelled_until.clear()
    else:
        _cancelled_until.pop(job_id, None)
