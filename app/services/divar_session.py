"""
Whether a stored Divar session still works — asked of Divar, not of ourselves.

`cookies.is_valid` is a *belief*. It is written when a session is imported and
corrected only when something happens to disprove it: the scraper hitting a 403
mid-run, or someone pressing the check button. Between those two events the
panel was stating that belief as fact, and it was wrong for a day and a half —
both accounts had been rejected since the previous morning and the header still
said «کوکی فعال».

So the belief now carries the time it was last tested, and a background loop
tests it on a schedule. The panel shows both: the state, and how long ago
anyone actually asked. A claim nobody has checked in an hour should not look
like one checked a minute ago.

The probe lives here rather than in the monitoring router because two callers
need it — the button and the loop — and a background task importing from an API
route would have the layering backwards.
"""
import asyncio
import time
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import select

from app.config import get_settings
from app.database import async_session_maker

settings = get_settings()

# Fixed. `phone` from a request is a lookup key into our own table and never
# reaches this value.
PROBE_URL = "https://api.divar.ir/v8/user/profile"

_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "fa-IR,fa;q=0.9",
    "Origin": "https://divar.ir",
    "Referer": "https://divar.ir/",
    "x-render-type": "CSR",
    "x-standard-divar-error": "true",
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120.0.0.0 Safari/537.36"),
}


# The cookie that carries the session. Everything else in the jar is
# analytics and preferences, and their expiries say nothing about whether we
# can still scrape.
AUTH_COOKIE = "token"


def cookie_expiry(c):
    """Expiry of one cookie dict as an aware UTC datetime, or None.

    Handles the three field names different exporters use and both the second
    and millisecond forms. None means "no wall-clock expiry" — a session
    cookie — which is not the same as expired.
    """
    from datetime import datetime as _dt, timezone as _tz

    raw = c.get("expirationDate") or c.get("expires") or c.get("expiry")
    if raw in (None, "", 0, -1):
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    # Some exporters write milliseconds. No real session expiry is in the year
    # 5138, so anything that large is ms.
    if v > 1e11:
        v /= 1000.0
    try:
        return _dt.fromtimestamp(v, tz=_tz.utc)
    except (OverflowError, OSError, ValueError):
        return None


def derive_expiry(jar):
    """When the session cookie expires, as an aware UTC datetime, or None.

    Three copies of this existed and two of them were wrong, which is why the
    panel showed «—» for every account:

      * they looked only for the field `expires`. Playwright writes that, but a
        jar pasted out of a browser extension — the documented import path —
        writes `expirationDate`, so an imported session had no expiry at all.
      * they used datetime.fromtimestamp() with no timezone, which builds a
        naive *local* time. _cookie_state compares it against naive *UTC*, so
        every countdown was out by the host's offset — three and a half hours
        here.

    A missing, zero or negative expiry means a session cookie: it dies with the
    browser and has no wall-clock expiry, which is not the same as expired.
    """
    for c in (jar or []):
        if c.get("name") == AUTH_COOKIE:
            return cookie_expiry(c)
    return None


def _header_from(jar) -> str:
    return "; ".join(f"{c.get('name')}={c.get('value')}" for c in (jar or [])
                     if c.get("name") and c.get("value"))


