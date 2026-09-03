"""
CRM insights — the aggregations behind the «هوش تصویری» page.

Split from the route on purpose. Everything that decides *meaning* — what
counts as a stalled lead, which statuses belong to which stage of the funnel,
how a conversion rate is computed when the denominator is zero — is a pure
function here, testable without a database. The route does the querying and
hands rows over.

The rule this file follows throughout: **never invent a number.** Where the
data cannot answer a question, the answer is None and the page says so,
because a chart that quietly renders zero is indistinguishable from a chart
reporting a real zero, and one of those is a lie.
"""
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence

# ── The funnel ──────────────────────────────────────────────────────────────
#
# Lead.status has no enum behind it — it is a free VARCHAR, and the panel has
# written at least "new", "contacted" and "qualified" into it. So the stages
# are a mapping, not a schema, and anything unrecognised is surfaced under its
# own name rather than dropped. A lead in a status nobody remembers creating
# is exactly the kind of thing this page exists to show.
FUNNEL_STAGES: List[tuple] = [
    ("new",        "جدید"),
    ("contacted",  "تماس گرفته‌شده"),
    ("qualified",  "واجد شرایط"),
    ("negotiating", "در حال مذاکره"),
    ("won",        "موفق"),
    ("lost",       "از دست رفته"),
]
_STAGE_LABELS = dict(FUNNEL_STAGES)

# A lead sitting in one of these has not been worked yet. Used for the
# «stalled» count — a lead that reached «won» or «lost» a year ago is
# finished, not neglected, and counting it as stale would bury the ones that
# actually need a call.
OPEN_STAGES = ("new", "contacted", "qualified", "negotiating")

STALE_AFTER_DAYS = 7


def funnel(status_counts: Dict[str, int]) -> List[Dict[str, Any]]:
    """Ordered funnel stages, with anything unrecognised appended.

    Order matters — a funnel drawn in dictionary order is not a funnel — so
    the known stages come first in their real sequence, then the strays.
    """
    known = []
    for key, label in FUNNEL_STAGES:
        known.append({"key": key, "label": label,
                      "count": int(status_counts.get(key, 0) or 0)})

    strays = []
    for key, count in status_counts.items():
        if key in _STAGE_LABELS or not count:
            continue
        strays.append({"key": key or "—",
                       "label": key or "بدون وضعیت",
                       "count": int(count), "unexpected": True})
    strays.sort(key=lambda s: -s["count"])
    return known + strays


def conversion_rate(won: int, total: int) -> Optional[float]:
    """Won as a share of everything that entered the funnel.

    None, not 0.0, when nothing has entered. «0٪ conversion» reads as a
    business that is failing; «—» reads as a question that cannot be answered
    yet, which is the truth on an empty CRM.
    """
    if not total or total <= 0:
        return None
    return round(won / total * 100, 1)


def stalled_leads(rows: Iterable, *, now: Optional[datetime] = None,
                  days: int = STALE_AFTER_DAYS) -> List[Dict[str, Any]]:
    """Open leads whose last update is older than `days`, oldest first.

    Reads updated_at and falls back to created_at: a lead nobody has ever
    touched has no update to be old, and it is the most neglected of all.
    """
    now = now or datetime.now()
    out = []
    for r in rows:
        status = (getattr(r, "status", None) or "new").strip()
        if status not in OPEN_STAGES:
            continue
        seen = getattr(r, "updated_at", None) or getattr(r, "created_at", None)
        if not seen:
            continue
        # Rows can arrive tz-aware from Postgres and naive from SQLite; a
        # comparison across the two raises, and a raised exception here would
        # take down the whole page for a formatting detail.
        if getattr(seen, "tzinfo", None) is not None:
            seen = seen.replace(tzinfo=None)
        idle = (now - seen).days
        if idle < days:
            continue
        out.append({
            "id": getattr(r, "id", None),
            "seller_name": getattr(r, "seller_name", None),
            "phone_number": getattr(r, "phone_number", None),
            "city_name": getattr(r, "city_name", None),
            "status": status,
            "status_label": _STAGE_LABELS.get(status, status),
            "idle_days": idle,
        })
    out.sort(key=lambda x: -x["idle_days"])
    return out


