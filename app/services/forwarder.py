"""
Which phone sent this code, whose phone it is, and may it answer for this
Divar account.

The single OTP_INBOUND_SECRET answered none of those. It said only «somebody
who knows the secret», and with more than one operator that is everybody: one
person's phone could answer another person's Divar prompt, and revoking a lost
handset means re-configuring every other one.

Three checks now, in this order, because each is cheaper than the next and a
failure in any of them means the same thing to the caller:

  1. WHICH DEVICE — X-Forwarder-Id names a row. Unknown or inactive is a 401,
     not a 404: an unauthenticated caller learns nothing about which ids exist.
  2. IS IT REALLY THAT DEVICE — HMAC of the raw body under that device's own
     secret, or the secret itself in a header for a forwarder that cannot sign.
     Constant-time either way.
  3. MAY IT ANSWER FOR THIS ACCOUNT — the Divar account named in the body must
     be one its owner owns. Without this a valid device could answer a prompt
     belonging to somebody else's account, which is the multi-user version of
     the bug this file exists to prevent.

The legacy global secret still works when no device id is sent, so the phone
configured before any of this existed keeps running until it is migrated.
"""
import hashlib
import hmac
from datetime import datetime, timezone
from typing import Optional, Tuple

from loguru import logger
from sqlalchemy import select

from app.models.forwarder import ForwarderDevice

# A heartbeat older than this means the phone is not reporting. Ten minutes:
# the app beats every five, so one miss is a blip and two is a problem.
ONLINE_WINDOW = 600


class ForwarderAuthError(Exception):
    """Carries the status and the message the route should answer with."""

    def __init__(self, status: int, detail: str):
        self.status = status
        self.detail = detail
        super().__init__(detail)


def _digits(v: Optional[str]) -> str:
    return "".join(ch for ch in str(v or "") if ch.isdigit())


def same_phone(a: Optional[str], b: Optional[str]) -> bool:
    """09058432452 == +989058432452 == 9058432452.

    Compared on the last ten digits: a number reaches us from a SIM, from a
    pasted cookie jar and from a form, and those three spell it differently.
    """
    da, db = _digits(a), _digits(b)
    if not da or not db:
        return False
    return da[-10:] == db[-10:]


async def resolve_device(db, device_id: Optional[str]) -> Optional[ForwarderDevice]:
    if not device_id:
        return None
    return (await db.execute(
        select(ForwarderDevice).where(ForwarderDevice.device_id == device_id.strip())
    )).scalars().first()


def verify_signature(secret: str, raw: bytes, signature: str, plain: str) -> bool:
    """HMAC over the raw bytes, or the secret itself. Constant-time on both."""
    if not secret:
        return False
    sig = (signature or "").strip().lower()
    if sig:
        want = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
        if hmac.compare_digest(sig, want):
            return True
    if plain and hmac.compare_digest(plain.encode("utf-8"), secret.encode("utf-8")):
        return True
    return False


async def owns_divar_account(db, user_id: int, account: Optional[str]) -> bool:
    """Whether this user owns the Divar session the code claims to be for.

    A user with no owned sessions is not silently allowed: that was the state
    every account was in before ownership existed, and treating it as «allow»
    would make the check do nothing on exactly the installation that needs it.
    Sessions with no owner at all are the migration's problem, and are matched
    so an un-migrated install keeps working.
    """
    from app.models.cookie import Cookie

    if not account:
        return False
    rows = (await db.execute(select(Cookie))).scalars().all()
    for row in rows:
        if not same_phone(row.phone_number, account):
            continue
        owner = getattr(row, "owner_user_id", None)
        # Unowned (pre-migration) sessions are answerable by anyone, which is
        # the behaviour that existed before this file. Owned ones are not.
        return owner is None or owner == user_id
    return False


async def authenticate(db, *, device_id: Optional[str], raw: bytes,
                       signature: str, plain: str, account: Optional[str],
                       legacy_secret: Optional[str]
                       ) -> Tuple[Optional[ForwarderDevice], str]:
    """Authenticate one inbound POST. Raises ForwarderAuthError, or returns
    (device or None for the legacy path, how it authenticated)."""
    if device_id:
        device = await resolve_device(db, device_id)
        # Unknown and inactive answer identically, and identically to a bad
        # signature: a caller without credentials learns nothing about which
        # device ids exist or which are switched off.
        if device is None or not device.is_active:
            raise ForwarderAuthError(401, "bad signature")
        if not verify_signature(device.secret, raw, signature, plain):
            raise ForwarderAuthError(401, "bad signature")
        if account and not await owns_divar_account(db, device.user_id, account):
            raise ForwarderAuthError(
                403, "this device may not answer for that Divar account")
        return device, "device"

    # ── legacy: one secret, no device ──
    if legacy_secret and verify_signature(legacy_secret, raw, signature, plain):
        return None, "legacy"
    if not legacy_secret:
        raise ForwarderAuthError(503, "no forwarder is configured")
    raise ForwarderAuthError(401, "bad signature")


async def note_seen(db, device: ForwarderDevice, *, battery=None, network=None,
                    version=None, delivered_code: bool = False) -> None:
    """Record a heartbeat or a delivery. Never raises: losing a statistic must
    not cost a code."""
    try:
        now = datetime.now(timezone.utc)
        device.last_seen_at = now
        if battery is not None:
            device.battery = int(battery)
        if network:
            device.network = str(network)[:24]
        if version:
            device.app_version = str(version)[:24]
        if delivered_code:
            device.last_code_at = now
            device.codes_forwarded = (device.codes_forwarded or 0) + 1
        # A device that is working again has nothing outstanding to warn about.
        if delivered_code:
            device.warned_at = None
        await db.commit()
    except Exception as e:
        logger.warning(f"[forwarder] could not record device state: {e}")
        try:
            await db.rollback()
        except Exception:
            pass


def health(device: ForwarderDevice, *, now: Optional[datetime] = None) -> dict:
    """What the panel shows, and what an email would say.

    «online» and «delivering» are separate on purpose. A phone can heartbeat
    perfectly and never forward a code — a wrong text filter does exactly that
    — and reporting one as the other sends somebody to check their internet
    when the problem is a rule in the app.
    """
    now = now or datetime.now(timezone.utc)

    def _age(ts):
        if not ts:
            return None
        t = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        return (now - t).total_seconds()

    seen = _age(device.last_seen_at)
    code = _age(device.last_code_at)
    online = seen is not None and seen < ONLINE_WINDOW

    if not device.is_active:
        state, fa = "disabled", "غیرفعال شده"
    elif seen is None:
        state, fa = "never_seen", "هنوز وصل نشده — برنامه را روی گوشی تنظیم کنید"
    elif not online:
        state, fa = "offline", "گوشی خاموش است یا اینترنت ندارد"
    elif code is None:
        state, fa = "no_codes_yet", "وصل است ولی هنوز کدی نفرستاده"
    else:
        state, fa = "ok", "سالم"

    return {
        "state": state,
        "message_fa": fa,
        "online": online,
        "seconds_since_seen": int(seen) if seen is not None else None,
        "seconds_since_code": int(code) if code is not None else None,
        "battery": device.battery,
        "network": device.network,
    }
