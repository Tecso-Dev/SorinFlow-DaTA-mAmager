"""
What one phone call does to a lead.

1,728 of the 1,739 leads were still «new» on 18 September: the scraper fills
the table and nobody works it, because working it meant a big filterable
grid with a status dropdown. A call is one tap now — the outcome decides the
status, the score, and when the lead comes back:

    answered        → contacted (scores a call); stays with the caller
    no_answer       → try again: 2 h, then tomorrow 10:00, then two days —
                      four tries, after which it stops coming back on its own
    busy            → try again in 30 minutes
    callback        → comes back at the time the person asked for
    visit           → visit (scores a showing); a calendar entry when a time
                      is given
    not_interested  → rejected
    wrong_number    → rejected

Pure functions here; the route applies what they return. Tehran wall-clock
for «tomorrow morning».
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

TEHRAN = ZoneInfo("Asia/Tehran")

OUTCOMES = {
    "answered":       "پاسخ داد",
    "no_answer":      "پاسخ نداد",
    "busy":           "مشغول بود",
    "callback":       "خواست دوباره تماس بگیریم",
    "visit":          "بازدید گذاشتیم",
    "not_interested": "علاقه‌ای ندارد",
    "wrong_number":   "شماره اشتباه است",
}

MAX_UNANSWERED = 4


def _tomorrow_at(now: datetime, hour: int = 10) -> datetime:
    local = now.astimezone(TEHRAN)
    t = (local + timedelta(days=1)).replace(hour=hour, minute=0, second=0, microsecond=0)
    return t.astimezone(timezone.utc)


def retry_after(outcome: str, attempts: int, *, now: datetime) -> datetime | None:
    """When an unanswered lead comes back to the queue, or None to stop."""
    if outcome == "busy":
        return now + timedelta(minutes=30)
    if outcome != "no_answer":
        return None
    if attempts >= MAX_UNANSWERED:
        return None
    if attempts == 1:
        return now + timedelta(hours=2)
    if attempts == 2:
        return _tomorrow_at(now)
    return now + timedelta(days=2)


def apply(outcome: str, *, status: str, attempts: int, now: datetime,
          callback_at: datetime | None = None) -> dict:
    """The lead's new state after this call. `attempts` is the count BEFORE
    this call. Raises ValueError on an outcome the panel does not offer."""
    if outcome not in OUTCOMES:
        raise ValueError(outcome)
    attempts += 1
    new_status = status
    next_call = None
    if outcome == "answered" and status == "new":
        new_status = "contacted"
    elif outcome == "callback":
        if callback_at is None:
            raise ValueError("callback needs a time")
        next_call = callback_at
        if status == "new":
            new_status = "contacted"
    elif outcome == "visit":
        new_status = "visit"
    elif outcome in ("not_interested", "wrong_number"):
        new_status = "rejected"
    elif outcome in ("no_answer", "busy"):
        next_call = retry_after(outcome, attempts, now=now)
    return {"status": new_status, "call_attempts": attempts,
            "next_call_at": next_call, "last_call_at": now, "last_call_outcome": outcome}
