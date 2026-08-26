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
from typing import Optional, Tuple

from loguru import logger
from sqlalchemy import select

KEY_ENABLED = "maintenance_enabled"
KEY_MESSAGE = "maintenance_message"
KEY_BYPASS = "maintenance_bypass_token"

DEFAULT_MESSAGE = "سایت در حال بروزرسانی می‌باشد"
BYPASS_COOKIE = "sf_maintenance_bypass"

_CACHE_TTL = 5.0
_cache: dict = {"at": 0.0, "enabled": False, "message": DEFAULT_MESSAGE, "bypass": None}


def _put(enabled: bool, message: str, bypass: Optional[str]) -> None:
    _cache.update({"at": time.time(), "enabled": enabled,
                   "message": message or DEFAULT_MESSAGE, "bypass": bypass})


def invalidate() -> None:
    _cache["at"] = 0.0


async def _read(db) -> Tuple[bool, str, Optional[str]]:
    from app.models.app_setting import AppSetting
    rows = (await db.execute(select(AppSetting).where(
        AppSetting.key.in_([KEY_ENABLED, KEY_MESSAGE, KEY_BYPASS])))).scalars().all()
    values = {r.key: r.value for r in rows}
    return (
        (values.get(KEY_ENABLED) or "").lower() == "true",
        values.get(KEY_MESSAGE) or DEFAULT_MESSAGE,
        values.get(KEY_BYPASS) or None,
    )


async def get_state(db, *, fresh: bool = False) -> Tuple[bool, str, Optional[str]]:
    """(enabled, message, bypass_token)."""
    if not fresh and time.time() - _cache["at"] < _CACHE_TTL:
        return _cache["enabled"], _cache["message"], _cache["bypass"]
    try:
        enabled, message, bypass = await _read(db)
    except Exception as e:
        # A database that is down must not lock everyone out of a site that
        # was never put into maintenance.
        logger.warning(f"[maintenance] state unreadable, assuming open: {e}")
        return False, DEFAULT_MESSAGE, None
    _put(enabled, message, bypass)
    return enabled, message, bypass


async def set_state(db, *, enabled: bool, message: Optional[str] = None,
                    actor: Optional[str] = None) -> Tuple[bool, str, str]:
    """Turn it on or off. Returns (enabled, message, bypass_token).

    Turning it on mints a fresh bypass token, so a link shared during an
    earlier closure cannot reopen a later one.
    """
    from app.models.app_setting import AppSetting

    _, current_message, bypass = await get_state(db, fresh=True)
    message = (message or current_message or DEFAULT_MESSAGE).strip()[:500]
    if enabled:
        bypass = secrets.token_urlsafe(24)
    else:
        bypass = None

    for key, value in ((KEY_ENABLED, "true" if enabled else "false"),
                       (KEY_MESSAGE, message),
                       (KEY_BYPASS, bypass or "")):
        row = (await db.execute(select(AppSetting).where(
            AppSetting.key == key))).scalar_one_or_none()
        if row:
            row.value = value
            row.updated_by = actor
        else:
            db.add(AppSetting(key=key, value=value, updated_by=actor))
    await db.commit()
    invalidate()
    logger.info(f"[maintenance] {'ON' if enabled else 'OFF'} by {actor or 'unknown'}")
    return enabled, message, bypass or ""


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
    "/api/config",
    "/favicon",
)


def is_open_path(path: str) -> bool:
    return any(path.startswith(p) for p in OPEN_PREFIXES)
