"""
Fires the saved scrapes at their hour.

One loop, one minute apart. Each due schedule is launched through the same
_launch_job a click goes through, as its owner — so the owner's Divar
accounts are the pool, the owner's permissions apply, and a schedule can
never scrape on somebody else's number. What happened is written back on
the schedule row, where the panel shows it: started with which job, or why
not (no valid session, too many runs already, the owner was disabled).

Times are Tehran wall-clock. next_run_at is recomputed after every firing,
so a missed hour (the pod was restarting) fires once as soon as the loop is
back, not once per missed minute.

The publish date of a schedule is relative. The config holds `posted_days_ago`
— 0 is «امروز», 1 «دیروز», N «N روز پیش» — and every firing turns it into the
actual Tehran day, because «yesterday» saved as one fixed date searched that
one old day every morning (#33). A schedule saved before that carries a fixed
`posted_date`; it is read as the same distance from the day the schedule was
made, so nothing had to be migrated.
"""
import asyncio
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from loguru import logger
from sqlalchemy import select

from app.config import get_settings
from app.database import async_session_maker
from app.models.scrape_schedule import ScrapeSchedule
from app.models.user import User

settings = get_settings()
# A fixed +03:30, not ZoneInfo("Asia/Tehran"): the Playwright image ships no
# tz database, so ZoneInfo raised at import and the whole app crash-looped —
# the site was down for fifteen minutes on 2026-09-18. Iran has had no
# daylight saving since 2022, so the fixed offset is also simply correct.
TEHRAN = timezone(timedelta(hours=3, minutes=30), "Asia/Tehran")
TICK_SECONDS = 60

# A daily run of «everything» re-crawls yesterday's listings only to find
# they exist. When the form set no age and no date, the schedule asks Divar
# for the last day only — which is what «every morning» means.
DEFAULT_MAX_AGE_HOURS = 24


def next_occurrence(hour: int, minute: int, *, now=None) -> datetime:
    """The next Tehran wall-clock hour:minute strictly after `now`, as UTC."""
    now = (now or datetime.now(timezone.utc)).astimezone(TEHRAN)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target.astimezone(timezone.utc)


# How far back «N روز پیش» may reach when a person sets it. A schedule saved
# with a fixed date is converted whatever the distance: that is arithmetic,
# not a choice.
MAX_POSTED_DAYS_AGO = 30

_DATE_KEYS = ("posted_date", "posted_days_ago")
_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
_DATE_CHOICES = f"«امروز»، «دیروز» یا «N روز پیش» (N تا {MAX_POSTED_DAYS_AGO})".translate(_FA_DIGITS)


class ScheduleDateError(ValueError):
    """The publish date a form asks for cannot be a schedule's. The message is
    the sentence the person should read."""


