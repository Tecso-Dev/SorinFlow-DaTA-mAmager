"""
پیامک — the SMS panel.

Settings, credit, single sends, broadcasts and the delivery log. Everything a
person needs to run the SMS side of the business without logging into
Kavenegar's own panel, which agency staff do not have an account for.

Two rules hold this together:

  * The API key is never returned. It is written, masked, and tested — read
    back only as `sf12****ab34`, and only by super_admin.
  * A broadcast is confirmed by count before it sends. Sending to "all users"
    is irreversible and costs real money, so the panel makes you look at the
    number first.
"""
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, _role_dep
from app.auth.permissions import ROLE_ROOT, ROLE_SUPER_ADMIN
from app.config import get_settings
from app.database import get_db
from app.models.crm_models import Contact, SmsLog
from app.models.portal import PropertyRequest
from app.models.user import User
from app.services import sms_service as sms

router = APIRouter()
settings = get_settings()

_super_admin = Depends(_role_dep(ROLE_ROOT, ROLE_SUPER_ADMIN))

# Iranian mobile numbers, the only thing worth sending to. Accepts the three
# forms people paste — 09xx, 9xx, +989xx — and normalises them to 09xx.
_MOBILE = re.compile(r"^(?:\+?98|0)?(9\d{9})$")


def normalize_mobile(raw: str) -> Optional[str]:
    """'+989121234567' | '9121234567' | '09121234567' -> '09121234567'."""
    if not raw:
        return None
    # Persian and Arabic digits arrive from copy-paste more often than not.
    trans = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    digits = re.sub(r"[\s\-()]", "", str(raw).translate(trans))
    m = _MOBILE.match(digits)
    return f"0{m.group(1)}" if m else None


# ── settings ────────────────────────────────────────────────────────────────

class SmsSettingsIn(BaseModel):
    api_key: Optional[str] = Field(None, description="write-only; omit to keep the current one")
    sender: Optional[str] = None
    otp_template: Optional[str] = None
    signature: Optional[str] = None
    enabled: Optional[bool] = None


@router.get("/settings")
async def get_sms_settings(db: AsyncSession = Depends(get_db),
                           _: User = _super_admin):
    """Current configuration. The key comes back masked, never whole."""
    rows = await sms._get_settings_rows(db, [
        sms.KEY_API_KEY, sms.KEY_SENDER, sms.KEY_OTP_TEMPLATE,
        sms.KEY_ENABLED, sms.KEY_SIGNATURE])

    env_key = (settings.kavenegar_api_key or "").strip()
    saved = rows.get(sms.KEY_API_KEY)
    effective = env_key or (sms._decrypt(saved) if saved else "")

    return {
        # where the key came from matters: one is a deploy away from changing,
        # the other is one click, and the panel should not pretend otherwise
        "key_source": "env" if env_key else ("panel" if saved else None),
        "api_key_masked": sms.mask_key(effective),
        "configured": bool(effective),
        "sender": rows.get(sms.KEY_SENDER) or settings.kavenegar_sender or "",
        "otp_template": rows.get(sms.KEY_OTP_TEMPLATE) or "",
        "signature": rows.get(sms.KEY_SIGNATURE) or "",
        "enabled": (rows.get(sms.KEY_ENABLED) or "true").lower() == "true",
        "provider": "kavenegar",
    }


@router.put("/settings")
async def put_sms_settings(payload: SmsSettingsIn,
                           db: AsyncSession = Depends(get_db),
                           user: User = _super_admin):
    actor = user.username
    if payload.api_key is not None:
        key = payload.api_key.strip()
        if key:
            await sms.put_setting(db, sms.KEY_API_KEY, sms._encrypt(key), actor)
            logger.info(f"[sms] API key updated by {actor}")
        else:
            # An empty string is "forget it", which is how a key is removed.
            await sms.put_setting(db, sms.KEY_API_KEY, None, actor)
            logger.info(f"[sms] API key cleared by {actor}")

    for field, key in ((payload.sender, sms.KEY_SENDER),
                       (payload.otp_template, sms.KEY_OTP_TEMPLATE),
                       (payload.signature, sms.KEY_SIGNATURE)):
        if field is not None:
            await sms.put_setting(db, key, field.strip() or None, actor)

    if payload.enabled is not None:
        await sms.put_setting(db, sms.KEY_ENABLED,
                              "true" if payload.enabled else "false", actor)

    return await get_sms_settings(db, user)


