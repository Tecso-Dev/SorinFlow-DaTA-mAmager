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
#
# A PUBLIC endpoint, deliberately. The obvious choice — /v8/user/profile — is
# role-gated, and answers 403 to everyone: no cookie, an unrelated cookie, a
# junk token, and (this is the part that cost two days) a perfectly valid
# ordinary account. Every session it was asked about came back dead, which is
# why sessions kept being written out of rotation minutes after a good login.
#
# /v8/places/cities needs no login at all, and Divar's edge validates the token
# cookie before routing:
#
#     no cookie          -> 200
#     did=abc123         -> 200
#     token=<junk>       -> 403
#
# So the status is a clean read on one question — will Divar accept this token
# — with no permissions layer on top to confuse a refusal with a lack of rights.
PROBE_URL = "https://api.divar.ir/v8/places/cities"

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


# The cookies that carry the session. Everything else in the jar is analytics
# and preferences, and their expiries say nothing about whether we can scrape.
#
# Divar has moved to SuperTokens. A login today sets:
#
#     cdid, csid, did, ff, referrer, resolution_width,
#     sAccessToken, sFrontToken, sRefreshToken, theme
#
# and no `token` at all. Every part of this codebase was looking for `token`,
# which is why a perfectly good login produced "No session cookie after 30s",
# "No specific auth cookies found", an empty expiry column, and — once a stale
# `token` from the old scheme was still in the jar — a 403 on everything.
#
# Order is preference: sAccessToken is the bearer SuperTokens actually checks.
# `token` stays for sessions saved under the old scheme and for the day Divar
# changes its mind again.
AUTH_COOKIE_NAMES = ("sAccessToken", "token", "sRefreshToken")

# Kept as the single legacy name for anything that still wants one string.
AUTH_COOKIE = "token"


def auth_cookie(jar):
    """The session-bearing cookie from a jar, by preference order, or None."""
    by_name = {c.get("name"): c for c in (jar or []) if c.get("name") and c.get("value")}
    for name in AUTH_COOKIE_NAMES:
        if name in by_name:
            return by_name[name]
    return None


def has_auth_cookie(jar) -> bool:
    """Whether this jar carries a session at all.

    The question every caller was really asking when it looked for `token`.
    """
    return auth_cookie(jar) is not None


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
    c = auth_cookie(jar)
    return cookie_expiry(c) if c else None


DIVAR_HOSTS = ("divar.ir", ".divar.ir", "api.divar.ir", ".api.divar.ir")


def divar_cookies(jar):
    """Divar's own cookies, one entry per name, freshest kept.

    Two things went wrong when this simply concatenated the whole jar.

    `context.cookies()` returns every cookie the browser holds for every domain
    it touched during the login — analytics, captcha providers, anything the
    page embedded. Sending those to api.divar.ir is wrong on its face and makes
    the header enormous.

    Worse, `token` legitimately appears twice: Divar sets it for `.divar.ir`
    and the page picks it up again for `divar.ir`. Joining the jar produced
    `token=<old>; token=<new>`, Divar read whichever it liked and refused the
    request — and a refusal is a flat 403 on *any* endpoint, including public
    ones. So a freshly-issued, perfectly good session came back "rejected by
    Divar", which is exactly what the panel then reported.
    """
    best = {}
    for c in (jar or []):
        name, value = c.get("name"), c.get("value")
        if not name or not value:
            continue
        dom = (c.get("domain") or "").strip().lower()
        # No domain at all: a hand-pasted jar. Keep it — refusing would break
        # the documented import path for the sake of a field it need not have.
        if dom and not any(dom == h or dom.endswith(".divar.ir") for h in DIVAR_HOSTS):
            continue
        prev = best.get(name)
        if prev is None or _fresher(c, prev):
            best[name] = c
    return list(best.values())


def _fresher(a, b) -> bool:
    """Later expiry wins; a session cookie loses to a dated one only if the
    dated one is still in the future."""
    ea, eb = cookie_expiry(a), cookie_expiry(b)
    if ea is None and eb is None:
        return False          # keep the first seen, so the order is stable
    if eb is None:
        return True
    if ea is None:
        return False
    return ea > eb


def _header_from(jar) -> str:
    return "; ".join(f"{c.get('name')}={c.get('value')}"
                     for c in divar_cookies(jar))


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
        # Check the instrument before trusting it.
        #
        # The same endpoint with no cookie at all must answer 200. If it does
        # not, then Divar — or whatever sits between us and it — is refusing
        # everyone, and this tells us nothing about the account. Reporting
        # "expired" there is how a working session gets written off during
        # somebody else's outage. Twice now, a probe I had not sanity-checked
        # deleted sessions that were fine.
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
                control = await client.get(PROBE_URL, headers=dict(_HEADERS))
        except Exception:
            control = None

        if control is None or control.status_code != 200:
            got = "no answer" if control is None else f"HTTP {control.status_code}"
            logger.warning(f"[session] refusal not trusted: the same endpoint "
                           f"without a token returned {got}")
            return {"alive": None, "state": "unknown", "needs_login": False,
                    "took_ms": took, "http_status": r.status_code,
                    "message": ("دیوار به همهٔ درخواست‌ها پاسخ منفی می‌دهد — "
                                "وضعیت این حساب نامشخص است")}

        return {"alive": False, "state": "expired", "needs_login": True,
                "took_ms": took, "http_status": r.status_code,
                "message": f"دیوار این نشست را رد کرد (HTTP {r.status_code}) — ورود مجدد لازم است"}

    return {"alive": None, "state": "unknown", "needs_login": False,
            "took_ms": took, "http_status": r.status_code,
            "message": f"پاسخ غیرمنتظره از دیوار (HTTP {r.status_code}) — وضعیت نامشخص"}


async def check_and_record(db, row, *, confirm: bool = False) -> dict:
    """probe(), then write down both the answer and the fact that we asked.

    `confirm` re-probes once before writing a session off. A 403 from Divar is
    not the unambiguous signal it looks like: Divar answers 403 to *any*
    request whose token cookie it dislikes — including on endpoints that need
    no login at all — so a transient edge refusal is indistinguishable from an
    expired session by status code alone. The unattended loop must therefore
    ask twice before removing an account from rotation: a false negative there
    costs a working session and a round of SMS codes to get it back. The manual
    button does not confirm — somebody is watching, and they asked.

    last_checked_at is stamped for every definite answer — including a healthy
    one. Recording only failures would leave a working session looking exactly
    as unverified as one nobody has ever tested.

    An indefinite answer (alive is None) is stamped too but leaves is_valid
    alone: "we asked and Divar did not answer" is still more than we knew
    before, and the row must not be written off for it.
    """
    res = await probe(row)

    if confirm and res["alive"] is False:
        await asyncio.sleep(3)
        second = await probe(row)
        if second["alive"] is not False:
            logger.info(f"[session] {row.phone_number} failed once then "
                        f"recovered — not writing it off")
        res = second

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
                        await check_and_record(db, row, confirm=True)
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
