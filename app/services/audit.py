"""
The audit trail: چه کسی، چه کاری، کِی. One row per staff action worth a
record — login, password/2FA changes, user management, Divar number
ownership, backups, provider-settings saves.

Modelled on app/services/sms_log.py and job_log.py, and for the same two
reasons:

* **Its own session, always.** A caller is usually mid-transaction — deleting
  a user, changing a setting. A failing audit INSERT inside that transaction
  would abort it and take the actual action down with it, turning a logging
  problem into an outage.
* **Never raises.** Whatever this is recording must still happen if the audit
  write itself fails.
"""
import re
from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import delete

from app.database import async_session_maker
from app.log_redaction import request_id_var

RETENTION_DAYS = 365

# key names that make a detail value worth dropping rather than storing —
# case-insensitive substring, deliberately broad: over-redacting a field like
# "keyword" costs nothing, a leaked one does not un-leak.
_SECRET_KEY_RE = re.compile(
    r"(password|token|secret|key|code|otp|cookie|passphrase|authorization)", re.I)


def _strip_secrets(value):
    """Recursively drop dict keys that look like they hold a secret."""
    if isinstance(value, dict):
        return {k: _strip_secrets(v) for k, v in value.items()
                if not _SECRET_KEY_RE.search(k)}
    if isinstance(value, list):
        return [_strip_secrets(v) for v in value]
    return value


async def record(action: str, *, actor=None, target_type: str = None,
                 target_id=None, summary: str = "", detail: dict = None,
                 request=None, db=None) -> bool:
    """Write one audit row. Returns True if it was stored.

    `actor` is a User (or anything with .id/.username/.role — a snapshot is
    taken, not a live reference: see app/models/audit_event.py) or None for
    an attempt against an unknown identity. `db` is accepted for call-site
    convenience — most callers already have a session in scope — but never
    used: see the module docstring for why this always opens its own.
    """
    from app.models.audit_event import AuditEvent
    from app.services.verification import client_ip

    try:
        async with async_session_maker() as session:
            session.add(AuditEvent(
                actor_user_id=getattr(actor, "id", None),
                actor_username=getattr(actor, "username", None),
                actor_role=getattr(actor, "role", None),
                action=action,
                target_type=target_type,
                target_id=str(target_id) if target_id is not None else None,
                summary=(summary or "")[:300],
                detail=_strip_secrets(detail) if detail else None,
                ip=client_ip(request),
                request_id=request_id_var.get(),
            ))
            await session.commit()
        return True
    except Exception as e:
        logger.warning(f"[audit] could not record {action!r}: {type(e).__name__}: {e}")
        return False


async def prune(days: int = RETENTION_DAYS) -> int:
    """Drop events older than `days`. Called once a day from app/main.py."""
    from app.models.audit_event import AuditEvent

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    try:
        async with async_session_maker() as session:
            res = await session.execute(
                delete(AuditEvent).where(AuditEvent.created_at < cutoff))
            await session.commit()
            return res.rowcount or 0
    except Exception as e:
        logger.warning(f"[audit] prune skipped: {type(e).__name__}: {e}")
        return 0


# action key -> Persian label, for GET /api/audit/actions (the panel's filter
# dropdown). One place, so a new action and its label land together.
ACTIONS = {
    "login_success": "ورود موفق",
    "login_failed": "ورود ناموفق",
    "password_change": "تغییر رمز عبور",
    "totp_enable": "فعال‌سازی احراز هویت دومرحله‌ای (برنامه)",
    "totp_disable": "غیرفعال‌سازی احراز هویت دومرحله‌ای (برنامه)",
    "email_2fa_enable": "فعال‌سازی احراز هویت دومرحله‌ای (ایمیل)",
    "email_2fa_disable": "غیرفعال‌سازی احراز هویت دومرحله‌ای (ایمیل)",
    "user_create": "ایجاد کاربر",
    "user_update": "ویرایش کاربر",
    "user_delete": "حذف کاربر",
    "user_verification_set": "تنظیم دستی تأیید شماره/ایمیل",
    "user_password_reset_admin": "بازنشانی رمز کاربر توسط مدیر",
    "user_totp_disable_admin": "غیرفعال‌سازی اجباری ۲FA کاربر",
    "divar_number_owner_change": "تغییر مالک شمارهٔ دیوار",
    "divar_session_delete": "حذف نشست دیوار",
    "backup_run": "اجرای دستی بکاپ",
    "dr_run": "اجرای دستی بکاپ کامل (DR)",
    "sms_settings_save": "ذخیرهٔ تنظیمات پیامک",
    "email_settings_save": "ذخیرهٔ تنظیمات ایمیل",
    "ai_settings_save": "ذخیرهٔ تنظیمات هوش مصنوعی",
    "ai_agent_toggle": "روشن/خاموش کردن ایجنت هوش مصنوعی",
    "ai_agent_cap_set": "تغییر سقف هزینهٔ ایجنت",
    "telegram_link": "اتصال حساب تلگرام به سورین",
    "telegram_unlink": "قطع اتصال تلگرام از سورین",
    "crm_export": "خروجی گرفتن از CRM",
    "crm_delete": "حذف در CRM",
    "leads_bulk": "تغییر یا حذف گروهی لیدها",
    "portal_ticket_decide": "تصمیم دربارهٔ درخواست ارتقای پرتال",
}