@router.get("/account")
async def sms_account(db: AsyncSession = Depends(get_db),
                      _: User = Depends(get_current_user)):
    """Live credit and expiry, straight from Kavenegar.

    Not cached: it is the number that decides whether a broadcast will work,
    and a stale one is worse than none.
    """
    try:
        info = await sms.account_info(db)
        return {"ok": True, **info}
    except sms.SmsError as e:
        return {"ok": False, "status": e.status, "error": e.message}


@router.post("/test")
async def sms_test(to: str = Query(..., description="mobile number"),
                   db: AsyncSession = Depends(get_db),
                   user: User = _super_admin):
    """Send one real message, to prove the settings work end to end."""
    number = normalize_mobile(to)
    if not number:
        raise HTTPException(400, "شماره موبایل معتبر نیست")

    text = "پیام آزمایشی از پنل سورین‌فلو. تنظیمات پیامک درست کار می‌کند."
    result = await sms.send_sms(number, text, db=db)
    await _log(db, number, text, result, user.username, kind="manual")
    if not result.get("success"):
        return {"ok": False, "error": result.get("response")}
    return {"ok": True, "message_id": result.get("messageid"),
            "cost": result.get("cost")}


# ── sending ─────────────────────────────────────────────────────────────────

async def _log(db, number: str, body: Optional[str], result: dict,
               actor: str, *, kind: str = "manual",
               campaign: Optional[str] = None) -> SmsLog:
    """Record one send. Never raises — a logging failure must not look like a
    delivery failure to the caller."""
    try:
        row = SmsLog(
            to_number=number,
            message=body or "",
            status="sent" if result.get("success") else "failed",
            provider=result.get("provider") or "kavenegar",
            response=(result.get("response") or "")[:2000],
            message_id=str(result.get("messageid")) if result.get("messageid") else None,
            cost=result.get("cost"),
            sent_by=actor,
            kind=kind,
            campaign=campaign,
        )
        db.add(row)
        await db.commit()
        return row
    except Exception as e:
        logger.warning(f"[sms] could not write the send log: {e}")
        try:
            await db.rollback()
        except Exception:
            pass
        return None


class SendIn(BaseModel):
    to: str
    message: str = Field(..., min_length=1, max_length=1000)


@router.post("/send")
async def sms_send(payload: SendIn,
                   db: AsyncSession = Depends(get_db),
                   user: User = Depends(get_current_user)):
    number = normalize_mobile(payload.to)
    if not number:
        raise HTTPException(400, "شماره موبایل معتبر نیست")

    body = await _with_signature(db, payload.message)
    result = await sms.send_sms(number, body, db=db)
    await _log(db, number, body, result, user.username, kind="manual")
    if not result.get("success"):
        return {"ok": False, "error": result.get("response")}
    return {"ok": True, "message_id": result.get("messageid")}


async def _with_signature(db, message: str) -> str:
    rows = await sms._get_settings_rows(db, [sms.KEY_SIGNATURE])
    sig = (rows.get(sms.KEY_SIGNATURE) or "").strip()
    return f"{message}\n{sig}" if sig else message


# ── audiences ───────────────────────────────────────────────────────────────
#
# Named groups rather than a free-form query: "everyone" is the request people
# actually make, and letting the panel compose arbitrary SQL to find recipients
# is a way to mail the wrong list once and never live it down.

AUDIENCES = {
    "staff":     "کارکنان پنل",
    "visitors":  "بازدیدکنندگان ثبت‌نام‌کرده",
    "contacts":  "مخاطبین CRM",
    "requests":  "ثبت‌کنندگان درخواست ملک",
}


