"""
خلاصهٔ صبحگاهی — one Telegram message at eight, before the office starts calling.

Everything below already exists somewhere in the panel: the new listings on
the scraper page, the matches and the price cuts on the call queue, the
backup's outcome on the users page. The owner does not open four pages at
eight in the morning; they open Telegram. So the same chat the backup and
the matches go to gets one message a day:

    what came in overnight, what fits whom, what got cheaper, how many calls
    are waiting, and whether last night's backup arrived.

Sent by the loop when the Tehran clock passes DIGEST_HOUR and today's has
not gone out yet — so a pod that was down at eight sends it when it comes
back, late but not never. «ارسال الان» on the backup card sends an extra one
without touching that record.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from loguru import logger
from sqlalchemy import func, or_, select

from app.config import get_settings
from app.database import async_session_maker
from app.models.crm_models import CustomerMatch, PriceAlert
from app.models.lead import Lead
from app.models.property import Property
from app.models.scraping_job import ScrapingJob
from app.services import secret_box

settings = get_settings()

# Fixed +03:30 — the production image has no tz database (see call_queue.TEHRAN).
TEHRAN = timezone(timedelta(hours=3, minutes=30), "Asia/Tehran")
HOUR = int(getattr(settings, "digest_hour", 8))     # DIGEST_HOUR; below 0 disables
WINDOW = timedelta(hours=24)
TOP_DROPS = 3
KEY_LAST = "digest_last_sent"                        # the Tehran date it went out
_WEEKDAYS = ("دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه", "یکشنبه")


async def last_sent(db) -> Optional[str]:
    try:
        return (await secret_box.get_many(db, (KEY_LAST,))).get(KEY_LAST) or None
    except Exception:
        return None


async def build(db, *, now: Optional[datetime] = None) -> Dict:
    """The message and the numbers behind it, for the last 24 hours."""
    from app.services.backup_service import last_offsite
    from app.services.dpa_service import to_jalali

    now = now or datetime.now(timezone.utc)
    since = now - WINDOW
    local = now.astimezone(TEHRAN)
    day_end = local.replace(hour=23, minute=59, second=59, microsecond=999999)

    async def count(stmt) -> int:
        return (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()

    props = select(Property.id).where(Property.created_at >= since)
    n_props = await count(props)
    n_rent = await count(props.where(Property.listing_type == "rent"))

    jobs = select(ScrapingJob.id).where(ScrapingJob.created_at >= since)
    n_done = await count(jobs.where(ScrapingJob.status == "completed"))
    n_failed = await count(jobs.where(ScrapingJob.status == "failed"))
    n_running = await count(select(ScrapingJob.id).where(ScrapingJob.status == "running"))

    n_matches = await count(select(CustomerMatch.id).where(CustomerMatch.created_at >= since))
    n_waiting = await count(select(CustomerMatch.id).where(CustomerMatch.status == "new"))

    drops = (await db.execute(
        select(PriceAlert, Property.title).join(Property, Property.id == PriceAlert.property_id)
        .where(PriceAlert.created_at >= since)
        .order_by(PriceAlert.delta_pct.asc()).limit(TOP_DROPS))).all()
    n_drops = await count(select(PriceAlert.id).where(PriceAlert.created_at >= since))

    # the call queue as the panel counts it (crm._queue_query), for everybody
    due = select(Lead.id).where(
        Lead.status.in_(("new", "contacted")), Lead.phone_number.isnot(None),
        or_(Lead.next_call_at.is_(None), Lead.next_call_at <= now))
    n_due = await count(due)
    n_callbacks = await count(select(Lead.id).where(
        Lead.status.in_(("new", "contacted")), Lead.phone_number.isnot(None),
        Lead.next_call_at > now, Lead.next_call_at <= day_end))

    lo = await last_offsite(db)

    lines = [f"☀️ خلاصهٔ صبح — {_WEEKDAYS[local.weekday()]} {to_jalali(local)}",
             f"از ساعت {since.astimezone(TEHRAN).strftime('%H:%M')} دیروز تا حالا:", ""]
    sale = n_props - n_rent
    lines.append(f"🏠 آگهی تازه: {n_props}" + (f" (فروش {sale} · اجاره {n_rent})" if n_props else ""))
    scrape = f"🕷 اسکرپ: {n_done} کامل · {n_failed} ناموفق"
    if n_running:
        scrape += f" · {n_running} در حال اجرا"
    lines.append(scrape)
    lines.append(f"🎯 تطبیق تازه: {n_matches} · در انتظار تماس: {n_waiting}")
    lines.append(f"📉 کاهش قیمت: {n_drops}")
    for alert, title in drops:
        # «12٪ ارزان‌تر», not «-12٪»: a leading minus flips around in RTL text
        lines.append(f"   • {(title or 'آگهی')[:40]} — {abs(alert.delta_pct)}٪ ارزان‌تر")
    lines.append(f"📞 صف تماس امروز: {n_due}" + (f" · تماس مجدد امروز: {n_callbacks}" if n_callbacks else ""))
    lines.append("💾 بکاپ دیشب: " + _backup_line(lo, since))
    domain = (getattr(settings, "domain", "") or "").strip() or "sorinflow.com"
    lines += ["", f"https://{domain}/dashboard/#crm"]
    return {"text": "\n".join(lines), "since": since.isoformat(),
            "counts": {"properties": n_props, "rent": n_rent, "jobs_done": n_done, "jobs_failed": n_failed,
                       "jobs_running": n_running, "matches": n_matches, "waiting": n_waiting,
                       "drops": n_drops, "calls_due": n_due, "callbacks": n_callbacks}}


def _backup_line(lo: Dict, since: datetime) -> str:
    """One phrase about last night's shipment. The outcome's `at` is naive
    server-local time (backup_service writes datetime.now()), so the window
    check is against a naive `since` in the same clock."""
    at = lo.get("at")
    if not at:
        return "هنوز فرستاده نشده"
    try:
        when = datetime.fromisoformat(at)
    except ValueError:
        return "نامشخص"
    if when < since.astimezone().replace(tzinfo=None):
        return f"در ۲۴ ساعت گذشته فرستاده نشد (آخری: {at[:16].replace('T', ' ')})"
    if lo.get("ok"):
        size = lo.get("size_kb") or 0
        chats = len(lo.get("delivered") or [])
        return f"رسید ({size // 1024 if size >= 1024 else size} {'MB' if size >= 1024 else 'KB'}" \
               + (f" · {chats} چت" if chats else "") + ")" \
               + (f" — بخشی نرسید: {lo['error']}" if lo.get("error") else "")
    return f"نرسید: {lo.get('error') or 'خطای نامشخص'}"


async def send(db, *, now: Optional[datetime] = None, record: bool = True) -> Dict:
    """Build and deliver to every configured chat. `record` writes today's date
    so the loop does not send again; the panel's «ارسال الان» leaves it alone."""
    from app.services.backup_service import chat_ids, resolve_route, resolve_telegram, tg_request
    now = now or datetime.now(timezone.utc)
    cfg = await resolve_telegram(db)
    chats = chat_ids(cfg["chat_id"])
    if not (cfg["token"] and chats):
        return {"ok": False, "error": "تلگرام تنظیم نشده است", "delivered": []}
    built = await build(db, now=now)
    route = await resolve_route(db)
    delivered, errors = [], []
    for chat in chats:
        try:
            resp, _ = await tg_request(cfg["token"], "sendMessage", route, timeout=20,
                                       json={"chat_id": chat, "text": built["text"],
                                             "disable_web_page_preview": True})
            if resp.status_code == 200:
                delivered.append(chat)
            else:
                errors.append(f"{chat}: {resp.status_code} {resp.text[:100]}")
        except Exception as e:
            errors.append(f"{chat}: {type(e).__name__}: {e}")
    ok = bool(delivered)
    if ok and record:
        try:
            await secret_box.put(db, KEY_LAST, now.astimezone(TEHRAN).strftime("%Y-%m-%d"), "digest")
        except Exception as e:
            logger.warning(f"[digest] could not record the send: {e}")
    if errors:
        logger.warning(f"[digest] not delivered to {len(errors)} chat(s): {'; '.join(errors)}")
    return {"ok": ok, "delivered": delivered, "error": "; ".join(errors), "text": built["text"],
            "counts": built["counts"]}


