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
from typing import Optional
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
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

# Outbound reachability is cached: it makes a real request, and the live view
# polls every five seconds. Probing an external host twelve times a minute is
# load on someone else's server and looks like scanning from ours.
_REACH_TTL = 60.0
_reach_cache: dict = {"at": 0.0, "value": None}


def _system_info() -> dict:
    """What this process is running on. Read once — none of it changes."""
    import platform
    info = {
        "os": f"{platform.system()} {platform.release()}",
        "python": platform.python_version(),
        "arch": platform.machine(),
        "hostname": platform.node(),
    }
    # This is the CONTAINER's OS — the Playwright image's Ubuntu — not the
    # host's. The panel labelled it «سیستم‌عامل» and people read it as the
    # server, then wondered why a box running Ubuntu 26.04 reported 22.04.
    pretty = _read_first("/etc/os-release")
    if pretty:
        for line in pretty.splitlines():
            if line.startswith("PRETTY_NAME="):
                info["distro"] = line.split("=", 1)[1].strip().strip('"')
                info["distro_is_container"] = True
                break
    up = _read_first("/proc/uptime")
    if up:
        try:
            info["host_uptime_seconds"] = int(float(up.split()[0]))
        except ValueError:
            pass
    return info


async def _check_reachability() -> dict:
    """Can the scraper still reach Divar?

    This is the one outbound check worth making. The scraper's whole job
    depends on it, and from an Iranian network a host can become unreachable
    without anything on this box changing — which otherwise shows up as a
    scrape that mysteriously returns nothing.

    Deliberately not a generic ping dashboard: probing Google from here always
    fails under sanctions, and a permanently red tile teaches people to ignore
    the panel.
    """
    now = time.time()
    if _reach_cache["value"] is not None and now - _reach_cache["at"] < _REACH_TTL:
        return _reach_cache["value"]

    import httpx
    result = {}
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=6.0, follow_redirects=False) as client:
            r = await client.head("https://divar.ir/", headers={"User-Agent": "SorinFlow-HealthCheck"})
        # This is full connection setup — DNS, TCP, TLS and a request — on a
        # cold client, not a round trip. The staged test is what to read when
        # the number looks wrong.
        result = {"divar": {"up": r.status_code < 500,
                            "status": r.status_code,
                            "setup_ms": round((time.perf_counter() - started) * 1000, 1)}}
    except Exception as e:
        result = {"divar": {"up": False, "error": type(e).__name__,
                            "latency_ms": round((time.perf_counter() - started) * 1000, 1)}}
    _reach_cache.update({"at": now, "value": result})
    return result


def _read_first(*paths) -> Optional[str]:
    """First readable path wins. cgroup v1 and v2 keep the same facts in
    different places, and a container can be under either."""
    for path in paths:
        try:
            with open(path) as fh:
                return fh.read().strip()
        except (OSError, ValueError):
            continue
    return None


def _read_meminfo() -> dict:
    """/proc/meminfo — the host's view. Used for swap, which cgroup only
    reports for the container's own usage, and as a fallback for total RAM."""
    out = {}
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                key, _, rest = line.partition(":")
                parts = rest.split()
                if parts:
                    out[key] = int(parts[0]) * 1024      # kB -> bytes
    except OSError:
        pass
    return out


