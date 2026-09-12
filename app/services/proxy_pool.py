"""
The proxy pool: testing, choosing, and keeping it fresh.

What was here before this file: a proxies table, a panel to add rows, and a
test button that fetched divar.ir through each one. Three things were missing,
and together they meant the feature could not help:

  * PROXY_ENABLED could not be switched on from the cluster (the manifest
    never listed it), so a proxy sat tested and unused for days.
  * The test said «works» for any exit that reached Divar. The only proxy
    configured exits from a hosting provider in Reykjavik. For an Iranian
    classifieds site, a foreign datacenter IP is the least convincing thing a
    visitor can be — it passed the test and would have made things worse.
  * One proxy was chosen for the whole run, so every account exited from the
    same IP. Ten people, one address.

Now: every test also records WHERE the proxy exits and whether it is a
hosting ASN; a proxy is picked PER ACCOUNT and stays with that account, so an
account is always the same person at the same address; the pool is re-tested
on a schedule so a dead entry does not stay «working»; and a list can be
imported from a URL, so whatever source is chosen — paid or free — the
reachability test is what decides whether an entry is ever used.

On free lists, plainly: they are almost entirely foreign datacenter IPs, dead
within hours, shared with abusers. The Divar gate here will reject most of
them, which is the point of having it. What actually passes as a person on
Divar is an Iranian residential or mobile IP.
"""
import asyncio
import hashlib
from datetime import datetime, timezone
from typing import List, Optional

import httpx
from loguru import logger
from sqlalchemy import select

from app.config import get_settings
from app.database import async_session_maker
from app.models.proxy import Proxy

settings = get_settings()

DIVAR = "https://divar.ir"
# Exit lookup. Best-effort and rate-limited upstream (45/min); a failure here
# leaves the country unknown, never marks the proxy bad.
GEO = "http://ip-api.com/json/?fields=status,countryCode,query,hosting"
TIMEOUT = 25.0

# Preferred exits, in order. Anything else works but is scored below these.
PREFERRED_COUNTRIES = ("IR",)


async def probe(proxy: Proxy) -> dict:
    """Test one proxy against Divar and learn where it exits.

    Mutates the row (is_working, counts, timing, exit_*) but does not commit —
    the caller owns the transaction.
    """
    start = datetime.now()
    outcome = {"proxy_id": proxy.id, "address": f"{proxy.address}:{proxy.port}"}
    try:
        async with httpx.AsyncClient(proxy=proxy.url, timeout=TIMEOUT) as client:
            r = await client.get(DIVAR)
            elapsed = (datetime.now() - start).total_seconds()
            if r.status_code == 200:
                proxy.is_working = True
                proxy.success_count = (proxy.success_count or 0) + 1
                proxy.avg_response_time = elapsed
                outcome.update(success=True, response_time=elapsed)
            else:
                proxy.is_working = False
                proxy.fail_count = (proxy.fail_count or 0) + 1
                outcome.update(success=False, status_code=r.status_code,
                               message="Proxy returned non-200 status")
            # Where does it come out? Only worth asking if it reaches anything.
            try:
                g = await client.get(GEO, timeout=10.0)
                j = g.json() if g.status_code == 200 else {}
                if j.get("status") == "success":
                    proxy.exit_country = (j.get("countryCode") or "")[:2] or None
                    proxy.exit_ip = (j.get("query") or "")[:45] or None
                    proxy.is_hosting = bool(j.get("hosting"))
                    outcome.update(exit_country=proxy.exit_country,
                                   exit_ip=proxy.exit_ip, is_hosting=proxy.is_hosting)
            except Exception as ge:
                logger.debug(f"[proxy] exit lookup failed for {proxy.id}: {ge}")
    except Exception as e:
        proxy.is_working = False
        proxy.fail_count = (proxy.fail_count or 0) + 1
        outcome.update(success=False, error=str(e)[:200])
    proxy.last_checked = datetime.now(timezone.utc)
    return outcome


def _score(p: Proxy) -> tuple:
    """Higher is better. Iranian, non-hosting, fast, reliable."""
    return (
        1 if (p.exit_country or "") in PREFERRED_COUNTRIES else 0,
        0 if p.is_hosting else 1,
        (p.success_count or 0) - 2 * (p.fail_count or 0),
        -(p.avg_response_time or 99.0),
    )


async def working(db) -> List[Proxy]:
    """Active, working proxies, best first."""
    rows = (await db.execute(
        select(Proxy).where(Proxy.is_active == True, Proxy.is_working == True)  # noqa: E712
    )).scalars().all()
    return sorted(rows, key=_score, reverse=True)


async def pick_for_account(db, account: Optional[str]) -> Optional[str]:
    """The proxy this account always uses, as a URL — or None if none work.

    Sticky by hash of the phone number: the same account lands on the same
    proxy every run and every recycle, so it is one person at one address.
    A different account most likely lands elsewhere. When the pool changes
    the mapping shifts, which is unavoidable and rare.

    With a single proxy configured everybody shares it — that is the pool's
    limitation, not the picker's, and the panel says so.
    """
    pool = await working(db)
    if not pool:
        return None
    if not account:
        return pool[0].url
    seed = int(hashlib.sha256(account.encode("utf-8")).hexdigest()[:8], 16)
    return pool[seed % len(pool)].url


async def refresh_all() -> dict:
    """Re-test every active proxy. Own session; never raises."""
    tested = ok = 0
    try:
        async with async_session_maker() as db:
            rows = (await db.execute(
                select(Proxy).where(Proxy.is_active == True)  # noqa: E712
            )).scalars().all()
            for p in rows:
                res = await probe(p)
                tested += 1
                ok += 1 if res.get("success") else 0
            await db.commit()
    except Exception as e:
        logger.warning(f"[proxy] refresh failed: {type(e).__name__}: {e}")
    if tested:
        logger.info(f"[proxy] refreshed {tested} proxies — {ok} reach Divar")
    return {"tested": tested, "working": ok}


async def refresh_loop() -> None:
    """Re-test the pool every PROXY_REFRESH_HOURS (default 24). 0 disables.

    Same shape as the Divar session verifier: a belief («working») that is
    only ever corrected when somebody presses a button is a belief that is
    wrong most of the time.
    """
    hours = float(getattr(settings, "proxy_refresh_hours", 24) or 0)
    if hours <= 0:
        return
    await asyncio.sleep(120)  # let the app come up first
    while True:
        await refresh_all()
        await asyncio.sleep(hours * 3600)


def parse_list(text: str) -> List[dict]:
    """ip:port, ip:port:user:pass, or scheme://[user:pass@]host:port — one per
    line. Comments and blanks ignored."""
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        protocol = "http"
        if "://" in line:
            protocol, line = line.split("://", 1)
            protocol = protocol.lower()
        user = pwd = None
        if "@" in line:
            cred, line = line.rsplit("@", 1)
            if ":" in cred:
                user, pwd = cred.split(":", 1)
        parts = line.split(":")
        if len(parts) < 2 or not parts[1].isdigit():
            continue
        if len(parts) >= 4 and user is None:
            user, pwd = parts[2], parts[3]
        out.append({"address": parts[0], "port": int(parts[1]),
                    "username": user, "password": pwd, "protocol": protocol})
    return out


async def fetch_list(url: str) -> str:
    """Download a proxy list. Plain text, one per line, up to 1 MB."""
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.text[:1_000_000]
