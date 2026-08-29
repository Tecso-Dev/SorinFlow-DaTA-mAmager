"""
پایش سامانه — the monitoring screen's data.

Everything here is computed from what the box already knows: the metrics
registry, the database, Redis and /proc. Nothing leaves the server, which is
the only design that works from Iran and also the cheapest one.

Separate from /metrics on purpose. That endpoint speaks Prometheus text for a
scraper; this one speaks JSON shaped for the panel, so the screen does not have
to parse an exposition format in the browser.
"""
import os
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from loguru import logger
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import metrics as mx
from app.config import get_settings
from app.database import get_db, get_redis
from app.models.property import Property
from app.models.lead import Lead
from app.models.scraping_job import ScrapingJob

router = APIRouter()
settings = get_settings()
_STARTED = time.time()


def _read_container_limits() -> dict:
    """Memory and CPU as the container sees them.

    cgroup v2 first (what k3s on a modern kernel uses), then v1. Read straight
    from the filesystem: no privileges, no extra dependency, and it reports the
    container's limit rather than the host's total — which is the number that
    decides whether this pod gets killed.
    """
    out = {}
    try:
        # ── cgroup v2 ──
        if os.path.exists("/sys/fs/cgroup/memory.current"):
            with open("/sys/fs/cgroup/memory.current") as fh:
                out["memory_used_bytes"] = int(fh.read().strip())
            try:
                with open("/sys/fs/cgroup/memory.max") as fh:
                    raw = fh.read().strip()
                out["memory_limit_bytes"] = None if raw == "max" else int(raw)
            except FileNotFoundError:
                pass
        # ── cgroup v1 ──
        elif os.path.exists("/sys/fs/cgroup/memory/memory.usage_in_bytes"):
            with open("/sys/fs/cgroup/memory/memory.usage_in_bytes") as fh:
                out["memory_used_bytes"] = int(fh.read().strip())
            with open("/sys/fs/cgroup/memory/memory.limit_in_bytes") as fh:
                limit = int(fh.read().strip())
            # v1 reports "no limit" as a number near 2^63; treat it as unlimited
            out["memory_limit_bytes"] = None if limit > (1 << 62) else limit
    except Exception:
        pass

    try:
        with open("/proc/loadavg") as fh:
            out["load_1m"] = float(fh.read().split()[0])
    except Exception:
        pass
    return out


@router.get("/overview")
async def monitoring_overview(db: AsyncSession = Depends(get_db),
                              _=Depends(lambda: None)):
    """Health, resources and throughput in one call.

    One call rather than five because the screen refreshes on a timer and five
    round trips per tick on a single-replica box is its own load problem.
    """
    import shutil

    services = {}

    # ── Postgres ──
    with mx.Timer("postgres"):
        pg_start = time.perf_counter()
        await db.execute(text("SELECT 1"))
    services["postgres"] = {
        "up": True,
        "latency_ms": round((time.perf_counter() - pg_start) * 1000, 2),
    }

    # ── Redis ──
    try:
        with mx.Timer("redis"):
            r_start = time.perf_counter()
            r = await get_redis()
            await r.ping()
        services["redis"] = {
            "up": True,
            "latency_ms": round((time.perf_counter() - r_start) * 1000, 2),
        }
    except Exception as e:
        # Redis being down degrades verification codes and login throttling; it
        # does not stop the site, so it is reported rather than raised.
        services["redis"] = {"up": False, "error": str(e)[:120]}

    # ── scraper jobs by status ──
    rows = (await db.execute(
        select(ScrapingJob.status, func.count(ScrapingJob.id))
        .group_by(ScrapingJob.status))).all()
    jobs = {status or "unknown": count for status, count in rows}
    for status, count in jobs.items():
        mx.scrape_jobs.labels(status).set(count)

    # A job that has been "running" for hours is the failure that hides: the
    # dashboard shows it as busy and nobody looks again.
    stale_cutoff = datetime.now(timezone.utc) - timedelta(hours=6)
    stale = (await db.execute(
        select(func.count(ScrapingJob.id)).where(
            ScrapingJob.status == "running",
            ScrapingJob.started_at < stale_cutoff))).scalar() or 0

    last_done = (await db.execute(
        select(func.max(ScrapingJob.completed_at)).where(
            ScrapingJob.status == "completed"))).scalar()

    # ── storage ──
    storage = {}
    try:
        usage = shutil.disk_usage(settings.images_path)
        storage = {
            "total_bytes": usage.total,
            "free_bytes": usage.free,
            "used_percent": round((usage.total - usage.free) / usage.total * 100, 1),
        }
        mx.disk_free.set(usage.free)
    except Exception:
        pass

    # ── business counters ──
    props = (await db.execute(select(func.count(Property.id)))).scalar() or 0
    leads = (await db.execute(select(func.count(Lead.id)))).scalar() or 0
    mx.properties_total.set(props)
    mx.leads_total.set(leads)

    return {
        "uptime_seconds": int(time.time() - _STARTED),
        "services": services,
        "scraper": {
            "jobs_by_status": jobs,
            "stale_running": stale,
            "last_completed_at": last_done.isoformat() if last_done else None,
        },
        "resources": {**_read_container_limits(), "storage": storage},
        "totals": {"properties": props, "leads": leads},
        "metrics_enabled": bool(settings.metrics_token),
    }