def _read_resources() -> dict:
    """CPU, RAM and swap, read from cgroup and /proc without extra privileges.

    cgroup first, because the container's limit is what decides whether this
    pod gets killed — the host having 8GB free is no comfort when the cgroup
    cap is 512MB. /proc/meminfo fills in swap and the host totals.

    Every value is optional: none of this exists on macOS, so a developer sees
    empty tiles rather than a stack trace.
    """
    out = {"cpu_count": os.cpu_count()}

    # ── memory ──
    mem_cur = _read_first("/sys/fs/cgroup/memory.current",
                          "/sys/fs/cgroup/memory/memory.usage_in_bytes")
    if mem_cur and mem_cur.isdigit():
        out["memory_used_bytes"] = int(mem_cur)

    mem_max = _read_first("/sys/fs/cgroup/memory.max",
                          "/sys/fs/cgroup/memory/memory.limit_in_bytes")
    if mem_max and mem_max != "max" and mem_max.isdigit():
        limit = int(mem_max)
        # cgroup v1 spells "no limit" as a number near 2^63
        out["memory_limit_bytes"] = None if limit > (1 << 62) else limit

    info = _read_meminfo()
    if info:
        out["host_memory_total_bytes"] = info.get("MemTotal")
        avail = info.get("MemAvailable")
        if avail is not None and info.get("MemTotal"):
            out["host_memory_used_bytes"] = info["MemTotal"] - avail
        # ── swap ── bootstrap.sh adds 4GB precisely because RAM is tight here,
        # so swap filling is a real signal and not a curiosity
        total, free = info.get("SwapTotal"), info.get("SwapFree")
        if total:
            out["swap_total_bytes"] = total
            out["swap_used_bytes"] = total - (free or 0)
            out["swap_used_percent"] = round((total - (free or 0)) / total * 100, 1)
        # if the cgroup gave no limit, the host total is the honest ceiling
        out.setdefault("memory_limit_bytes", info.get("MemTotal"))
        out.setdefault("memory_used_bytes", out.get("host_memory_used_bytes"))

    # ── cpu ── cumulative microseconds; the caller differences two samples
    cpu = _read_first("/sys/fs/cgroup/cpu.stat")
    if cpu:
        for line in cpu.splitlines():
            if line.startswith("usage_usec"):
                out["cpu_usage_usec"] = int(line.split()[1])
                break
    else:
        v1 = _read_first("/sys/fs/cgroup/cpuacct/cpuacct.usage")   # nanoseconds
        if v1 and v1.isdigit():
            out["cpu_usage_usec"] = int(v1) // 1000

    quota = _read_first("/sys/fs/cgroup/cpu.max")
    if quota and quota != "max":
        try:
            q, period = quota.split()
            if q != "max":
                out["cpu_limit_cores"] = round(int(q) / int(period), 2)
        except ValueError:
            pass

    load = _read_first("/proc/loadavg")
    if load:
        try:
            out["load_1m"], out["load_5m"], out["load_15m"] = (
                float(x) for x in load.split()[:3])
        except ValueError:
            pass
    return out


# kept for callers that only wanted the two memory facts
def _read_container_limits() -> dict:
    return _read_resources()


# Fixed targets. This endpoint must never take a host from the caller: an
# admin-triggered prober that accepts arbitrary addresses is an SSRF tool and a
# port scanner wearing a dashboard button, and it would run from inside the
# cluster where it can see things the internet cannot.
_PROBE_TARGETS = {
    "divar": ("divar.ir", 443),
}


async def _probe_stages(host: str, port: int) -> dict:
    """DNS, TCP, TLS and HTTP as four separate timed stages.

    One boolean says "Divar is down" and leaves you guessing. These four say
    which layer failed, which is the difference between a DNS problem on the
    box, a blocked route, a TLS interception, and Divar actually being down —
    four different fixes.
    """
    import asyncio
    import socket
    import ssl

    stages, ip = [], None

    def add(name, ok, ms, detail=""):
        stages.append({"stage": name, "ok": ok, "ms": round(ms * 1000, 1), "detail": detail})

    loop = asyncio.get_running_loop()

    # ── DNS ──
    t = time.perf_counter()
    try:
        infos = await asyncio.wait_for(
            loop.getaddrinfo(host, port, proto=socket.IPPROTO_TCP), timeout=5)
        ip = infos[0][4][0]
        add("DNS", True, time.perf_counter() - t, ip)
    except Exception as e:
        add("DNS", False, time.perf_counter() - t, type(e).__name__)
        return {"host": host, "port": port, "stages": stages, "ok": False}

    # ── TCP ── three connects, best of. A single sample catches whatever the
    # scheduler and the first-packet path happened to be doing; the minimum is
    # the closest thing to the real round trip, and it is the only figure here
    # that means "network latency" in the way people expect from ping.
    best, tcp_error = None, None
    for _ in range(3):
        t = time.perf_counter()
        writer = None
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=6)
            elapsed = time.perf_counter() - t
            best = elapsed if best is None else min(best, elapsed)
        except Exception as e:
            tcp_error = type(e).__name__
            break
        finally:
            if writer:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
    if best is None:
        add("TCP", False, 0, tcp_error or "failed")
        return {"host": host, "port": port, "ip": ip, "stages": stages, "ok": False}
    add("TCP", True, best, f"{ip}:{port} — بهترین از ۳")

    # ── TLS ── the certificate is worth seeing: an unexpected issuer is what
    # interception looks like, and an expiring one explains a sudden failure
    t = time.perf_counter()
    tls_writer = None
    try:
        ctx = ssl.create_default_context()
        _, tls_writer = await asyncio.wait_for(
            asyncio.open_connection(ip, port, ssl=ctx, server_hostname=host), timeout=8)
        cert = tls_writer.get_extra_info("peercert") or {}
        issuer = dict(x[0] for x in cert.get("issuer", ())).get("organizationName", "?")
        add("TLS", True, time.perf_counter() - t,
            f"{issuer} · تا {cert.get('notAfter', '?')}")
    except Exception as e:
        add("TLS", False, time.perf_counter() - t, f"{type(e).__name__}: {str(e)[:60]}")
    finally:
        if tls_writer:
            tls_writer.close()
            try:
                await tls_writer.wait_closed()
            except Exception:
                pass

    # ── HTTP ── HEAD, so this is time-to-response and not time-to-download.
    # The first version issued a GET and timed the whole homepage arriving,
    # which reported ~1400ms on a link whose actual round trip was under 2ms
    # and made a healthy connection look broken.
    import httpx
    t = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=False) as client:
            r = await client.head(f"https://{host}/",
                                  headers={"User-Agent": "SorinFlow-HealthCheck"})
        add("HTTP", r.status_code < 500, time.perf_counter() - t,
            f"HTTP {r.status_code} (بدون بدنه)")
    except Exception as e:
        add("HTTP", False, time.perf_counter() - t, type(e).__name__)

    rtt = next((st["ms"] for st in stages if st["stage"] == "TCP"), None)
    return {"host": host, "port": port, "ip": ip, "stages": stages,
            # the headline number: network round trip, not the sum of every
            # setup cost, which is what a total would be
            "rtt_ms": rtt,
            "ok": all(st["ok"] for st in stages)}