def tehran_day(moment: datetime) -> date:
    """The Tehran calendar day `moment` falls on. A naive value is UTC:
    SQLite hands datetimes back without their zone."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(TEHRAN).date()


def _fixed_day(value) -> Optional[date]:
    """A fixed `posted_date` as a day — read the way the scraper reads it."""
    try:
        return datetime.fromisoformat(str(value).strip()).date()
    except (TypeError, ValueError):
        return None


def _jalali(day: date) -> str:
    from app.services.dpa_service import to_jalali
    return to_jalali(datetime(day.year, day.month, day.day)).translate(_FA_DIGITS)


def days_ago_label(days: int) -> str:
    """«امروز», «دیروز», «۳ روز پیش» — the words the schedule form uses."""
    if days == 0:
        return "امروز"
    if days == 1:
        return "دیروز"
    return f"{days} روز پیش".translate(_FA_DIGITS)


def relative_posted_days(config: Any, created_at=None) -> Optional[int]:
    """How many days before the run the schedule's publish date lies, or None
    when it has none.

    `posted_days_ago` is the stored form. A fixed `posted_date` (a schedule
    saved before that existed) is the same distance from the schedule's own
    Tehran creation day: made on the 5th with the 4th typed in is «دیروز».
    A date after the creation day cannot be «N days ago», so it reads as
    «امروز». Without a creation day there is nothing to measure from, and
    the fixed date is left as it is.
    """
    cfg = config or {}
    days = cfg.get("posted_days_ago")
    if isinstance(days, int) and not isinstance(days, bool) and days >= 0:
        return days
    if not cfg.get("posted_date") or created_at is None:
        return None
    fixed = _fixed_day(cfg["posted_date"])
    if fixed is None:
        return None
    return max((tehran_day(created_at) - fixed).days, 0)


def with_posted_days_ago(config: Any, days: Optional[int]) -> dict:
    """A copy of the config holding this publish date, in the relative form
    only. None means «no date», which is the last day again (the same default
    a new schedule stores); with a date the age is not kept, because the
    scraper ignores it once a day is named."""
    out = {k: v for k, v in (config or {}).items() if k not in _DATE_KEYS}
    if days is None:
        if not out.get("max_age_hours"):
            out["max_age_hours"] = DEFAULT_MAX_AGE_HOURS
        return out
    out["posted_days_ago"] = days
    if out.get("max_age_hours") == DEFAULT_MAX_AGE_HOURS:
        del out["max_age_hours"]
    return out


def as_relative(config: Any, created_at=None) -> dict:
    """The config with a fixed date, if it has one, converted to relative.
    A copy; a config with no fixed date, or none that can be measured, comes
    back equal."""
    cfg = config or {}
    days = relative_posted_days(cfg, created_at)
    if days is None or cfg.get("posted_days_ago") is not None or not cfg.get("posted_date"):
        return dict(cfg)
    return with_posted_days_ago(cfg, days)


def fixed_date_note(config: Any, created_at=None) -> Optional[str]:
    """One line for a schedule saved with a fixed date: that it was converted,
    and to what. None for every other schedule."""
    cfg = config or {}
    if cfg.get("posted_days_ago") is not None or created_at is None:
        return None
    fixed = _fixed_day(cfg.get("posted_date"))
    days = relative_posted_days(cfg, created_at)
    if fixed is None or days is None:
        return None
    made = tehran_day(created_at)
    if fixed > made:
        return f"تاریخ ثابت {_jalali(fixed)} بعد از روز ساخت بود و به «{days_ago_label(days)}» تبدیل شد"
    return (f"تاریخ ثابت {_jalali(fixed)} به «{days_ago_label(days)}» تبدیل شد "
            f"(نسبت به روز ساخت، {_jalali(made)})")


def describe_date(config: Any, created_at=None) -> dict:
    """What the panel shows of a schedule's publish date: the config with the
    date relative, how many days ago, its words, and the conversion note."""
    days = relative_posted_days(config, created_at)
    return {"config": as_relative(config, created_at), "days_ago": days,
            "label": None if days is None else days_ago_label(days),
            "note": fixed_date_note(config, created_at)}


def check_days_ago(days: Any) -> int:
    """`days` as a publish date a person may set on a schedule, or
    ScheduleDateError: a whole number from 0 (امروز) to MAX_POSTED_DAYS_AGO."""
    if isinstance(days, bool) or not isinstance(days, int) or not 0 <= days <= MAX_POSTED_DAYS_AGO:
        raise ScheduleDateError(f"تاریخ انتشار زمان‌بندی باید {_DATE_CHOICES} باشد")
    return days


def _asked_days_ago(cfg: dict, now: datetime) -> Optional[int]:
    """The publish date a new form asks for, as days ago; ScheduleDateError
    when it cannot be one. `posted_days_ago` is the form. A fixed
    `posted_date` (an older client) is converted from today, which is the
    schedule's creation day."""
    if cfg.get("posted_days_ago") is not None:
        return check_days_ago(cfg["posted_days_ago"])
    if not cfg.get("posted_date"):
        return None
    fixed = _fixed_day(cfg["posted_date"])
    if fixed is None:
        raise ScheduleDateError(f"تاریخ انتشار معتبر نیست — باید {_DATE_CHOICES} باشد")
    days = (tehran_day(now) - fixed).days
    if not 0 <= days <= MAX_POSTED_DAYS_AGO:
        raise ScheduleDateError(
            f"تاریخ ثابت {_jalali(fixed)} در آینده است یا از امروز دورتر از حد مجاز — "
            f"تاریخ زمان‌بندی نسبی است و باید {_DATE_CHOICES} باشد")
    return days


def stored_config(config: dict, *, now=None) -> dict:
    """A new schedule's form as it is saved: checked the way a run is, with
    the publish date relative and never fixed. Raises ScheduleDateError for a
    date that cannot be kept, and pydantic's error for any other bad field."""
    from app.schemas import ScrapingJobCreate

    now = now or datetime.now(timezone.utc)
    form = dict(config or {})
    days = _asked_days_ago(form, now)
    form = {k: v for k, v in form.items() if k not in _DATE_KEYS}
    if days is not None:
        form["posted_days_ago"] = days
    saved = ScrapingJobCreate(**config_for_run(form, now=now)).model_dump(exclude_none=True)
    if days is not None:
        saved.pop("posted_date", None)
        saved["posted_days_ago"] = days
    return saved


