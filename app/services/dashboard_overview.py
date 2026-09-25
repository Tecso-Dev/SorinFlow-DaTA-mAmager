"""
The new panel's dashboard, as pure functions over rows the route fetched.

Same rule as crm_insights: never invent a number. A ratio with nothing under
it is None, and the page shows «—».

Days, weekdays and hours are Tehran's (fixed +03:30, the image has no tz
database). A naive timestamp is UTC: that is how sqlite stores func.now().
"""
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

TEHRAN = timezone(timedelta(hours=3, minutes=30), "Asia/Tehran")

# Lead.status values the panel writes (crm.VALID_LEAD_STATUSES), in the order
# a lead moves through them. qualified is an older name for «ready to visit».
FUNNEL: List[Tuple[str, str, Tuple[str, ...]]] = [
    ("new", "لید تازه", ("new", "contacted", "qualified", "visit", "contract_meeting", "closed", "rented")),
    ("contacted", "تماس گرفته‌شده", ("contacted", "qualified", "visit", "contract_meeting", "closed", "rented")),
    ("visit", "بازدید", ("visit", "contract_meeting", "closed", "rented")),
    ("contract_meeting", "جلسهٔ قرارداد", ("contract_meeting", "closed", "rented")),
    ("won", "قرارداد", ("closed", "rented")),
]
OPEN_STATUSES = ("new", "contacted", "qualified", "visit", "contract_meeting")
WON_STATUSES = ("closed", "rented")
DEAL_DONE = ("contract", "closed")

# Calls grid: Saturday first (the Iranian week), office hours 8..19.
GRID_HOURS = list(range(8, 20))


def tehran(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(TEHRAN)


def today_tehran(now: Optional[datetime] = None) -> date:
    return tehran(now or datetime.now(timezone.utc)).date()


def day_start_utc(day: date) -> datetime:
    return datetime.combine(day, datetime.min.time(), tzinfo=TEHRAN).astimezone(timezone.utc)


def pct_change(now: Optional[float], before: Optional[float]) -> Optional[float]:
    """Percent change, None when there is no base to compare against."""
    if now is None or not before:
        return None
    return round((now - before) / before * 100, 1)


def ratio(part: int, whole: int) -> Optional[float]:
    return round(part / whole * 100, 1) if whole else None


def funnel(status_counts: Dict[str, int]) -> List[Dict[str, Any]]:
    """Cumulative: a lead at «visit» has also been contacted, so each stage
    counts every lead that reached it. Rejected leads are left out."""
    return [{"key": key, "label": label,
             "count": sum(int(status_counts.get(s, 0) or 0) for s in reached)}
            for key, label, reached in FUNNEL]


def daily(stamps: Iterable[datetime], days: int, now: Optional[datetime] = None) -> Dict[str, int]:
    """Count per Tehran day (ISO date) over the last `days` days, zeros included."""
    end = today_tehran(now)
    out = {(end - timedelta(days=days - 1 - i)).isoformat(): 0 for i in range(days)}
    for ts in stamps:
        if ts is None:
            continue
        key = tehran(ts).date().isoformat()
        if key in out:
            out[key] += 1
    return out


def weekday_sat0(d: date) -> int:
    """0 = Saturday … 6 = Friday."""
    return (d.weekday() + 2) % 7


def call_grid(stamps: Iterable[datetime]) -> List[List[int]]:
    grid = [[0] * len(GRID_HOURS) for _ in range(7)]
    for ts in stamps:
        if ts is None:
            continue
        t = tehran(ts)
        if t.hour in GRID_HOURS:
            grid[weekday_sat0(t.date())][t.hour - GRID_HOURS[0]] += 1
    return grid


def outcome_code(detail: Optional[str], labels: Dict[str, str]) -> Optional[str]:
    """The call's outcome from its activity line («پاسخ داد — note»)."""
    head = (detail or "").split(" — ")[0].strip()
    return {v: k for k, v in labels.items()}.get(head)


def status_reached(detail: Optional[str]) -> Optional[str]:
    """The status a status_change line moved to: «وضعیت به «closed» تغییر کرد»."""
    d = detail or ""
    if "«" in d and "»" in d:
        return d.split("«", 1)[1].split("»", 1)[0].strip() or None
    return None


def team(people: List[Dict[str, Any]], calls: Iterable[Tuple[str, Optional[str]]],
         wins: Iterable[str], visits: Iterable[str], labels: Dict[str, str]) -> List[Dict[str, Any]]:
    """One row per person: calls, answered share, visits, won leads.

    `people` are {name, role, presence}; activity is matched by the display
    name it was written under (crm_activity_log.actor).
    """
    rows = {p["name"]: {**p, "calls": 0, "answered": 0, "visits": 0, "won": 0} for p in people}
    for name, detail in calls:
        if name in rows:
            rows[name]["calls"] += 1
            if outcome_code(detail, labels) in ("answered", "visit", "callback"):
                rows[name]["answered"] += 1
    for name in visits:
        if name in rows:
            rows[name]["visits"] += 1
    for name in wins:
        if name in rows:
            rows[name]["won"] += 1
    out = list(rows.values())
    for r in out:
        r["answer_rate"] = ratio(r["answered"], r["calls"])
    out.sort(key=lambda r: (-r["won"], -r["visits"], -r["calls"], r["name"]))
    return out


def deals_by_month(rows: Iterable[Tuple[datetime, Optional[str]]]) -> List[Dict[str, Any]]:
    """Done deals per Tehran day and type; the page groups them into Jalali
    months with the browser's own calendar, so no Jalali code lives here."""
    agg: Dict[Tuple[str, str], int] = {}
    for when, kind in rows:
        if when is None:
            continue
        key = (tehran(when).date().isoformat(), (kind or "buy").strip() or "buy")
        agg[key] = agg.get(key, 0) + 1
    return [{"date": d, "type": k, "count": c} for (d, k), c in sorted(agg.items())]


def jalali_month_start(day: date) -> date:
    """The Gregorian date the Jalali month containing `day` began on."""
    from app.services.dpa_service import to_jalali
    d = day
    while not to_jalali(datetime.combine(d, datetime.min.time())).endswith("/01"):
        d -= timedelta(days=1)
    return d


def sources(pairs: Iterable[Tuple[Optional[str], int]]) -> List[Dict[str, Any]]:
    """Customers by where they came from (crm_customers.source), largest first."""
    agg: Dict[str, int] = {}
    for src, n in pairs:
        key = (src or "").strip() or "unknown"
        agg[key] = agg.get(key, 0) + int(n or 0)
    return [{"key": k, "count": c} for k, c in sorted(agg.items(), key=lambda kv: -kv[1]) if c]


TARGET_KEY = "dashboard.monthly_target"


def parse_target(raw: Optional[str]) -> Dict[str, Optional[int]]:
    import json
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        data = {}
    out: Dict[str, Optional[int]] = {}
    for k in ("deals", "commission"):
        v = data.get(k) if isinstance(data, dict) else None
        out[k] = int(v) if isinstance(v, (int, float)) and v > 0 else None
    return out