@router.post("/connectivity-test")
async def connectivity_test(target: str = "divar"):
    """Run a real connection test on demand, stage by stage.

    POST because it does outbound work rather than reading state, and because a
    GET would be re-run by every refresh and preload. The target is a key into
    a fixed table, never a host — see _PROBE_TARGETS.
    """
    if target not in _PROBE_TARGETS:
        raise HTTPException(status_code=400,
                            detail=f"unknown target; choose one of {sorted(_PROBE_TARGETS)}")
    host, port = _PROBE_TARGETS[target]
    started = time.perf_counter()
    result = await _probe_stages(host, port)
    result["total_ms"] = round((time.perf_counter() - started) * 1000, 1)
    result["target"] = target
    # a fresh manual test should update the cached tile too
    _reach_cache.update({"at": time.time(),
                         "value": {"divar": {"up": result["ok"],
                                             "latency_ms": result.get("rtt_ms")
                                                           or result["total_ms"]}}})
    logger.info(f"[probe] {target}: {'ok' if result['ok'] else 'FAILED'} "
                f"in {result['total_ms']}ms")
    return result


@router.get("/live")
async def monitoring_live():
    """A cheap snapshot for the live view, polled every few seconds.

    Deliberately touches nothing but the in-process registry and /proc — no
    database, no Redis. The overview does the expensive work on a slower timer;
    this one has to be safe to call often on a single-replica box.
    """
    import shutil
    import time as _t

    snap = mx.snapshot()
    snap["ts"] = _t.time()
    snap["uptime_seconds"] = int(_t.time() - _STARTED)
    res = _read_resources()
    snap.update(res)
    # process RSS is the fallback when no cgroup is readable
    snap["memory_used_bytes"] = res.get("memory_used_bytes") or snap.get("rss_bytes")
    try:
        u = shutil.disk_usage(settings.images_path)
        snap["disk_used_percent"] = round((u.total - u.free) / u.total * 100, 1)
    except Exception:
        pass
    return snap


