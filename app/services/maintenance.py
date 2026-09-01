"""
حالت تعمیر — closing the site while it is being updated.

Kept in the database rather than in memory: it stays on for days at a time,
across deploys and pod restarts, and a module-level flag would clear itself
every time Kubernetes replaced the pod — quietly reopening a site that was
meant to be shut.

Reads are cached for a few seconds so this does not put a query in front of
every request; writes clear the cache immediately, so the toggle feels instant.
"""
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from loguru import logger
from sqlalchemy import select

KEY_ENABLED = "maintenance_enabled"
KEY_MESSAGE = "maintenance_message"
KEY_BYPASS = "maintenance_bypass_token"
KEY_UNTIL = "maintenance_until"          # ISO-8601 UTC — drives the countdown
KEY_PHONE = "maintenance_contact_phone"
KEY_EMAIL = "maintenance_contact_email"

DEFAULT_MESSAGE = "سایت در حال بروزرسانی می‌باشد"
BYPASS_COOKIE = "sf_maintenance_bypass"
# How long a closure is assumed to last when nobody says otherwise.
DEFAULT_WINDOW_HOURS = 72                # three days


@dataclass
class State:
    """Everything the closed-site page needs to render itself.

    A dataclass rather than a widening tuple: this grew from three fields to
    six, and `enabled, message, bypass, until, phone, email = ...` at five call
    sites is a positional mistake waiting to happen. Unpacking still works for
    the first three, so existing callers were left alone where they only needed
    those.
    """
    enabled: bool = False
    message: str = DEFAULT_MESSAGE
    bypass: Optional[str] = None
    until: Optional[str] = None          # ISO-8601 UTC, or None for no countdown
    phone: Optional[str] = None
    email: Optional[str] = None

    def __iter__(self):
        # keeps `enabled, message, bypass = await get_state(db)` working
        return iter((self.enabled, self.message, self.bypass))

    @property
    def seconds_left(self) -> Optional[int]:
        if not self.until:
            return None
        try:
            end = datetime.fromisoformat(self.until)
        except ValueError:
            return None
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        return max(int((end - datetime.now(timezone.utc)).total_seconds()), 0)

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "message": self.message,
            "until": self.until,
            "seconds_left": self.seconds_left,
            "contact_phone": self.phone,
            "contact_email": self.email,
        }

_CACHE_TTL = 5.0
_cache: dict = {"at": 0.0, "state": State()}


def _put(state: State) -> None:
    _cache.update({"at": time.time(), "state": state})


def invalidate() -> None:
    _cache["at"] = 0.0


async def _read(db) -> State:
    from app.models.app_setting import AppSetting
    rows = (await db.execute(select(AppSetting).where(AppSetting.key.in_(
        [KEY_ENABLED, KEY_MESSAGE, KEY_BYPASS,
         KEY_UNTIL, KEY_PHONE, KEY_EMAIL])))).scalars().all()
    v = {r.key: r.value for r in rows}
    return State(
        enabled=(v.get(KEY_ENABLED) or "").lower() == "true",
        message=v.get(KEY_MESSAGE) or DEFAULT_MESSAGE,
        bypass=v.get(KEY_BYPASS) or None,
        until=v.get(KEY_UNTIL) or None,
        phone=v.get(KEY_PHONE) or None,
        email=v.get(KEY_EMAIL) or None,
    )


async def get_state(db, *, fresh: bool = False) -> State:
    """Current maintenance state. Unpacks as (enabled, message, bypass)."""
    if not fresh and time.time() - _cache["at"] < _CACHE_TTL:
        return _cache["state"]
    try:
        state = await _read(db)
    except Exception as e:
        # A database that is down must not lock everyone out of a site that
        # was never put into maintenance.
        logger.warning(f"[maintenance] state unreadable, assuming open: {e}")
        return State()
    _put(state)
    return state


async def set_state(db, *, enabled: bool, message: Optional[str] = None,
                    until: Optional[str] = None, hours: Optional[float] = None,
                    phone: Optional[str] = None, email: Optional[str] = None,
                    actor: Optional[str] = None) -> State:
    """Turn it on or off, and set what the closed page shows.

    Turning it on mints a fresh bypass token, so a link shared during an
    earlier closure cannot reopen a later one.

    `until` is an explicit ISO timestamp; `hours` is the friendlier form the
    dashboard sends ("close it for 72 hours"). Neither given, a first closure
    gets DEFAULT_WINDOW_HOURS so the page always has something to count down —
    a closed site with no stated end reads as abandoned rather than busy.
    """
    from app.models.app_setting import AppSetting

    current = await get_state(db, fresh=True)
    message = (message or current.message or DEFAULT_MESSAGE).strip()[:500]

    if enabled:
        # Only mint a new token when the site is being closed, not every time
        # the settings are re-saved. Re-minting on a settings change invalidated
        # the bypass link the admin was already using — and it meant a deadline
        # could not be added to a site that was already closed without opening
        # it first, which is exactly the state production was left in.
        bypass = current.bypass if current.enabled and current.bypass else secrets.token_urlsafe(24)
        if hours is not None:
            until = (datetime.now(timezone.utc)
                     + timedelta(hours=max(float(hours), 0))).isoformat()
        elif until is None:
            # keep an existing deadline across a re-save, otherwise start one
            until = current.until or (
                datetime.now(timezone.utc)
                + timedelta(hours=DEFAULT_WINDOW_HOURS)).isoformat()
    else:
        bypass = None
        until = None                     # a reopened site has nothing to count

    phone = (phone if phone is not None else current.phone or "").strip()[:32]
    email = (email if email is not None else current.email or "").strip()[:200]

    for key, value in ((KEY_ENABLED, "true" if enabled else "false"),
                       (KEY_MESSAGE, message),
                       (KEY_BYPASS, bypass or ""),
                       (KEY_UNTIL, until or ""),
                       (KEY_PHONE, phone),
                       (KEY_EMAIL, email)):
        row = (await db.execute(select(AppSetting).where(
            AppSetting.key == key))).scalar_one_or_none()
        if row:
            row.value = value
            row.updated_by = actor
        else:
            db.add(AppSetting(key=key, value=value, updated_by=actor))
    await db.commit()
    invalidate()
    logger.info(f"[maintenance] {'ON' if enabled else 'OFF'} by {actor or 'unknown'}"
                + (f" until {until}" if until else ""))
    return State(enabled=enabled, message=message, bypass=bypass,
                 until=until or None, phone=phone or None, email=email or None)


# Paths that stay open no matter what.
#   /health   — Kubernetes reads it; closing it makes the kubelet kill the pod
#               and the site goes down for real instead of showing a notice.
#   the login endpoints — the way back in for whoever is allowed through.
OPEN_PREFIXES = (
    "/health",
    # Kubernetes reads this; closing it makes the kubelet kill the pod and the
    # site goes down for real instead of showing a notice.
    "/api/users/token",     # the way back in — login and the TOTP step
    "/api/maintenance",     # status, and the off-switch, which must never be
                            # unreachable; the POST is still super_admin-only
    "/maintenance-access",  # the bypass link would be blocked by the very
                            # middleware it exists to get past
    "/favicon",
    "/dashboard/css/fonts/",  # the notice's own webfont. Blocking it dropped a
                              # wholly-Persian page to Tahoma at the one moment
                              # nobody can go and fix it.
)


def is_open_path(path: str) -> bool:
    return any(path.startswith(p) for p in OPEN_PREFIXES)