async def tick(now: Optional[datetime] = None) -> Dict:
    """Send today's if the hour has passed and it has not gone out."""
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(TEHRAN)
    if local.hour < HOUR:
        return {"skipped": "early"}
    async with async_session_maker() as db:
        try:
            if await last_sent(db) == local.strftime("%Y-%m-%d"):
                return {"skipped": "sent"}
            from app.services.backup_service import chat_ids, resolve_telegram
            cfg = await resolve_telegram(db)
            if not (cfg["token"] and chat_ids(cfg["chat_id"])):
                return {"skipped": "unconfigured"}
            res = await send(db, now=now)
            if res["ok"]:
                logger.info(f"[digest] sent to {len(res['delivered'])} chat(s): {res['counts']}")
            return res
        except Exception as e:
            logger.warning(f"[digest] tick failed: {type(e).__name__}: {e}")
            try:
                await db.rollback()
            except Exception:
                pass
            return {"error": type(e).__name__}


async def digest_loop() -> None:
    """Runs for the life of the process. MATCH_ENGINE=0 or DIGEST_HOUR=-1 disables."""
    if not getattr(settings, "match_engine", True) or HOUR < 0:
        logger.info("[digest] disabled")
        return
    await asyncio.sleep(120)         # let startup finish
    logger.info(f"[digest] armed — every day after {HOUR:02d}:00 Tehran")
    while True:
        await tick()
        # ponytail: a failed delivery is retried every minute until it lands;
        # back off here if Telegram outages ever make the log noisy
        await asyncio.sleep(60)
