"""
ایمیل — the email panel.

SMTP settings, a connection check, a test send, template previews and the
delivery log.

Two deliberate separations:

  * **Checking credentials and sending a test are different buttons.** The
    check logs in and hangs up; it answers "is this password right", which is
    the question being asked while typing one, and it costs nobody an email.
  * **The password is written, never read.** It comes back as `abcd****wxyz`
    and only to super_admin. There is no endpoint that returns it whole.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, _role_dep
from app.auth.permissions import ROLE_ROOT, ROLE_SUPER_ADMIN
from app.database import get_db
from app.models.email_log import EmailLog
from app.models.user import User
from app.services import email_service as mail
from app.services import email_templates as tpl
from app.services import secret_box

router = APIRouter()
_super_admin = Depends(_role_dep(ROLE_ROOT, ROLE_SUPER_ADMIN))


async def log_email(db, to: str, subject: str, template: str, result: dict,
                    actor: str = "") -> None:
    """Record one send. Never raises — a logging failure must not be mistaken
    for a delivery failure."""
    try:
        db.add(EmailLog(
            to_email=to[:255],
            subject=(subject or "")[:300],
            template=template,
            status="sent" if result.get("success") else "failed",
            error=(result.get("error") or "")[:2000] or None,
            message_id=(result.get("message_id") or "")[:255] or None,
            sent_by=actor or None,
        ))
        await db.commit()
    except Exception as e:
        logger.warning(f"[email] could not write the send log: {e}")
        try:
            await db.rollback()
        except Exception:
            pass


# ── settings ────────────────────────────────────────────────────────────────

class EmailSettingsIn(BaseModel):
    host: Optional[str] = None
    port: Optional[int] = None
    user: Optional[str] = None
    password: Optional[str] = Field(None, description="write-only; omit to keep the current one")
    from_name: Optional[str] = None
    reply_to: Optional[str] = None
    from_email: Optional[str] = None
    security: Optional[str] = Field(None, description="starttls | ssl | none")
    enabled: Optional[bool] = None


@router.get("/settings")
async def get_email_settings(db: AsyncSession = Depends(get_db),
                             _: User = _super_admin):
    cfg = await mail.resolve_config(db)
    return {
        "host": cfg["host"],
        "port": cfg["port"],
        "user": cfg["user"],
        "password_masked": secret_box.mask(cfg["password"]),
        "configured": bool(cfg["host"] and cfg["user"] and cfg["password"]),
        "password_source": cfg["source"],
        "from_name": cfg["from_name"],
        "reply_to": cfg["reply_to"],
        "from_email": cfg.get("from_email", ""),
        "security": cfg["security"],
        "enabled": cfg["enabled"],
    }


@router.put("/settings")
async def put_email_settings(payload: EmailSettingsIn,
                             db: AsyncSession = Depends(get_db),
                             user: User = _super_admin):
    actor = user.username

    if payload.security is not None and payload.security not in ("starttls", "ssl", "none"):
        raise HTTPException(400, "نوع رمزگذاری نامعتبر است")
    if payload.port is not None and not (1 <= payload.port <= 65535):
        raise HTTPException(400, "پورت نامعتبر است")
    if payload.reply_to and not mail.valid_email(payload.reply_to):
        raise HTTPException(400, "آدرس پاسخ نامعتبر است")
    if payload.from_email and not mail.valid_email(payload.from_email):
        raise HTTPException(400, "آدرس فرستنده نامعتبر است")

    if payload.password is not None:
        pw = payload.password.strip()
        if pw:
            await secret_box.put(db, mail.KEY_PASSWORD, secret_box.encrypt(pw), actor)
            logger.info(f"[email] SMTP password updated by {actor}")
        else:
            # An empty string is "forget it" — how a saved password is removed.
            await secret_box.put(db, mail.KEY_PASSWORD, None, actor)
            logger.info(f"[email] SMTP password cleared by {actor}")

    for value, key in ((payload.host, mail.KEY_HOST),
                       (payload.user, mail.KEY_USER),
                       (payload.from_name, mail.KEY_FROM_NAME),
                       (payload.reply_to, mail.KEY_REPLY_TO),
                       (payload.from_email, mail.KEY_FROM_EMAIL),
                       (payload.security, mail.KEY_SECURITY)):
        if value is not None:
            await secret_box.put(db, key, str(value).strip() or None, actor)

    if payload.port is not None:
        await secret_box.put(db, mail.KEY_PORT, str(payload.port), actor)
    if payload.enabled is not None:
        await secret_box.put(db, mail.KEY_ENABLED,
                             "true" if payload.enabled else "false", actor)

    return await get_email_settings(db, user)


@router.post("/verify")
async def verify_smtp(db: AsyncSession = Depends(get_db), _: User = _super_admin):
    """Log in to the SMTP server and hang up. Sends nothing."""
    return await mail.verify_connection(db)


@router.post("/test")
async def send_test(to: str = Query(..., description="recipient"),
                    db: AsyncSession = Depends(get_db),
                    user: User = _super_admin):
    """Send the styled test message — proves SMTP, templates, Persian and RTL."""
    if not mail.valid_email(to):
        raise HTTPException(400, "آدرس ایمیل معتبر نیست")
    subject, html, text = tpl.test_message()
    result = await mail.send(to, subject, html, text, db=db)
    await log_email(db, to, subject, "test", result, user.username)
    if not result.get("success"):
        return {"ok": False, "error": result.get("error"), "detail": result.get("detail")}
    return {"ok": True, "message_id": result.get("message_id")}


# ── sending ─────────────────────────────────────────────────────────────────

class SendIn(BaseModel):
    to: str
    subject: str = Field(..., min_length=1, max_length=200)
    message: str = Field(..., min_length=1, max_length=4000)
    cta_label: Optional[str] = None
    cta_url: Optional[str] = None


@router.post("/send")
async def send_one(payload: SendIn, db: AsyncSession = Depends(get_db),
                   user: User = Depends(get_current_user)):
    """A one-off message, rendered through the site's notification template so
    it looks like everything else the system sends."""
    if not mail.valid_email(payload.to):
        raise HTTPException(400, "آدرس ایمیل معتبر نیست")

    _, html, text = tpl.notification(
        payload.subject, payload.message,
        cta_label=payload.cta_label or "", cta_url=payload.cta_url or "")
    result = await mail.send(payload.to, payload.subject, html, text, db=db)
    await log_email(db, payload.to, payload.subject, "notification", result, user.username)
    if not result.get("success"):
        return {"ok": False, "error": result.get("error")}
    return {"ok": True, "message_id": result.get("message_id")}


# ── templates ───────────────────────────────────────────────────────────────

_SAMPLES = {
    "login_code": lambda: tpl.login_code("۸۳۹۲۴۱".translate(
        str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")), name="سبحان"),
    "welcome": lambda: tpl.welcome("سبحان"),
    "ticket_decision": lambda: tpl.ticket_decision("سبحان", True, "خوش آمدید به تیم"),
    "request_received": lambda: tpl.request_received(
        "سبحان", "آپارتمان ۲ خوابه، تهران — سعادت‌آباد، بودجه تا ۸ میلیارد تومان"),
    "notification": lambda: tpl.notification(
        "یک ملک منطبق پیدا شد", "ملکی مطابق با درخواست شما ثبت شده است.",
        cta_label="مشاهدهٔ ملک", cta_url="https://sorinflow.com/portal"),
    "test": lambda: tpl.test_message(),
}


@router.get("/templates")
async def list_templates(_: User = Depends(get_current_user)):
    return {"templates": [{"key": k, "label": v} for k, v in tpl.CATALOG.items()]}


@router.get("/preview/{name}", response_class=HTMLResponse)
async def preview_template(name: str, _: User = Depends(get_current_user)):
    """Render a template with sample data, so the panel can show it.

    Returned as a document rather than JSON because the panel drops it straight
    into an iframe — seeing the real thing beats a description of it.
    """
    if name not in _SAMPLES:
        raise HTTPException(404, "قالب یافت نشد")
    _, html, _text = _SAMPLES[name]()
    return HTMLResponse(html)


# ── history ─────────────────────────────────────────────────────────────────

@router.get("/messages")
async def list_messages(limit: int = Query(50, le=200), offset: int = 0,
                        template: Optional[str] = None,
                        status: Optional[str] = None,
                        db: AsyncSession = Depends(get_db),
                        _: User = Depends(get_current_user)):
    q = select(EmailLog)
    if template:
        q = q.where(EmailLog.template == template)
    if status:
        q = q.where(EmailLog.status == status)
    total = (await db.execute(
        select(func.count()).select_from(q.subquery()))).scalar() or 0
    rows = (await db.execute(
        q.order_by(EmailLog.created_at.desc()).limit(limit).offset(offset))).scalars().all()
    return {"total": total, "items": [r.to_dict() for r in rows]}


@router.get("/stats")
async def email_stats(db: AsyncSession = Depends(get_db),
                      _: User = Depends(get_current_user)):
    since = datetime.now(timezone.utc) - timedelta(days=30)
    total = (await db.execute(select(func.count(EmailLog.id)))).scalar() or 0
    month = (await db.execute(select(func.count(EmailLog.id))
                              .where(EmailLog.created_at >= since))).scalar() or 0
    failed = (await db.execute(select(func.count(EmailLog.id))
                               .where(EmailLog.status == "failed",
                                      EmailLog.created_at >= since))).scalar() or 0
    codes = (await db.execute(select(func.count(EmailLog.id))
                              .where(EmailLog.template == "login_code",
                                     EmailLog.created_at >= since))).scalar() or 0
    return {
        "total": total,
        "last_30_days": month,
        "failed_30_days": failed,
        "login_codes_30_days": codes,
        # A rate over a handful of sends is noise; say nothing rather than 0%.
        "success_rate": round((month - failed) / month * 100, 1) if month >= 10 else None,
    }
