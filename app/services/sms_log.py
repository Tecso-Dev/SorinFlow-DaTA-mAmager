"""
The SMS panel's own event log.

Modelled on app/services/job_log.py, and for the same reason: the application
log knows everything and can answer nothing, because the line you want has
rotated away or is interleaved with a thousand others.

The two rules from job_log carry over unchanged, and they are the important
part of this file:

* **Its own session, always.** A caller may be mid-transaction — sending a
  message writes a crm_sms_logs row on the request's session. A failed INSERT
  inside that transaction would put Postgres into an aborted state and take the
  send down with it, turning a logging problem into a delivery problem.
* **Never raises.** A message must not fail to send because we could not
  describe it.
"""
import json
from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import delete, select

from app.database import async_session_maker

# Deliberately few.
SEND = "send"            # one message, or a batch, went out — or did not
TEST = "test"            # the panel's «ارسال پیام آزمایشی»
SETTINGS = "settings"    # somebody changed the configuration
TEMPLATE = "template"    # a template was configured, or started/stopped working
CREDIT = "credit"        # balance observed, or too low to keep sending
DELIVERY = "delivery"    # a delivery-status poll changed what we believe
ERROR = "error"          # a call failed in a way that is not any of the above
INBOUND = "inbound"      # a Divar SMS forwarded from a phone, matched or not

RETENTION_DAYS = 90


async def record(stage: str, message: str, *, level: str = "info",
                 route: str = None, status: int = None, actor: str = None,
                 **details) -> bool:
    """Write one event. Returns True if it was stored.

    Never raises, never touches the caller's session.
    """
    from app.models.sms_log import SmsEvent

    payload = {k: v for k, v in details.items() if v is not None}
    try:
        async with async_session_maker() as db:
            db.add(SmsEvent(
                stage=stage,
                level=level,
                message=(message or "")[:2000],
                route=route,
                status=status,
                actor=(actor or None),
                details=json.dumps(payload, ensure_ascii=False) if payload else None,
            ))
            await db.commit()
        return True
    except Exception as e:
        # Deliberately swallowed, as in job_log.
        logger.warning(f"[sms-log] could not record {stage}: {type(e).__name__}: {e}")
        return False


async def record_send(number: str, result: dict, *, route: str, actor: str = None,
                      kind: str = "manual") -> bool:
    """One send, described by whether it worked and — when it did not — why.

    `result` is the dict both send paths return. A failure carries Kavenegar's
    own Persian in `response`, which is the sentence somebody actually needs.
    """
    ok = bool(result.get("success"))
    return await record(
        TEST if kind == "test" else SEND,
        ("ارسال شد" if ok else "ناموفق") + f" — {_mask(number)}",
        level="info" if ok else "warning",
        route=route,
        actor=actor,
        reason=None if ok else (result.get("response") or "")[:400],
        message_id=result.get("messageid"),
        cost=result.get("cost"),
        kind=kind,
    )


def _mask(number: str) -> str:
    """09121234567 -> 0912***4567. Enough to recognise the recipient in a list,
    not enough to turn the log into a contact export."""
    n = (number or "").strip()
    return n if len(n) < 8 else f"{n[:4]}***{n[-4:]}"


async def events(db, *, limit: int = 200, stage: str = None, level: str = None):
    """Most recent first — a log is read newest-down, unlike a run timeline."""
    from app.models.sms_log import SmsEvent

    q = select(SmsEvent)
    if stage:
        q = q.where(SmsEvent.stage == stage)
    if level:
        q = q.where(SmsEvent.level == level)
    q = q.order_by(SmsEvent.created_at.desc(), SmsEvent.id.desc()).limit(limit)
    return (await db.execute(q)).scalars().all()


async def prune(days: int = RETENTION_DAYS) -> int:
    """Drop events older than `days`."""
    from app.models.sms_log import SmsEvent

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    try:
        async with async_session_maker() as db:
            res = await db.execute(
                delete(SmsEvent).where(SmsEvent.created_at < cutoff))
            await db.commit()
            return res.rowcount or 0
    except Exception as e:
        logger.warning(f"[sms-log] prune skipped: {type(e).__name__}: {e}")
        return 0
