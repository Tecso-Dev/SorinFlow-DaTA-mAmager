"""
SorinFlow CRM — Notification service
Supports Telegram bot and SMTP email.
"""
import asyncio
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from loguru import logger
import httpx

from app.config import get_settings
from app.models.property import Property
from app.models.lead import Lead

settings = get_settings()


def _format_price(price: int | None) -> str:
    if not price:
        return "توافقی"
    return f"{price:,} تومان"


def _build_message(prop: Property, lead: Lead) -> str:
    price_label = "قیمت اجاره" if prop.listing_type == "rent" else "قیمت"
    price_val = _format_price(prop.rent_price or prop.total_price or prop.price)

    lines = [
        "🏠 آگهی جدید اسکرپ شد",
        "",
        f"📌 عنوان: {prop.title}",
        f"🏙 شهر: {prop.city_name or '—'}",
        f"📂 دسته: {prop.category_name or '—'}",
        f"💰 {price_label}: {price_val}",
    ]
    if prop.area:
        lines.append(f"📐 متراژ: {prop.area} متر")
    if prop.rooms is not None:
        lines.append(f"🛏 اتاق: {prop.rooms}")
    if prop.phone_number:
        lines.append(f"📞 شماره: {prop.phone_number}")
    lines += [
        f"🔗 لینک: {prop.url}",
        f"🔖 تگ: {prop.tag_number}",
        f"🆔 لید: #{lead.id}",
    ]
    return "\n".join(lines)


async def send_telegram(prop: Property, lead: Lead) -> bool:
    """Send a Telegram notification via Bot API."""
    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id

    if not token or not chat_id:
        logger.debug("Telegram not configured, skipping notification")
        return False

    message = _build_message(prop, lead)
    url = f"https://api.telegram.org/bot{token}/sendMessage"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            })
        if resp.status_code == 200:
            logger.info(f"Telegram notification sent for lead #{lead.id}")
            return True
        logger.warning(f"Telegram API returned {resp.status_code}: {resp.text}")
        return False
    except Exception as e:
        logger.error(f"Telegram notification failed for lead #{lead.id}: {e}")
        return False


def _sync_send_email(prop: Property, lead: Lead) -> bool:
    host = settings.smtp_host
    port = settings.smtp_port
    user = settings.smtp_user
    password = settings.smtp_password
    recipient = settings.notification_email

    if not all([host, user, password, recipient]):
        logger.debug("Email not configured, skipping notification")
        return False

    subject = f"آگهی جدید: {prop.title[:60]}"
    body = _build_message(prop, lead).replace("\n", "<br>")
    html = f"<div dir='rtl' style='font-family:Tahoma'>{body}</div>"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = recipient
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        with smtplib.SMTP(host, port, timeout=10) as server:
            server.starttls()
            server.login(user, password)
            server.sendmail(user, recipient, msg.as_string())
        logger.info(f"Email notification sent for lead #{lead.id}")
        return True
    except Exception as e:
        logger.error(f"Email notification failed for lead #{lead.id}: {e}")
        return False


async def send_email(prop: Property, lead: Lead) -> bool:
    """Send an email notification via SMTP (runs in a thread to avoid blocking the event loop)."""
    return await asyncio.to_thread(_sync_send_email, prop, lead)


async def notify(prop: Property, lead: Lead) -> str:
    """Send notifications via all configured channels concurrently.
    Returns comma-separated names of channels that succeeded, or 'none'.
    """
    telegram_ok, email_ok = await asyncio.gather(
        send_telegram(prop, lead),
        send_email(prop, lead),
        return_exceptions=True,
    )

    channels = []
    if telegram_ok is True:
        channels.append("telegram")
    if email_ok is True:
        channels.append("email")

    return ",".join(channels) or "none"