def daily_series(rows: Iterable, *, days: int = 30,
                 today: Optional[date] = None) -> List[Dict[str, Any]]:
    """One entry per day for the last `days`, including days with nothing.

    Gap-filled deliberately. A line chart drawn only from days that have data
    puts an even spacing between points that are a week apart, which turns a
    quiet fortnight into a smooth climb.
    """
    today = today or date.today()
    start = today - timedelta(days=days - 1)

    counts: Counter = Counter()
    for r in rows:
        d = r if isinstance(r, date) and not isinstance(r, datetime) else None
        if d is None:
            stamp = getattr(r, "created_at", None) or (r if isinstance(r, datetime) else None)
            if not stamp:
                continue
            d = stamp.date()
        if start <= d <= today:
            counts[d] += 1

    return [{"date": (start + timedelta(days=i)).isoformat(),
             "count": counts.get(start + timedelta(days=i), 0)}
            for i in range(days)]


def top_buckets(pairs: Iterable, *, limit: int = 6,
                other_label: str = "سایر") -> List[Dict[str, Any]]:
    """The `limit` biggest buckets, with the remainder folded into one.

    Without the fold, a doughnut of 174 cities is a grey ring. With it, the
    total still adds up — which matters, because a chart whose slices do not
    sum to the headline number is worse than no chart.
    """
    counted = [(str(name or "—"), int(count or 0)) for name, count in pairs]
    counted = [c for c in counted if c[1] > 0]
    counted.sort(key=lambda kv: -kv[1])

    head = counted[:limit]
    tail = counted[limit:]
    out = [{"label": n, "count": c} for n, c in head]
    if tail:
        out.append({"label": other_label,
                    "count": sum(c for _, c in tail),
                    "is_other": True})
    return out


def agent_scoreboard(rows: Iterable) -> List[Dict[str, Any]]:
    """Per-agent totals from the daily performance rows, best closer first.

    Summed rather than averaged: an agent who logged four days and closed
    three deals is ahead of one who logged twenty and closed three, and an
    average per day would say the opposite.
    """
    agg: Dict[str, Dict[str, int]] = {}
    for r in rows:
        name = (getattr(r, "agent_name", None) or "").strip()
        if not name:
            continue
        a = agg.setdefault(name, {"agent": name, "days": 0, "new_files": 0,
                                  "showings": 0, "offers": 0, "closed": 0})
        a["days"] += 1
        a["new_files"] += int(getattr(r, "new_files", 0) or 0)
        a["showings"] += int(getattr(r, "showings_count", 0) or 0)
        a["offers"] += int(getattr(r, "offers_count", 0) or 0)
        a["closed"] += int(getattr(r, "closed_count", 0) or 0)

    out = list(agg.values())
    for a in out:
        # Showings per close: how many viewings it takes this agent to land
        # one. None when they have closed nothing — dividing by zero would
        # either crash or, worse, be silently clamped to something flattering.
        a["showings_per_close"] = (
            round(a["showings"] / a["closed"], 1) if a["closed"] else None)
    out.sort(key=lambda a: (-a["closed"], -a["offers"], -a["showings"]))
    return out


def deal_totals(rows: Iterable) -> Dict[str, Any]:
    """Money in the pipeline versus money actually banked."""
    total = 0
    closed_amount = 0
    commission_due = 0
    commission_paid = 0
    open_count = 0
    closed_count = 0

    for r in rows:
        amount = int(getattr(r, "amount", 0) or 0)
        commission = int(getattr(r, "commission", 0) or 0)
        status = (getattr(r, "status", None) or "").strip().lower()
        total += amount
        if status in ("closed", "won", "done"):
            closed_count += 1
            closed_amount += amount
            if getattr(r, "commission_paid", False):
                commission_paid += commission
            else:
                commission_due += commission
        else:
            open_count += 1

    return {
        "deal_count": open_count + closed_count,
        "open_count": open_count,
        "closed_count": closed_count,
        "total_amount": total,
        "closed_amount": closed_amount,
        "commission_paid": commission_paid,
        "commission_due": commission_due,
    }


def coverage(total: int, with_value: int) -> Optional[float]:
    """What share of records actually carry a field, 0-100.

    The page leans on this instead of hiding it: a funnel built from leads
    that are 40% missing a phone number is worth looking at, but only if the
    40% is on screen next to it.
    """
    if not total or total <= 0:
        return None
    return round(with_value / total * 100, 1)
