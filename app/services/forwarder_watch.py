"""
Telling the owner their phone stopped forwarding, before a run needs it.

A forwarder fails silently by nature. The phone reports success to itself, the
panel shows a prompt nobody answers, and the run parks for hours — and the one
person who could fix it in ten seconds is not looking at the panel. That is
exactly what happened on the first live test: a run sat paused for fifty
minutes while the alert went to a developer's inbox instead of the owner's.

So two notifications, and no more than that:

  * the phone went quiet — sent once per outage, when a device that has been
    delivering stops reporting;
  * a code was needed and the phone did not deliver it — sent from the OTP
    wait, where the failure actually bites.

Both name what to check, in the order a person would check it, because
«forwarder offline» tells somebody nothing they can act on.

Never raises. A notification that fails must not take a scrape with it.
"""
import asyncio
from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import select

from app.config import get_settings
from app.database import async_session_maker
from app.models.forwarder import ForwarderDevice
from app.models.user import User
from app.services import forwarder as fw

settings = get_settings()

# How often to look. A phone beats every five minutes and reads as offline
# after ten, so checking every five notices an outage within about fifteen —
# fast enough to matter, slow enough not to be noise.
CHECK_SECONDS = 300

# Only warn about a device that has actually worked. A phone registered an
# hour ago and never configured is not an outage, it is an unfinished setup,
# and the panel already says so.
REWARN_HOURS = 12


def _fa_diagnosis(state: str) -> tuple:
    """(what happened, what to do) — in the order a person would check it."""
    if state == "offline":
        return (
            "گوشی‌ای که کدهای دیوار را می‌فرستد دیگر خبری نمی‌دهد.",
            "۱) اینترنت گوشی روشن است؟\n"
            "۲) برنامهٔ فرستندهٔ پیامک هنوز نصب و باز است؟\n"
            "۳) در تنظیمات باتریِ گوشی، برای این برنامه «بدون محدودیت» را "
            "انتخاب کنید تا سیستم آن را نبندد.\n"
            "۴) اگر گوشی شیائومی است، «Autostart» را هم روشن کنید.",
        )
    if state == "no_codes_yet":
        return (
            "گوشی وصل است ولی تا حالا هیچ کدی نفرستاده.",
            "۱) در برنامه، «فیلتر متن» قانون باید «اطلاعات تماس» باشد.\n"
            "۲) دکمهٔ TEST داخل برنامه را بزنید و ببینید پاسخ می‌گیرد یا نه.\n"
            "۳) اگر پیامک دیوار در «پیام‌رسان» گوگل می‌آید، RCS را خاموش کنید "
            "— پیام RCS اصلاً پیامک نیست و برنامه آن را نمی‌بیند.",
        )
    return ("وضعیت فرستندهٔ پیامک درست نیست.", "تنظیمات دستگاه را در پنل بررسی کنید.")


async def _email(to: str, subject: str, body: str, db=None) -> bool:
    try:
        from app.services import email_service, email_templates
        from app.services.site_settings import read_site
        site_cfg = await read_site(db) if db is not None else None
        cta_url = f"https://{(settings.domain or 'sorinflow.com')}/dashboard/"
        subj, html, text = email_templates.notification(
            subject, body, cta_label="باز کردن پنل", cta_url=cta_url, site=site_cfg)
        res = await email_service.send(to, subj, html, text, db=db)
        return bool(res.get("success"))
    except Exception as e:
        logger.warning(f"[forwarder-watch] could not email {to}: {e}")
        return False


async def warn_owner(device: ForwarderDevice, health: dict, db) -> bool:
    """Tell this device's owner it stopped working. Once per outage."""
    owner = (await db.execute(
        select(User).where(User.id == device.user_id))).scalars().first()
    to = (getattr(owner, "email", "") or "").strip()
    if not to:
        logger.warning(
            f"[forwarder-watch] {device.device_id} is {health['state']} and its "
            f"owner has no email on file — nobody can be told")
        return False

    what, how = _fa_diagnosis(health["state"])
    since = health.get("seconds_since_seen")
    when = f"\n\nآخرین خبر: {int(since // 60)} دقیقه پیش." if since else ""
    body = (
        f"{what}{when}\n\n"
        f"دستگاه: {device.label or device.device_id}\n"
        f"شمارهٔ سیم‌کارت: {' و '.join(device.sims()) or '—'}\n\n"
        f"{how}\n\n"
        "تا وقتی این درست نشود، اسکرپر برای هر کد تأیید منتظر می‌ماند و "
        "شمارهٔ تماس آگهی‌ها گرفته نمی‌شود."
    )
    ok = await _email(to, "فرستندهٔ پیامک کار نمی‌کند", body, db=db)
    if ok:
        device.warned_at = datetime.now(timezone.utc)
        await db.commit()
        logger.info(f"[forwarder-watch] warned {to} about {device.device_id}")
    return ok


async def sweep() -> dict:
    """One pass over every device. Never raises."""
    warned = checked = 0
    try:
        async with async_session_maker() as db:
            rows = (await db.execute(
                select(ForwarderDevice).where(ForwarderDevice.is_active == True)  # noqa: E712
            )).scalars().all()
            now = datetime.now(timezone.utc)
            for d in rows:
                checked += 1
                h = fw.health(d)
                if h["state"] not in ("offline", "no_codes_yet"):
                    continue
                # Only a device that has actually delivered before. One
                # registered and never configured is an unfinished setup, not
                # an outage, and the panel already says so.
                if h["state"] == "offline" and not d.last_code_at:
                    continue
                # A device online but silent is only worth an email once it has
                # had time to be configured.
                if h["state"] == "no_codes_yet":
                    age = (now - (d.created_at.replace(tzinfo=timezone.utc)
                                  if d.created_at and not d.created_at.tzinfo
                                  else d.created_at or now)).total_seconds()
                    if age < 3600:
                        continue
                last = d.warned_at
                if last:
                    last = last if last.tzinfo else last.replace(tzinfo=timezone.utc)
                    if (now - last) < timedelta(hours=REWARN_HOURS):
                        continue
                if await warn_owner(d, h, db):
                    warned += 1
    except Exception as e:
        logger.warning(f"[forwarder-watch] sweep failed: {type(e).__name__}: {e}")
    return {"checked": checked, "warned": warned}


async def watch_loop() -> None:
    """Runs for the life of the process. FORWARDER_WATCH_MINUTES=0 disables."""
    every = float(getattr(settings, "forwarder_watch_minutes", 5) or 0)
    if every <= 0:
        logger.info("[forwarder-watch] disabled")
        return
    await asyncio.sleep(90)          # let startup finish
    from app.services.supervisor import beat
    while True:
        beat("forwarder_watch")
        res = await sweep()
        if res["warned"]:
            logger.info(f"[forwarder-watch] {res}")
        await asyncio.sleep(every * 60)
