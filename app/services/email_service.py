"""
SorinFlow — email.

Login codes, welcome messages and notifications, plus the panel that manages
them. Templates live in app/services/email_templates.py.

## Why stdlib smtplib and not an async client

`aiosmtplib` is not installed and this does not justify adding it. Sending is
rare, bounded, and already has a working pattern in this repo:
app/crm/notification.py runs smtplib inside `asyncio.to_thread`, which keeps
the event loop free without a new dependency. Same approach here.

## Gmail

`sorinflow.agency@gmail.com` cannot be used with its account password — Google
stopped accepting those for SMTP. It needs a 16-character **App Password**,
which requires 2-Step Verification on the account first. Host
`smtp.gmail.com`, port 587 with STARTTLS, or 465 with implicit TLS. Both work;
587 is the default here because it survives more restrictive outbound
firewalls, which matters on an Iranian VPS.

## Where the password lives

`SMTP_PASSWORD` in the environment (the Kubernetes Secret) wins. If it is
absent, the value saved from the panel is used — encrypted via secret_box,
never returned to the browser. The environment is still the right home in
production; the panel exists so this can be set up without a deploy.

## Fail-closed

send() returns {"success": bool, ...} and never raises. Verification depends on
that contract: app/services/verification.py burns the one-time code when
delivery fails, so a code nobody received is never left alive. An exception
escaping this module would skip that.
"""
import asyncio
import re
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, make_msgid
from typing import Optional

from loguru import logger

from app.config import get_settings
from app.services import secret_box

settings = get_settings()

# ── panel-editable settings ─────────────────────────────────────────────────
KEY_HOST = "email_smtp_host"
KEY_PORT = "email_smtp_port"
KEY_USER = "email_smtp_user"
KEY_PASSWORD = "email_smtp_password"        # encrypted
KEY_FROM_NAME = "email_from_name"
KEY_SECURITY = "email_security"             # starttls | ssl | none
KEY_ENABLED = "email_enabled"
KEY_REPLY_TO = "email_reply_to"
# The address recipients see. Gmail only permits an alias that has been
# verified under "Send mail as"; anything else is silently rewritten back to
# the account address, or rejected outright.
KEY_FROM_EMAIL = "email_from_email"

ALL_KEYS = [KEY_HOST, KEY_PORT, KEY_USER, KEY_PASSWORD,
            KEY_FROM_NAME, KEY_SECURITY, KEY_ENABLED, KEY_REPLY_TO,
            KEY_FROM_EMAIL]

DEFAULT_FROM_NAME = "سورین‌فلو"
TIMEOUT = 20

# Deliberately permissive: the job here is to catch a typo before it reaches
# the SMTP conversation, not to adjudicate RFC 5322. The repo already hand-rolls
# an EMAIL_RE in app/schemas for the same reason (no email-validator installed).
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


def valid_email(addr: str) -> bool:
    return bool(addr and _EMAIL.match(addr.strip()))


class EmailError(Exception):
    """Delivery failed, with a message already written for a Persian panel."""

    def __init__(self, message: str, detail: str = ""):
        self.message = message
        self.detail = detail
        super().__init__(message if not detail else f"{message} ({detail})")


def _explain(exc: Exception) -> str:
    """Turn an smtplib exception into something an operator can act on.

    "SMTPAuthenticationError" tells nobody whether to fix the password, turn on
    2-Step Verification, or open a port.
    """
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return ("نام کاربری یا رمز عبور SMTP پذیرفته نشد. برای Gmail باید از "
                "«App Password» استفاده کنید، نه رمز عبور حساب.")
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return "آدرس گیرنده از سوی سرور رد شد."
    if isinstance(exc, smtplib.SMTPSenderRefused):
        return "آدرس فرستنده از سوی سرور رد شد — معمولاً باید با نام کاربری یکی باشد."
    if isinstance(exc, smtplib.SMTPConnectError):
        return "اتصال به سرور SMTP برقرار نشد — میزبان و پورت را بررسی کنید."
    if isinstance(exc, smtplib.SMTPServerDisconnected):
        return "سرور SMTP ارتباط را قطع کرد — نوع رمزگذاری (STARTTLS/SSL) را بررسی کنید."
    if isinstance(exc, (TimeoutError, OSError)):
        return "سرور SMTP در زمان مقرر پاسخ نداد — پورت ممکن است بسته باشد."
    return f"خطای غیرمنتظره در ارسال ایمیل: {type(exc).__name__}"


