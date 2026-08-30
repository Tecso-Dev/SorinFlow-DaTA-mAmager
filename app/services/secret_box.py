"""
Credentials that the panel is allowed to write.

The rule in SECRETS.md still stands: production credentials belong in the
Kubernetes Secret. But a panel that cannot be configured without a deploy does
not get configured, so the SMS and email screens accept a key and put it here —
encrypted, never returned to the browser, and always losing to the environment
variable if one is set.

The Fernet key is derived from SECRET_KEY rather than stored separately, so
there is no second secret to manage. The consequence is deliberate: rotating
SECRET_KEY makes everything written here unreadable, and the values must be
re-entered rather than silently decrypting under a key that has been retired.
"""
import base64
import hashlib
from typing import Iterable, Optional

from loguru import logger
from sqlalchemy import select

from app.config import get_settings


def _fernet():
    from cryptography.fernet import Fernet
    digest = hashlib.sha256(get_settings().secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except Exception:
        # A rotated SECRET_KEY, or a hand-edited row. Absent, not fatal — this
        # is reached from inside a send, and raising there turns "the password
        # needs re-entering" into a 500.
        logger.warning("[secrets] a stored credential could not be decrypted — re-enter it in the panel")
        return ""


def mask(value: str) -> str:
    """A hint that identifies a credential without revealing it."""
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}{'*' * 8}{value[-4:]}"


async def get_many(db, keys: Iterable[str]) -> dict:
    from app.models.app_setting import AppSetting
    rows = (await db.execute(
        select(AppSetting).where(AppSetting.key.in_(list(keys))))).scalars().all()
    return {r.key: r.value for r in rows}


async def put(db, key: str, value: Optional[str], actor: str = "") -> None:
    from app.models.app_setting import AppSetting
    row = (await db.execute(
        select(AppSetting).where(AppSetting.key == key))).scalars().first()
    if row is None:
        row = AppSetting(key=key)
        db.add(row)
    row.value = value
    row.updated_by = actor or None
    await db.commit()
