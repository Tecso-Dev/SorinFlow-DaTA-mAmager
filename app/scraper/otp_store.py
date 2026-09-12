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
    _store[key] = {"event": evt, "code": None, "phone_hint": phone_hint,
                   "ts": time.time(), "resend": False, "resends": 0}
    return evt


# Asking again is worth a hard cap. Each one is a real SMS Divar sends on our
# behalf, and a panel button that can be held down is a way to get an account
# rate-limited by its owner rather than by Divar.
MAX_RESENDS = 3


def ask_resend(key: str) -> dict:
    """Ask the parked browser to press Divar's «ارسال مجدد».

    Only the browser can do it — the control lives on the page it is sitting
    on — so this raises a flag the wait loop picks up within its next slice.
    Returns what to tell the operator.
    """
    entry = _store.get(key)
    if not entry or entry["event"].is_set():
        return {"ok": False, "reason": "no_request",
                "message": "این درخواست دیگر باز نیست — اسکرپر رد شده و برای آگهی بعدی دوباره می‌پرسد"}
    if entry.get("resends", 0) >= MAX_RESENDS:
        return {"ok": False, "reason": "limit",
                "message": f"بیشتر از {MAX_RESENDS} بار نمی‌شود کد خواست"}
    entry["resend"] = True
    return {"ok": True, "message": "درخواست ارسال دوباره ثبت شد"}


def take_resend(key: str) -> bool:
    """Consume a pending resend request. Called only by the wait loop."""
    entry = _store.get(key)
    if not entry or not entry.get("resend"):
        return False
    entry["resend"] = False
    entry["resends"] = entry.get("resends", 0) + 1
    return True


def restart_clock(key: str) -> None:
    """A fresh code deserves a fresh window — otherwise the countdown the
    panel shows belongs to the code that never arrived."""
    entry = _store.get(key)
    if entry:
        entry["ts"] = time.time()


def submit(key: str, code: str) -> bool:
    entry = _store.get(key)
    if not entry or entry["event"].is_set():
        return False
    entry["code"] = code
    entry["event"].set()
    return True


def wait_window() -> int:
    """How long a request stays open — the SAME number the browser waits.

    It was otp_wait_timeout alone, and that stopped being the browser's
    answer when wait-for-human arrived: contact_extractor waits
    max(otp_wait_timeout, otp_wait_max_seconds) when otp_wait_for_human is
    on, which is 6 hours against this function's 5 minutes.

    So get_pending() dropped the prompt after five minutes and the panel
    said «مهلت این کد تمام شد — اسکرپر بدون این شماره ادامه داد» while the
    browser sat parked for another five hours and fifty-five, with the entry
    still in _store and a code still perfectly acceptable. The operator was
    told it was too late and given a disabled button; the run stayed paused
    because of the message, not because of Divar.

    The expression is copied from the extractor deliberately. Two places
    deriving one deadline is what broke it; if this ever moves, move both.
    """
    from app.config import get_settings
    cfg = get_settings()
    base = int(getattr(cfg, "otp_wait_timeout", 300) or 300)
    if bool(getattr(cfg, "otp_wait_for_human", False)):
        return max(base, int(getattr(cfg, "otp_wait_max_seconds", 21600) or 21600))
    return base


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
            "resends": v.get("resends", 0),
            "resends_left": max(MAX_RESENDS - v.get("resends", 0), 0),
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