async def _audience_numbers(db, audience: str) -> list:
    """Distinct, valid, normalised mobile numbers for one named group."""
    raw = []
    if audience == "staff":
        rows = (await db.execute(
            select(User.phone).where(User.phone.isnot(None),
                                     User.role != "visitor",
                                     User.is_active == True))).scalars().all()  # noqa: E712
        raw = rows
    elif audience == "visitors":
        rows = (await db.execute(
            select(User.phone).where(User.phone.isnot(None),
                                     User.role == "visitor",
                                     User.is_active == True))).scalars().all()  # noqa: E712
        raw = rows
    elif audience == "contacts":
        rows = (await db.execute(
            select(Contact.phone).where(Contact.phone.isnot(None)))).scalars().all()
        raw = rows
    elif audience == "requests":
        rows = (await db.execute(
            select(PropertyRequest.contact_phone)
            .where(PropertyRequest.contact_phone.isnot(None)))).scalars().all()
        raw = rows
    else:
        raise HTTPException(400, f"گروه نامعتبر: {audience}")

    seen, out = set(), []
    for r in raw:
        n = normalize_mobile(r)
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


@router.get("/audiences")
async def list_audiences(db: AsyncSession = Depends(get_db),
                         _: User = Depends(get_current_user)):
    """Each group and how many reachable numbers it holds.

    Counted before sending, on purpose: a broadcast cannot be recalled, and the
    number is the last chance to notice that "all visitors" is 4,000 people.
    """
    out = []
    for key, label in AUDIENCES.items():
        try:
            out.append({"key": key, "label": label,
                        "count": len(await _audience_numbers(db, key))})
        except Exception as e:
            logger.warning(f"[sms] audience {key} could not be counted: {e}")
            out.append({"key": key, "label": label, "count": None})
    return {"audiences": out}


class BroadcastIn(BaseModel):
    audience: Optional[str] = None
    numbers: Optional[list] = None      # explicit list, instead of a group
    message: str = Field(..., min_length=1, max_length=1000)
    campaign: Optional[str] = None
    confirm_count: int = Field(..., description="what the panel showed the user")


@router.post("/broadcast")
async def sms_broadcast(payload: BroadcastIn,
                        db: AsyncSession = Depends(get_db),
                        user: User = _super_admin):
    """Send one message to a whole group.

    super_admin only, and guarded by confirm_count: the panel sends back the
    number it displayed, and if the real audience has changed since then the
    send is refused rather than quietly reaching more people than the person
    agreed to. This is the difference between a mistake and an incident.
    """
    if payload.numbers:
        numbers, source = [], "custom"
        seen = set()
        for r in payload.numbers:
            n = normalize_mobile(r)
            if n and n not in seen:
                seen.add(n)
                numbers.append(n)
    elif payload.audience:
        numbers = await _audience_numbers(db, payload.audience)
        source = payload.audience
    else:
        raise HTTPException(400, "گروه گیرندگان مشخص نشده است")

    if not numbers:
        raise HTTPException(400, "هیچ شماره معتبری در این گروه نیست")

    if payload.confirm_count != len(numbers):
        raise HTTPException(
            409,
            f"تعداد گیرندگان تغییر کرده است: {len(numbers)} به جای "
            f"{payload.confirm_count}. دوباره بررسی و تایید کنید.")

    body = await _with_signature(db, payload.message)
    campaign = (payload.campaign or f"{source}-{datetime.now(timezone.utc):%Y%m%d-%H%M}")[:120]

    logger.info(f"[sms] broadcast '{campaign}' to {len(numbers)} numbers by {user.username}")
    result = await sms.send_bulk(numbers, body, db=db)

    # One row per recipient, so the log answers "was this person messaged?"
    try:
        for r in result.get("results", []):
            db.add(SmsLog(
                to_number=r["receptor"],
                message=body,
                status="sent" if r.get("ok") else "failed",
                provider="kavenegar",
                response=(r.get("error") or "")[:2000],
                message_id=str(r["messageid"]) if r.get("messageid") else None,
                cost=r.get("cost"),
                sent_by=user.username,
                kind="broadcast",
                campaign=campaign,
            ))
        await db.commit()
    except Exception as e:
        logger.warning(f"[sms] broadcast log write failed: {e}")
        await db.rollback()

    return {"ok": result.get("success"), "campaign": campaign,
            "sent": result.get("sent"), "failed": result.get("failed"),
            "total": len(numbers), "error": result.get("error")}


# ── history ─────────────────────────────────────────────────────────────────