def config_for_run(config: Any, created_at=None, *, now=None) -> dict:
    """The saved form as one run of it: the publish date turned into the actual
    Tehran day of `now`, and the daily default applied.

    The result is what the panel's «شروع» would have sent for that day — a
    `posted_date` and no age, since the scraper ignores the age once a day is
    named — so a scheduled run and a manual one are the same job.
    """
    cfg = dict(config or {})
    cfg.pop("owner_user_id", None)
    days = relative_posted_days(cfg, created_at)
    cfg.pop("posted_days_ago", None)
    if days is not None:
        today = tehran_day(now or datetime.now(timezone.utc))
        cfg["posted_date"] = (today - timedelta(days=days)).isoformat()
        cfg.pop("max_age_hours", None)
    if not cfg.get("max_age_hours") and not cfg.get("posted_date"):
        cfg["max_age_hours"] = DEFAULT_MAX_AGE_HOURS
    return cfg


async def fire(schedule: ScrapeSchedule, db) -> dict:
    """Launch one schedule now, as its owner. Records the outcome on the row.
    Returns what it recorded."""
    from fastapi import HTTPException
    from app.api.routes.scraper import _launch_job
    from app.schemas import ScrapingJobCreate

    # Taken once, before anything else: «yesterday» is the day before the one
    # this firing happens on, however long the launch below takes.
    now = datetime.now(timezone.utc)
    owner = (await db.execute(
        select(User).where(User.id == schedule.owner_user_id))).scalars().first()
    result: dict
    from app.auth.dependencies import phone_gate_reason
    gate = await phone_gate_reason(owner, db) if owner is not None else None
    if owner is None or not owner.is_active:
        result = {"status": "skipped", "detail": "صاحب زمان‌بندی غیرفعال است"}
    elif gate:
        # The same rule as starting by hand: a person whose own number is
        # unverified does not scrape — at 8am any more than at noon.
        result = {"status": "skipped", "detail": f"شمارهٔ موبایل صاحب زمان‌بندی تأیید نشده — {gate}"}
    else:
        try:
            cfg = config_for_run(schedule.config, getattr(schedule, "created_at", None), now=now)
            job = await _launch_job(ScrapingJobCreate(**cfg), db, owner, interactive=False)
            schedule.last_job_id = str(job.job_id)
            result = {"status": "started", "detail": f"اسکرپ {str(job.job_id)[:8]} شروع شد"}
            day = _fixed_day(cfg.get("posted_date"))
            if day:
                # Which day it went for, where the person looks: «اجرای امروز
                # صبح تاریخ ۵ مهر داشت» could not be told from a hand-started run.
                result["posted_date"] = day.isoformat()
                result["detail"] += f" — آگهی‌های {_jalali(day)}"
        except HTTPException as e:
            result = {"status": "failed", "detail": str(e.detail)}
        except Exception as e:
            result = {"status": "failed", "detail": f"{type(e).__name__}: {e}"[:200]}
    schedule.last_run_at = now
    schedule.last_result = {**result, "at": now.isoformat(timespec="seconds")}
    schedule.next_run_at = next_occurrence(schedule.hour, schedule.minute, now=now)
    await db.commit()
    logger.info(f"[schedule] #{schedule.id} «{schedule.name}» → {result}")
    return result


async def tick() -> int:
    """Fire every enabled schedule whose time has come. Never raises."""
    fired = 0
    try:
        async with async_session_maker() as db:
            now = datetime.now(timezone.utc)
            due = (await db.execute(
                select(ScrapeSchedule).where(
                    ScrapeSchedule.enabled == True,  # noqa: E712
                    ScrapeSchedule.next_run_at <= now)
                .order_by(ScrapeSchedule.next_run_at.asc())
            )).scalars().all()
            for s in due:
                await fire(s, db)
                fired += 1
    except Exception as e:
        logger.warning(f"[schedule] tick failed: {type(e).__name__}: {e}")
    return fired


async def scheduler_loop() -> None:
    """Runs for the life of the process. SCRAPE_SCHEDULER=0 disables."""
    if not getattr(settings, "scrape_scheduler", True):
        logger.info("[schedule] disabled")
        return
    await asyncio.sleep(120)         # let startup finish
    from app.services.supervisor import beat
    while True:
        beat("scrape_scheduler")
        await tick()
        await asyncio.sleep(TICK_SECONDS)
