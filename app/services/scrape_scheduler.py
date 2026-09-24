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
"""
import asyncio
from datetime import datetime, timedelta, timezone

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


def config_for_run(config: dict) -> dict:
    """The saved form, with the daily default applied."""
    cfg = dict(config or {})
    cfg.pop("owner_user_id", None)
    if not cfg.get("max_age_hours") and not cfg.get("posted_date"):
        cfg["max_age_hours"] = DEFAULT_MAX_AGE_HOURS
    return cfg


async def fire(schedule: ScrapeSchedule, db) -> dict:
    """Launch one schedule now, as its owner. Records the outcome on the row.
    Returns what it recorded."""
    from fastapi import HTTPException
    from app.api.routes.scraper import _launch_job
    from app.schemas import ScrapingJobCreate

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
            job = await _launch_job(ScrapingJobCreate(**config_for_run(schedule.config)),
                                    None, db, owner, interactive=False)
            schedule.last_job_id = str(job.job_id)
            result = {"status": "started", "detail": f"اسکرپ {str(job.job_id)[:8]} شروع شد"}
        except HTTPException as e:
            result = {"status": "failed", "detail": str(e.detail)}
        except Exception as e:
            result = {"status": "failed", "detail": f"{type(e).__name__}: {e}"[:200]}
    now = datetime.now(timezone.utc)
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
    while True:
        await tick()
        await asyncio.sleep(TICK_SECONDS)
