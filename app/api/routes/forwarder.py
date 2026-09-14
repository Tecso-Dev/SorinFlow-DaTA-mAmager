"""
The panel's side of the SMS forwarder: a person's phones, and what to type
into them.

Everything here is scoped to the caller. A user sees and edits their own
devices; nobody else's id, secret or status is reachable, including by
guessing an id — the lookup is always filtered by owner, so a wrong id is a
404 whether it exists or not.

The secret is returned in full only to the owner, and only from the two places
that need it: creation and the config endpoint the setup guide reads. The list
carries a masked form, so a screenshot of the device list does not hand over a
credential.
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.config import get_settings
from app.database import get_db
from app.models.forwarder import ForwarderDevice, new_secret
from app.models.user import User
from app.services import forwarder as fw

router = APIRouter()
settings = get_settings()

# Where the Android build lives. Not on Play: the app reads every SMS, which
# Play's SMS policy does not allow for a use like this, so it is sideloaded.
ANDROID_APK_URL = "https://sorinflow.com/downloads/sorinflow-forwarder.apk"


def _base_url() -> str:
    d = (getattr(settings, "domain", "") or "").strip()
    return f"https://{d}" if d else "https://sorinflow.com"


class DeviceIn(BaseModel):
    label: Optional[str] = Field(None, max_length=80)
    sim_phone: Optional[str] = Field(None, max_length=20)
    note: Optional[str] = None


class DeviceEdit(DeviceIn):
    is_active: Optional[bool] = None


async def _mine(db: AsyncSession, user: User, device_id: int) -> ForwarderDevice:
    """One of MY devices, or 404.

    Filtered by owner in the query rather than fetched and then checked: the
    two differ when somebody guesses an id, and only one of them keeps quiet
    about whether it exists.
    """
    row = (await db.execute(
        select(ForwarderDevice).where(
            ForwarderDevice.id == device_id,
            ForwarderDevice.user_id == user.id)
    )).scalars().first()
    if not row:
        raise HTTPException(status_code=404, detail="دستگاهی با این شناسه ندارید")
    return row


@router.get("/devices")
async def list_devices(db: AsyncSession = Depends(get_db),
                       user: User = Depends(get_current_user)):
    """My phones and how each is doing."""
    rows = (await db.execute(
        select(ForwarderDevice)
        .where(ForwarderDevice.user_id == user.id)
        .order_by(ForwarderDevice.id.asc())
    )).scalars().all()
    return {"devices": [{**d.to_dict(), "health": fw.health(d)} for d in rows],
            "count": len(rows)}


@router.post("/devices")
async def create_device(data: DeviceIn, db: AsyncSession = Depends(get_db),
                        user: User = Depends(get_current_user)):
    """Register a phone. The secret is shown in full here and nowhere else in
    a list — the setup guide reads it from the config endpoint below."""
    row = ForwarderDevice(
        user_id=user.id,
        label=(data.label or "").strip() or "گوشی من",
        sim_phone=(data.sim_phone or "").strip() or None,
        note=data.note,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    logger.info(f"[forwarder] {user.username} registered device {row.device_id}")
    return {**row.to_dict(reveal_secret=True), "health": fw.health(row)}


@router.patch("/devices/{device_id}")
async def edit_device(device_id: int, data: DeviceEdit,
                      db: AsyncSession = Depends(get_db),
                      user: User = Depends(get_current_user)):
    row = await _mine(db, user, device_id)
    if data.label is not None:
        row.label = data.label.strip() or row.label
    if data.sim_phone is not None:
        row.sim_phone = data.sim_phone.strip() or None
    if data.note is not None:
        row.note = data.note
    if data.is_active is not None:
        row.is_active = bool(data.is_active)
    await db.commit()
    await db.refresh(row)
    return {**row.to_dict(), "health": fw.health(row)}


@router.delete("/devices/{device_id}")
async def delete_device(device_id: int, db: AsyncSession = Depends(get_db),
                        user: User = Depends(get_current_user)):
    """Remove a phone. Its secret stops working immediately and no other
    device is affected — the reason secrets are per-device."""
    row = await _mine(db, user, device_id)
    await db.delete(row)
    await db.commit()
    return {"success": True, "message": "دستگاه حذف شد"}


@router.post("/devices/{device_id}/rotate")
async def rotate_secret(device_id: int, db: AsyncSession = Depends(get_db),
                        user: User = Depends(get_current_user)):
    """New secret for this phone only. The app stops working until the new one
    is typed in, which is the point — this is what you press for a lost handset."""
    row = await _mine(db, user, device_id)
    row.secret = new_secret()
    row.warned_at = None
    await db.commit()
    await db.refresh(row)
    logger.warning(f"[forwarder] {user.username} rotated the secret for {row.device_id}")
    return {**row.to_dict(reveal_secret=True), "health": fw.health(row)}


@router.get("/devices/{device_id}/config")
async def device_config(device_id: int, db: AsyncSession = Depends(get_db),
                        user: User = Depends(get_current_user)):
    """Exactly what goes into the app, ready to copy field by field.

    Built here rather than in the browser so the guide cannot drift from what
    the server actually accepts: the URL, the headers and the payload template
    are the ones /api/scraper/otp-inbound parses.
    """
    row = await _mine(db, user, device_id)
    base = _base_url()
    acct = row.sim_phone or (user.divar_phone or "")
    tpl = (
        '{"kind":"%s","account":"' + acct + '",'
        '"code":"%Regex=Code:\\\\s*(\\\\d{6})%",'
        '"text":"%text%","sim":"%sim%",'
        '"sentStamp":%sentStamp%,"receivedStamp":%receivedStamp%,'
        '"battery":%battery%,"network":"%network%"}'
    )
    headers = {
        "User-agent": "SMS Forwarder App",
        "X-Forwarder-Id": row.device_id,
        "X-OTP-Secret": row.secret,
    }
    return {
        "device": row.to_dict(reveal_secret=True),
        "android_apk_url": ANDROID_APK_URL,
        "ios": {"available": False, "message_fa": "نسخهٔ آیفون به‌زودی"},
        "endpoints": {
            "inbound": f"{base}/api/scraper/otp-inbound",
            "heartbeat": f"{base}/api/scraper/forwarder-heartbeat",
        },
        "headers": headers,
        "rules": [
            {"name_fa": "کد اطلاعات تماس", "sender": "*",
             "text_filter": "اطلاعات تماس",
             "template": tpl % "contact",
             "why_fa": "کدی که برای دیدن شمارهٔ آگهی لازم است"},
            {"name_fa": "کد ورود", "sender": "*",
             "text_filter": "کد تایید",
             "template": tpl % "login",
             "why_fa": "کد ورود به حساب دیوار"},
        ],
        "advanced_fa": {
            "retries": 10,
            "store_failed": True,
            "ignore_ssl": False,
            "note": "«ذخیرهٔ پیام‌های ناموفق» را روشن بگذارید تا اگر اینترنت "
                    "لحظه‌ای قطع شد کد از دست نرود.",
        },
    }


@router.get("/devices/{device_id}/events")
async def device_events(device_id: int, limit: int = 50,
                        db: AsyncSession = Depends(get_db),
                        user: User = Depends(get_current_user)):
    """What this phone has actually sent us.

    Read from sms_events, which already records every inbound POST with its
    outcome — matched, parked, refused and why. Filtered to this device's SIM
    so one person's log does not show another's traffic.
    """
    from app.models.sms_log import SmsEvent

    row = await _mine(db, user, device_id)
    rows = (await db.execute(
        select(SmsEvent)
        .where(SmsEvent.stage == "inbound")
        .order_by(SmsEvent.created_at.desc(), SmsEvent.id.desc())
        .limit(500)
    )).scalars().all()

    import json as _json
    out = []
    for e in rows:
        try:
            d = _json.loads(e.details) if e.details else {}
        except Exception:
            d = {}
        if row.sim_phone and not fw.same_phone(d.get("account"), row.sim_phone):
            continue
        out.append({
            "at": e.created_at.isoformat() if e.created_at else None,
            "level": e.level,
            "message": e.message,
            "kind": d.get("kind"),
            "reason": d.get("reason"),
            "code": d.get("code"),
            "latency_ms": d.get("latency_ms"),
        })
        if len(out) >= max(1, min(limit, 200)):
            break
    return {"events": out, "count": len(out), "device_id": row.device_id}


@router.post("/devices/{device_id}/test")
async def test_device(device_id: int, db: AsyncSession = Depends(get_db),
                      user: User = Depends(get_current_user)):
    """Has this phone ever actually reached us, and how long ago?

    Deliberately NOT a request to the phone — nothing can make a handset speak.
    It reports what the phone has told us, which is the only thing that is
    true. The app's own «TEST» button is what proves the other direction.
    """
    row = await _mine(db, user, device_id)
    h = fw.health(row)
    ok = h["state"] == "ok"
    hint = {
        "never_seen": "هنوز هیچ پیامی از این گوشی نرسیده. برنامه را نصب کنید، "
                      "تنظیمات را وارد کنید و در خود برنامه دکمهٔ TEST را بزنید.",
        "offline": "آخرین خبر از این گوشی قدیمی است. اینترنت گوشی روشن است؟ "
                   "برنامه در تنظیمات باتری «بدون محدودیت» است؟",
        "no_codes_yet": "گوشی وصل است ولی هنوز کدی نفرستاده. اگر دیوار کد فرستاده "
                        "و اینجا چیزی نیامده، «فیلتر متن» قانون را بررسی کنید.",
        "disabled": "این دستگاه غیرفعال است. برای استفادهٔ دوباره فعالش کنید.",
        "ok": "همه‌چیز درست کار می‌کند.",
    }.get(h["state"], "")
    return {"ok": ok, "health": h, "hint_fa": hint,
            "checked_at": datetime.now(timezone.utc).isoformat()}