@router.get("/overview")
async def monitoring_overview(db: AsyncSession = Depends(get_db)):
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

    # Scraper liveness is a different question from the API's: the process can
    # be up for days while the scraper has not completed a job since Tuesday.
    running = (await db.execute(
        select(func.count(ScrapingJob.id)).where(
            ScrapingJob.status == "running"))).scalar() or 0

    return {
        "uptime_seconds": int(time.time() - _STARTED),
        "system": _system_info(),
        "network": {
            "server_ip": settings.server_ip,
            "domain": settings.domain,
            "reachability": await _check_reachability(),
        },
        "scraper_running": running,
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


# ── Divar session health ────────────────────────────────────────────────────
#
# The scraper rotates between Divar accounts, and its rotation pool is exactly
# "Cookie rows where is_valid is true". So a session that died without anyone
# noticing does not merely sit there: rotation keeps handing work to a dead
# account, and the run fails on it every cycle until someone looks.
#
# The existing /api/auth/status reports "Session active and verified with
# Divar" without ever contacting Divar — it reads the is_valid flag and the
# expiry date out of the database. That is fine as a cheap listing and useless
# as an answer to "does this still work", which is the question worth asking.

# Divar's own API, used because it gives a straight answer: 403 "RBAC: access
# denied" with no session, 200 with one. The HTML pages cannot be used for this
# — divar.ir/my-divar returns 200 to a logged-out client and does the redirect
# in JavaScript, so an HTTP check against it says "fine" no matter what.
_SESSION_PROBE_URL = "https://api.divar.ir/v8/user/profile"


# Older than this and the stored answer is no longer worth presenting as
# current. Twice the default verifier interval, so a single missed round does
# not make a healthy session look doubtful.
STALE_AFTER_MIN = 25


def _cookie_state(row, now: datetime) -> tuple:
    """(state, human note) for one stored session, from the database alone."""
    if not row.is_valid:
        return "expired", "باطل شده — نیاز به ورود مجدد"
    if row.expires_at:
        exp = row.expires_at.replace(tzinfo=None) if row.expires_at.tzinfo else row.expires_at
        if exp < now:
            return "expired", "تاریخ انقضا گذشته — نیاز به ورود مجدد"
        if exp - now < timedelta(days=3):
            left = exp - now
            return "expiring", f"{int(left.total_seconds() // 3600)} ساعت تا انقضا"
    return "active", "فعال"


@router.get("/cookies")
async def cookie_health(db: AsyncSession = Depends(get_db)):
    """Every stored Divar account and the state of its session.

    Cheap: database only, safe to poll. The real check is a separate button,
    because it costs an outbound request to someone else's server.
    """
    from app.models.cookie import Cookie

    rows = (await db.execute(
        select(Cookie).order_by(Cookie.updated_at.desc().nullslast())
    )).scalars().all()

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    items, seen = [], set()
    for r in rows:
        if not r.phone_number or r.phone_number in seen:
            continue                      # one entry per number, newest wins
        seen.add(r.phone_number)
        state, note = _cookie_state(r, now)
        updated = r.updated_at or r.created_at
        age_h = None
        if updated:
            u = updated.replace(tzinfo=None) if updated.tzinfo else updated
            age_h = round((now - u).total_seconds() / 3600, 1)

        # Seconds until the session cookie expires. The browser counts down
        # from this rather than the server re-sending a number every second.
        seconds_left = None
        if r.expires_at:
            e = r.expires_at.replace(tzinfo=None) if r.expires_at.tzinfo else r.expires_at
            seconds_left = int((e - now).total_seconds())

        # How long ago Divar was actually asked — as opposed to when we last
        # wrote the row, which is what age_hours measures and what used to be
        # presented as though it meant the same thing.
        checked_age_min, verified = None, False
        if r.last_checked_at:
            c = r.last_checked_at.replace(tzinfo=None) if r.last_checked_at.tzinfo else r.last_checked_at
            checked_age_min = round((now - c).total_seconds() / 60, 1)
            verified = checked_age_min <= STALE_AFTER_MIN

        items.append({
            "id": r.id,
            "phone_number": r.phone_number,
            "state": state,
            "note": note,
            "is_valid": bool(r.is_valid),
            "expires_at": r.expires_at.isoformat() if r.expires_at else None,
            "updated_at": updated.isoformat() if updated else None,
            "age_hours": age_h,
            "expires_at": r.expires_at.isoformat() if r.expires_at else None,
            "seconds_left": seconds_left,
            "last_checked_at": r.last_checked_at.isoformat() if r.last_checked_at else None,
            "checked_age_minutes": checked_age_min,
            # False means "nobody has asked Divar recently" — the panel shows
            # that differently from a confirmed answer, because a belief with
            # no date on it is what made the header contradict the table.
            "verified": verified,
            # rotation picks from exactly this set — see _load_rotation_pool
            "in_rotation": bool(r.is_valid),
        })

    usable = sum(1 for i in items if i["in_rotation"])
    return {
        "items": items,
        "total": len(items),
        "usable": usable,
        # One account cannot rotate: maybe_rotate_account needs two before it
        # will switch, so a single-account pool silently never rotates.
        "rotation_possible": usable >= 2,
        "rotate_every": getattr(settings, "cookie_rotate_every", 0) or 0,
        # So the panel can say how live "live" actually is.
        "check_every_minutes": int(getattr(settings, "divar_session_check_minutes", 0) or 0),
        "stale_after_minutes": STALE_AFTER_MIN,
    }


@router.post("/cookies/check")
async def cookie_check(phone: str, db: AsyncSession = Depends(get_db)):
    """Ask Divar whether this account's session still works.

    `phone` is a lookup key into our own table, never a host or a URL — the
    endpoint contacted is fixed in the service.

    The probe itself lives in app/services/divar_session so the background
    verifier can run exactly the same check; two copies would drift, and the
    one that drifted would be whichever nobody was watching.
    """
    from app.models.cookie import Cookie
    from app.services import divar_session

    row = (await db.execute(
        select(Cookie).where(Cookie.phone_number == phone)
        .order_by(Cookie.updated_at.desc().nullslast()).limit(1)
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="no stored session for that number")

    return await divar_session.check_and_record(db, row)