async def resolve_config(db=None) -> dict:
    """Effective SMTP settings — environment first, then the panel."""
    cfg = {
        "host": (settings.smtp_host or "").strip(),
        "port": int(settings.smtp_port or 587),
        "user": (settings.smtp_user or "").strip(),
        "password": (settings.smtp_password or ""),
        "from_name": DEFAULT_FROM_NAME,
        "security": "starttls",
        "reply_to": "",
        "from_email": "",
        "enabled": True,
        "source": "env" if (settings.smtp_host and settings.smtp_password) else None,
    }

    if db is None:
        return cfg

    try:
        v = await secret_box.get_many(db, ALL_KEYS)
    except Exception as e:
        logger.warning(f"[email] saved settings unreadable: {e}")
        return cfg

    if not cfg["host"] and v.get(KEY_HOST):
        cfg["host"] = v[KEY_HOST].strip()
    if v.get(KEY_PORT):
        try:
            cfg["port"] = int(v[KEY_PORT])
        except ValueError:
            pass
    if not cfg["user"] and v.get(KEY_USER):
        cfg["user"] = v[KEY_USER].strip()
    if not cfg["password"] and v.get(KEY_PASSWORD):
        cfg["password"] = secret_box.decrypt(v[KEY_PASSWORD])
        if cfg["password"]:
            cfg["source"] = "panel"
    if v.get(KEY_FROM_NAME):
        cfg["from_name"] = v[KEY_FROM_NAME]
    if v.get(KEY_SECURITY):
        cfg["security"] = v[KEY_SECURITY]
    if v.get(KEY_REPLY_TO):
        cfg["reply_to"] = v[KEY_REPLY_TO]
    if v.get(KEY_FROM_EMAIL):
        cfg["from_email"] = v[KEY_FROM_EMAIL]
    if v.get(KEY_ENABLED) is not None:
        cfg["enabled"] = (v.get(KEY_ENABLED) or "true").lower() == "true"
    if cfg["source"] is None and cfg["host"] and cfg["password"]:
        cfg["source"] = "panel"
    return cfg


def _connect(cfg: dict):
    """Open an authenticated SMTP session. Caller closes it."""
    if cfg["security"] == "ssl":
        server = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=TIMEOUT,
                                  context=ssl.create_default_context())
    else:
        server = smtplib.SMTP(cfg["host"], cfg["port"], timeout=TIMEOUT)
        if cfg["security"] != "none":
            server.starttls(context=ssl.create_default_context())
    if cfg["user"]:
        server.login(cfg["user"], cfg["password"])
    return server


def _sync_send(cfg: dict, to: str, subject: str, html: str,
               text: str = "") -> str:
    """Blocking send. Returns the Message-ID."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    # The envelope sender stays the authenticated account — Gmail checks it
    # and a mismatch is rejected. Only the visible From header uses the
    # alias, which is what "Send mail as" authorises.
    msg["From"] = formataddr((str(cfg["from_name"]),
                              cfg.get("from_email") or cfg["user"]))
    msg["To"] = to
    if cfg.get("reply_to"):
        msg["Reply-To"] = cfg["reply_to"]
    mid = make_msgid(domain="sorinflow.com")
    msg["Message-ID"] = mid

    # Plain part first: a multipart/alternative is rendered last-part-wins, so
    # the HTML must come second or text-only clients win for everyone.
    msg.attach(MIMEText(text or _strip_html(html), "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    with _connect(cfg) as server:
        server.sendmail(cfg["user"], [to], msg.as_string())
    return mid


def _strip_html(html: str) -> str:
    """A crude text fallback, for when a template forgot to supply one.

    Every template here returns its own text part; this exists so a future one
    that does not still sends something readable rather than raw markup.
    """
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>|</p>|</div>|</h[1-6]>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


async def send(to: str, subject: str, html: str, text: str = "",
               db=None, cfg: Optional[dict] = None) -> dict:
    """Send one message. Never raises — see the module docstring."""
    if not valid_email(to):
        return {"success": False, "error": "آدرس ایمیل معتبر نیست"}

    cfg = cfg or await resolve_config(db)

    if not cfg["enabled"]:
        return {"success": False, "error": "ارسال ایمیل در تنظیمات غیرفعال است"}
    if not cfg["host"] or not cfg["user"]:
        return {"success": False, "error": "تنظیمات SMTP کامل نیست"}
    if not cfg["password"]:
        return {"success": False, "error": "رمز SMTP تنظیم نشده است"}

    try:
        mid = await asyncio.to_thread(_sync_send, cfg, to.strip(), subject, html, text)
    except Exception as e:
        why = _explain(e)
        # The password can appear in an smtplib repr; never let it reach a log.
        safe = str(e).replace(cfg["password"], "***") if cfg["password"] else str(e)
        logger.error(f"[email] send to {to} failed: {why} :: {safe[:200]}")
        return {"success": False, "error": why, "detail": safe[:400]}

    logger.info(f"[email] sent '{subject}' to {to}")
    return {"success": True, "message_id": mid}


async def verify_connection(db=None) -> dict:
    """Log in and hang up, without sending anything.

    Separate from the test message on purpose: this answers "are the
    credentials right", which is the question asked while typing them in, and
    it costs nobody an email.
    """
    cfg = await resolve_config(db)
    if not cfg["host"] or not cfg["user"]:
        return {"ok": False, "error": "تنظیمات SMTP کامل نیست"}
    if not cfg["password"]:
        return {"ok": False, "error": "رمز SMTP تنظیم نشده است"}

    def _check():
        with _connect(cfg) as server:
            return server.noop()[0]

    try:
        code = await asyncio.to_thread(_check)
        return {"ok": True, "host": cfg["host"], "port": cfg["port"],
                "user": cfg["user"], "security": cfg["security"], "code": code}
    except Exception as e:
        return {"ok": False, "error": _explain(e), "detail": str(e)[:300]}
