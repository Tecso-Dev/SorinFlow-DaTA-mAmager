"""
موتور تطبیق خودکار — who was looking for the listing that just arrived.

The scraper fills the database and the matcher (`customers_for_property`)
can say, for one listing, which customers wanted it — but only when somebody
opened that listing and pressed the button. Nobody presses a button for
every new listing, so the answer went unasked.

This runs the question for every listing as it arrives: a cursor over
properties.id (the only thing that only ever grows), the same scorer the
button uses, a threshold below which a match is noise, and one row per
(listing, customer) so a consultant sees it once. New rows are announced
to the Telegram chat the backup uses, through the same proxy.

Not in the scraper's request path on purpose: a scoring bug or a slow query
here must never cost a scrape.
"""
import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Optional

from loguru import logger
from sqlalchemy import func, select

from app.config import get_settings
from app.database import async_session_maker
from app.models.crm_models import Customer, CustomerMatch
from app.models.property import Property
from app.services import secret_box
from app.services.match_service import customers_for_property

settings = get_settings()

MIN_SCORE = 55           # below this the engine would ring people about the wrong thing
PER_PROPERTY = 5         # the strongest fits only; a listing that fits twenty is a filter bug
BATCH = 300              # listings per tick — a big scrape drains over a few ticks
TICK_SECONDS = 300       # a listing waits at most five minutes for its matches
KEY_CURSOR = "match_engine_cursor"


async def _cursor(db) -> int:
    try:
        raw = (await secret_box.get_many(db, (KEY_CURSOR,))).get(KEY_CURSOR)
        return int(raw) if raw else 0
    except Exception:
        return 0


async def run_once(db, *, notify: bool = True, limit: int = BATCH) -> Dict:
    """One pass over the listings that arrived since the last one."""
    since = await _cursor(db)
    props = (await db.execute(
        select(Property).where(Property.id > since, Property.is_active == True)   # noqa: E712
        .order_by(Property.id.asc()).limit(limit))).scalars().all()
    if not props:
        return {"scanned": 0, "matched": 0, "cursor": since}

    have = (await db.execute(select(func.count(Customer.id)))).scalar_one()
    created: List[CustomerMatch] = []
    if have:
        for p in props:
            try:
                fits = await customers_for_property(db, p, limit=PER_PROPERTY, use_llm=False)
            except Exception as e:
                logger.warning(f"[match] scoring listing {p.id} failed: {type(e).__name__}: {e}")
                continue
            for c in fits:
                if c["score"] < MIN_SCORE:
                    continue
                exists = (await db.execute(select(CustomerMatch.id).where(
                    CustomerMatch.property_id == p.id, CustomerMatch.customer_id == c["id"]))).scalar_one_or_none()
                if exists:
                    continue
                row = CustomerMatch(property_id=p.id, customer_id=c["id"], score=int(round(c["score"])),
                                    reasons=c.get("reasons") or [], consultant=c.get("consultant_name") or None)
                db.add(row)
                created.append(row)
    # the cursor moves whether or not anything matched: a listing is judged once
    await secret_box.put(db, KEY_CURSOR, str(props[-1].id), "match_engine")
    await db.commit()

    if created and notify:
        await _announce(db, created)
    logger.info(f"[match] scanned {len(props)} listings, {len(created)} new matches, cursor {props[-1].id}")
    return {"scanned": len(props), "matched": len(created), "cursor": props[-1].id}


def _fa_price(n: Optional[int]) -> str:
    if not n:
        return "توافقی"
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}".rstrip("0").rstrip(".") + " میلیارد"
    return f"{round(n / 1_000_000)} میلیون"


async def _announce(db, rows: List[CustomerMatch]) -> None:
    """One Telegram message per pass, to the chat the backup goes to. Losing
    the message must not lose the matches, so every failure is a log line."""
    try:
        from app.services.backup_service import chat_ids, resolve_route, resolve_telegram, tg_request
        cfg = await resolve_telegram(db)
        chats = chat_ids(cfg["chat_id"])
        if not (cfg["token"] and chats):
            return
        ids_p = {r.property_id for r in rows}
        ids_c = {r.customer_id for r in rows}
        props = {p.id: p for p in (await db.execute(select(Property).where(Property.id.in_(ids_p)))).scalars().all()}
        custs = {c.id: c for c in (await db.execute(select(Customer).where(Customer.id.in_(ids_c)))).scalars().all()}
        lines = [f"🎯 {len(rows)} تطبیق تازه — مشتری‌هایی که دنبال آگهی‌های تازه بودند"]
        for r in sorted(rows, key=lambda x: -x.score)[:15]:
            p, c = props.get(r.property_id), custs.get(r.customer_id)
            if not p or not c:
                continue
            who = c.full_name + (f" ({c.consultant_name})" if c.consultant_name else "")
            where = " · ".join(x for x in (p.district or p.city_name, f"{p.area} متر" if p.area else "") if x)
            from app.services.match_service import _price_of
            lines.append(f"• {who} ← {p.title[:40]} — {where} — {_fa_price(_price_of(p))} — {r.score}٪")
        if len(rows) > 15:
            lines.append(f"… و {len(rows) - 15} مورد دیگر")
        domain = (getattr(settings, "domain", "") or "").strip() or "sorinflow.com"
        lines.append(f"https://{domain}/dashboard/#crm")
        route = await resolve_route(db)
        sent_any = False
        for chat in chats:
            resp, _ = await tg_request(cfg["token"], "sendMessage", route, timeout=15,
                                       json={"chat_id": chat, "text": "\n".join(lines),
                                             "disable_web_page_preview": True})
            if resp.status_code == 200:
                sent_any = True
            else:
                logger.warning(f"[match] telegram refused the announcement for {chat}: {resp.status_code} {resp.text[:120]}")
        if sent_any:
            now = datetime.now(timezone.utc)
            for r in rows:
                r.notified_at = now
            await db.commit()
    except Exception as e:
        logger.warning(f"[match] announcement not sent: {type(e).__name__}: {e}")


async def tick() -> Dict:
    async with async_session_maker() as db:
        try:
            return await run_once(db)
        except Exception as e:
            logger.warning(f"[match] tick failed: {type(e).__name__}: {e}")
            try:
                await db.rollback()
            except Exception:
                pass
            return {"error": type(e).__name__}


async def engine_loop() -> None:
    """Runs for the life of the process. MATCH_ENGINE=0 disables."""
    if not getattr(settings, "match_engine", True):
        logger.info("[match] disabled")
        return
    await asyncio.sleep(90)          # let startup finish
    logger.info(f"[match] engine armed — every {TICK_SECONDS // 60} min, threshold {MIN_SCORE}٪")
    while True:
        await tick()
        await asyncio.sleep(TICK_SECONDS)