async def probe(row) -> dict:
    """Ask Divar about one stored session. Never raises, never writes.

    alive is True, False, or None — and None is load-bearing. "Divar did not
    answer" is not "the account is dead", and conflating them would empty the
    rotation pool during an outage, which is exactly when the scraper can least
    afford it.
    """
    import httpx

    header = _header_from(row.cookies)
    if not header:
        return {"alive": False, "state": "expired", "needs_login": True,
                "message": "هیچ کوکی ذخیره‌شده‌ای وجود ندارد — ورود مجدد لازم است"}

    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            r = await client.get(PROBE_URL, headers={"Cookie": header, **_HEADERS})
    except Exception as e:
        return {"alive": None, "state": "unknown", "needs_login": False,
                "took_ms": round((time.perf_counter() - started) * 1000, 1),
                "message": f"دیوار پاسخ نداد ({type(e).__name__}) — وضعیت نامشخص است"}

    took = round((time.perf_counter() - started) * 1000, 1)

    if r.status_code == 200:
        return {"alive": True, "state": "active", "needs_login": False,
                "took_ms": took, "http_status": r.status_code,
                "message": f"دیوار این نشست را پذیرفت (HTTP {r.status_code})"}

    if r.status_code in (401, 403):
        return {"alive": False, "state": "expired", "needs_login": True,
                "took_ms": took, "http_status": r.status_code,
                "message": f"دیوار این نشست را رد کرد (HTTP {r.status_code}) — ورود مجدد لازم است"}

    return {"alive": None, "state": "unknown", "needs_login": False,
            "took_ms": took, "http_status": r.status_code,
            "message": f"پاسخ غیرمنتظره از دیوار (HTTP {r.status_code}) — وضعیت نامشخص"}


async def check_and_record(db, row) -> dict:
    """probe(), then write down both the answer and the fact that we asked.

    last_checked_at is stamped for every definite answer — including a healthy
    one. Recording only failures would leave a working session looking exactly
    as unverified as one nobody has ever tested.

    An indefinite answer (alive is None) is stamped too but leaves is_valid
    alone: "we asked and Divar did not answer" is still more than we knew
    before, and the row must not be written off for it.
    """
    res = await probe(row)
    now = datetime.now(timezone.utc)

    # Backfill on the way past. Every session stored before derive_expiry
    # existed has expires_at NULL, and re-deriving here fixes them without
    # anyone having to log in again.
    if row.expires_at is None:
        exp = derive_expiry(row.cookies)
        if exp is not None:
            row.expires_at = exp

    row.last_checked_at = now
    if res["alive"] is True and not row.is_valid:
        row.is_valid = True
        logger.info(f"[session] {row.phone_number} accepted by Divar — back in rotation")
    elif res["alive"] is False and row.is_valid:
        row.is_valid = False
        logger.warning(f"[session] {row.phone_number} rejected by Divar "
                       f"(HTTP {res.get('http_status')}) — removed from rotation")
    await db.commit()

    res["phone_number"] = row.phone_number
    res["last_checked_at"] = now.isoformat()
    return res


async def verifier_loop():
    """Re-test every stored session on a schedule, so the panel is live.

    One loop in one process, not one per open browser tab: the cost is an
    outbound request to someone else's server, and it should not scale with how
    many people happen to be looking at the page.

    Set DIVAR_SESSION_CHECK_MINUTES to 0 to switch this off.
    """
    from app.models.cookie import Cookie

    every = int(getattr(settings, "divar_session_check_minutes", 0) or 0)
    if every <= 0:
        logger.info("[session] periodic verification disabled")
        return

    logger.info(f"[session] verifier armed — every {every}m")
    # Let startup finish first; a probe during boot competes with the migration
    # and tells us nothing we need in the first minute.
    await asyncio.sleep(45)

    while True:
        try:
            async with async_session_maker() as db:
                rows = (await db.execute(
                    select(Cookie).order_by(Cookie.updated_at.desc().nullslast())
                )).scalars().all()

                seen = set()
                for row in rows:
                    if not row.phone_number or row.phone_number in seen:
                        continue          # one entry per number, newest wins
                    seen.add(row.phone_number)
                    try:
                        await check_and_record(db, row)
                    except Exception as e:
                        # One bad row must not stop the others being checked.
                        logger.warning(
                            f"[session] check failed for {row.phone_number}: "
                            f"{type(e).__name__}: {e}")
                    # Spread them out rather than arriving as a burst.
                    await asyncio.sleep(2)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[session] verifier error: {type(e).__name__}: {e}")

        try:
            await asyncio.sleep(every * 60)
        except asyncio.CancelledError:
            break
