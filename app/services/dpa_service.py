"""
Automatic DPA scoring.

CRM events (a lead marked as contacted, a visit logged, a new file
registered…) bump the agent's daily activity counters, so the DPA score
builds itself instead of being typed in by hand.
"""
from datetime import datetime
from typing import Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.crm_models import DailyPerformance

# CRM lead status → activity key that earns points
LEAD_STATUS_ACTIVITY = {
    "contacted": "call",           # تماس با مشتری — ۲ امتیاز
    "visit": "showing",            # پرزنت / بازدید ملک — ۱۰ امتیاز
    "contract_meeting": "meeting", # نشست و تنظیم قرارداد — ۲۰ امتیاز
}


def to_jalali(g: datetime) -> str:
    """A datetime as a Jalali YYYY/MM/DD string (pure arithmetic, no deps)."""
    gy, gm, gd = g.year, g.month, g.day
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy2 = gy - 1600
    gm2 = gm - 1
    gd2 = gd - 1
    g_day_no = 365 * gy2 + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400
    g_day_no += g_d_m[gm2] + gd2
    if gm > 2 and ((gy % 4 == 0 and gy % 100 != 0) or gy % 400 == 0):
        g_day_no += 1
    j_day_no = g_day_no - 79
    j_np = j_day_no // 12053
    j_day_no %= 12053
    jy = 979 + 33 * j_np + 4 * (j_day_no // 1461)
    j_day_no %= 1461
    if j_day_no >= 366:
        jy += (j_day_no - 1) // 365
        j_day_no = (j_day_no - 1) % 365
    j_all = [31, 31, 31, 31, 31, 31, 30, 30, 30, 30, 30, 29]
    # Stop at Esfand (index 11) instead of running past it: in a Jalali leap
    # year Esfand has 30 days, and letting the loop consume the table's 29
    # rolled the date over into a nonexistent month 13.
    jm = 0
    while jm < 11 and j_day_no >= j_all[jm]:
        j_day_no -= j_all[jm]
        jm += 1
    return f"{jy}/{jm + 1:02d}/{j_day_no + 1:02d}"


def today_jalali() -> str:
    """Today as a Jalali YYYY/MM/DD string."""
    return to_jalali(datetime.now())


async def record_activity(
    db: AsyncSession,
    agent_name: Optional[str],
    activity: str,
    delta: int = 1,
) -> None:
    """Add `delta` units of `activity` to the agent's DPA row for today.

    Creates the row on first activity of the day. Never raises — scoring
    must not break the CRM action that triggered it.
    """
    if not agent_name or activity not in DailyPerformance.ACTIVITY_POINTS:
        return
    try:
        date_j = today_jalali()
        row = (await db.execute(
            select(DailyPerformance).where(
                DailyPerformance.agent_name == agent_name,
                DailyPerformance.date_jalali == date_j,
            )
        )).scalars().first()

        if row is None:
            row = DailyPerformance(
                agent_name=agent_name, date_jalali=date_j,
                auto_activities={}, activities={}, base_tasks={},
            )
            db.add(row)

        auto = dict(row.auto_activities or {})
        auto[activity] = int(auto.get(activity, 0) or 0) + delta
        if auto[activity] < 0:
            auto[activity] = 0
        row.auto_activities = auto          # reassign so SQLAlchemy sees the change
        row.updated_at = datetime.now()

        # keep the legacy headline counters in sync for the report cards
        if activity == "showing":
            row.showings_count = (row.showings_count or 0) + delta
        elif activity == "new_file":
            row.new_files = (row.new_files or 0) + delta
        elif activity == "close":
            row.closed_count = (row.closed_count or 0) + delta

        logger.info(f"[DPA] {agent_name}: +{delta} {activity} ({date_j})")
    except Exception as e:
        logger.warning(f"[DPA] failed to record {activity} for {agent_name}: {e}")


async def record_lead_status(db: AsyncSession, agent_name: Optional[str], status: str) -> None:
    """Map a lead's new CRM status onto an activity, if it earns points."""
    activity = LEAD_STATUS_ACTIVITY.get(status)
    if activity:
        await record_activity(db, agent_name, activity)
