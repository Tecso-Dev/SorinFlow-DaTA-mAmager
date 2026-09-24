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
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from loguru import logger
from sqlalchemy import and_, func, or_, select

from app.ai import listing_reader
from app.auth.visibility import stamp_owner
from app.config import get_settings
from app.database import async_session_maker
from app.models.crm_models import Customer, CustomerMatch
from app.models.property import Property
from app.services import secret_box
from app.services.match_service import customers_for_property, preload_customers

settings = get_settings()

MIN_SCORE = 55           # below this the engine would ring people about the wrong thing
PER_PROPERTY = 5         # the strongest fits only; a listing that fits twenty is a filter bug
BATCH = 300              # listings per tick — a big scrape drains over a few ticks
TICK_SECONDS = 300       # a listing waits at most five minutes for its matches
KEY_CURSOR = "match_engine_cursor"
# A stuck reader (a bug, a listing outside every retry path) must not hold a
# listing back from matching forever — 30 minutes is long enough for the
# reader's own tick (every 120s) to have had several tries at it.
READ_WAIT_TIMEOUT = timedelta(minutes=30)


async def _cursor(db) -> int:
    try:
        raw = (await secret_box.get_many(db, (KEY_CURSOR,))).get(KEY_CURSOR)
        return int(raw) if raw else 0
    except Exception:
        return 0


async def _due(db):
    """Listings ready to be judged, and due for it.

    Ready: read at the current prompt version, OR the reader will never get
    here — off, unconfigured, over budget (listing_reader.reader_will_run),
    or this specific listing is past listing_reader.MAX_ATTEMPTS on its
    current content — OR the 30-minute safety window has passed regardless.
    Due: never judged, or the content/facts moved since (ai_match_fp behind
    ai_content_fp — see app/models/property.py:content_fingerprint; a fresher
    read counts too, since the reader's facts are part of what gets judged).
    """
    clauses = [Property.is_active == True]                                      # noqa: E712
    if await listing_reader.reader_will_run(db):
        read_enough = and_(Property.ai_read_at.isnot(None),
                           Property.ai_facts["prompt_version"].as_integer() == listing_reader.PROMPT_VERSION)
        gave_up_on_it = and_(Property.ai_read_attempts >= listing_reader.MAX_ATTEMPTS,
                             Property.ai_read_fp.isnot(None),
                             ~Property.ai_read_fp.is_distinct_from(Property.ai_content_fp))
        cutoff = datetime.now(timezone.utc) - READ_WAIT_TIMEOUT
        clauses.append(or_(read_enough, gave_up_on_it, Property.created_at < cutoff))
    # else: nobody is coming to read anything right now, so waiting for one
    # is pointless — every active listing is "ready" on that count, and only
    # the due-for-judging clause below still gates it
    changed = Property.ai_match_fp.is_distinct_from(Property.ai_content_fp)
    clauses.append(or_(Property.ai_matched_at.is_(None), changed))
    return and_(*clauses)


async def run_once(db, *, notify: bool = True, limit: int = BATCH) -> Dict:
    """One pass over the listings due to be judged (see _due) — not only
    the ones that arrived since the last pass: a listing the reader just
    finished, or whose content changed, is exactly as due as a new one, so
    there is no id lower bound any more (same reasoning as
    listing_reader.run_once). The stored cursor is reporting only."""
    from app.crm import portal_bridge
    await portal_bridge.sync_open(db)       # portal requests the engine has not met yet
    since = await _cursor(db)
    props = (await db.execute(
        select(Property).where(await _due(db))
        .order_by(Property.id.asc()).limit(limit))).scalars().all()
    if not props:
        return {"scanned": 0, "matched": 0, "cursor": since}

    have = (await db.execute(select(func.count(Customer.id)))).scalar_one()
    customers = await preload_customers(db) if have else []      # once for the whole pass, not once per listing
    created: List[CustomerMatch] = []
    now = datetime.now(timezone.utc)
    for p in props:
        # scoring against preloaded customers does no I/O, so without this a
        # pass (300 listings × every customer) holds the event loop — and every
        # request on this single worker — for seconds at a time
        await asyncio.sleep(0)
        if customers:
            try:
                fits = await customers_for_property(db, p, limit=PER_PROPERTY, use_llm=False, customers=customers)
            except Exception as e:
                logger.warning(f"[match] scoring listing {p.id} failed: {type(e).__name__}: {e}")
                fits = []
            for c in fits:
                if c["score"] < MIN_SCORE:
                    continue
                exists = (await db.execute(select(CustomerMatch.id).where(
                    CustomerMatch.property_id == p.id, CustomerMatch.customer_id == c["id"]))).scalar_one_or_none()
                if exists:
                    continue
                row = CustomerMatch(property_id=p.id, customer_id=c["id"], score=int(round(c["score"])),
                                    reasons=c.get("reasons") or [])
                # the card is for the customer's consultant — the account, not the name
                stamp_owner(row, c.get("consultant_name") or None, c.get("consultant_user_id"))
                db.add(row)
                created.append(row)
        # judged either way — no customers, or scoring failed, is still a
        # pass over this listing, and the old cursor advanced past it just
        # the same; ai_match_fp is what keeps it from looking due again
        p.ai_matched_at = now
        p.ai_match_fp = p.ai_content_fp
    # a portal request behind a matched customer is «مورد پیدا شد» from now on
    await portal_bridge.note_matches(db, [(r.customer_id, r.property_id) for r in created])
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
    from app.services.supervisor import beat
    while True:
        beat("match_engine")
        await tick()
        await asyncio.sleep(TICK_SECONDS)