@router.get("/messages")
async def sms_messages(limit: int = Query(50, le=200), offset: int = 0,
                       campaign: Optional[str] = None,
                       status: Optional[str] = None,
                       search: Optional[str] = None,
                       db: AsyncSession = Depends(get_db),
                       _: User = Depends(get_current_user)):
    q = select(SmsLog)
    if campaign:
        q = q.where(SmsLog.campaign == campaign)
    if status:
        q = q.where(SmsLog.status == status)
    if search:
        n = normalize_mobile(search)
        q = q.where(SmsLog.to_number == n) if n else q.where(
            SmsLog.message.ilike(f"%{search}%"))

    total = (await db.execute(
        select(func.count()).select_from(q.subquery()))).scalar() or 0
    rows = (await db.execute(
        q.order_by(SmsLog.sent_at.desc()).limit(limit).offset(offset))).scalars().all()
    return {"total": total, "items": [r.to_dict() for r in rows]}


@router.get("/campaigns")
async def sms_campaigns(db: AsyncSession = Depends(get_db),
                        _: User = Depends(get_current_user)):
    """Broadcasts, newest first, with their totals."""
    rows = (await db.execute(
        select(SmsLog.campaign,
               func.count().label("total"),
               func.sum(func.coalesce(SmsLog.cost, 0)).label("cost"),
               func.max(SmsLog.sent_at).label("at"))
        .where(SmsLog.campaign.isnot(None))
        .group_by(SmsLog.campaign)
        .order_by(func.max(SmsLog.sent_at).desc())
        .limit(40))).all()
    return {"campaigns": [
        {"campaign": r[0], "total": r[1], "cost": int(r[2] or 0),
         "at": r[3].isoformat() if r[3] else None} for r in rows]}


@router.post("/messages/refresh-status")
async def refresh_delivery(limit: int = Query(100, le=200),
                           campaign: Optional[str] = None,
                           db: AsyncSession = Depends(get_db),
                           _: User = Depends(get_current_user)):
    """Ask Kavenegar what happened to messages we have not settled yet.

    On demand rather than on a timer: a broadcast of several thousand would
    otherwise generate the same number of status calls that nobody reads.
    Already-final states are skipped, so pressing it twice costs nothing.
    """
    q = (select(SmsLog)
         .where(SmsLog.message_id.isnot(None),
                or_(SmsLog.delivery_status.is_(None),
                    SmsLog.delivery_status.notin_(list(sms.DELIVERY_FINAL)))))
    if campaign:
        q = q.where(SmsLog.campaign == campaign)
    rows = (await db.execute(
        q.order_by(SmsLog.sent_at.desc()).limit(limit))).scalars().all()

    if not rows:
        return {"ok": True, "checked": 0, "updated": 0}

    by_id = {r.message_id: r for r in rows}
    try:
        states = await sms.delivery_status(list(by_id.keys()), db)
    except sms.SmsError as e:
        return {"ok": False, "error": e.message, "status": e.status}

    now = datetime.now(timezone.utc)
    updated = 0
    for s in states:
        row = by_id.get(str(s.get("messageid")))
        if not row:
            continue
        row.delivery_status = s["status"]
        row.delivery_text = s["status_text"]
        row.delivery_checked_at = now
        updated += 1
    await db.commit()
    return {"ok": True, "checked": len(rows), "updated": updated}


@router.get("/stats")
async def sms_stats(db: AsyncSession = Depends(get_db),
                    _: User = Depends(get_current_user)):
    """The numbers on the panel's cards."""
    since = datetime.now(timezone.utc) - timedelta(days=30)

    total = (await db.execute(select(func.count(SmsLog.id)))).scalar() or 0
    month = (await db.execute(
        select(func.count(SmsLog.id)).where(SmsLog.sent_at >= since))).scalar() or 0
    failed = (await db.execute(
        select(func.count(SmsLog.id)).where(SmsLog.status == "failed",
                                            SmsLog.sent_at >= since))).scalar() or 0
    cost = (await db.execute(
        select(func.sum(func.coalesce(SmsLog.cost, 0)))
        .where(SmsLog.sent_at >= since))).scalar() or 0
    delivered = (await db.execute(
        select(func.count(SmsLog.id)).where(SmsLog.delivery_status == 10,
                                            SmsLog.sent_at >= since))).scalar() or 0

    return {
        "total": total,
        "last_30_days": month,
        "failed_30_days": failed,
        "delivered_30_days": delivered,
        "cost_30_days": int(cost),
        # A rate over a tiny sample is noise, so say so rather than print 0%.
        "success_rate": round((month - failed) / month * 100, 1) if month >= 10 else None,
    }
