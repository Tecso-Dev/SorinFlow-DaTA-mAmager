"""
هشدار کاهش قیمت — the listing got cheaper; somebody should hear about it.

The scraper has kept a price trail on every listing since August and
nothing ever read it back: a cut was a log line and a JSON entry. This
walks the listings whose price moved since the last pass, keeps the ones
that came DOWN by a real margin, files one alert per move, and re-runs the
customer matcher on them — a flat that was over somebody's budget last
week may fit it today, which is the whole point of watching.

Rentals are judged on the same figure the matcher uses, deposit + 30 × rent,
so a landlord swapping deposit for rent does not read as a cut. Same shape
as the matching engine: a cursor, a threshold, one row per event, off the
scraper's request path, a Telegram line per pass.
"""
import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from loguru import logger
from sqlalchemy import select

from app.auth.visibility import stamp_owner
from app.config import get_settings
from app.database import async_session_maker
from app.models.crm_models import CustomerMatch, PriceAlert
from app.models.property import Property
from app.services import secret_box
from app.services.match_service import RENT_TO_DEPOSIT, customers_for_property

settings = get_settings()

MIN_DROP_PCT = 3          # under this it is rounding, not a cut
BATCH = 300
TICK_SECONDS = 300
KEY_CURSOR = "price_watch_cursor"
MATCH_MIN_SCORE = 55      # the matching engine's bar, kept in step
MATCH_PER_LISTING = 5


def latest_move(prop) -> Optional[Tuple[int, int, str]]:
    """(before, after, kind) of the newest recorded move on the comparable
    figure, or None when the trail is empty or the move is not readable."""
    trail = getattr(prop, "price_history", None) or []
    entry = trail[-1] if trail and isinstance(trail[-1], dict) else None
    if not entry:
        return None
    frm = entry.get("from") or {}
    if prop.listing_type == "rent":
        dep_to = entry.get("deposit", prop.deposit) or 0
        rent_to = entry.get("rent_price", prop.rent_price) or 0
        dep_from = frm.get("deposit", dep_to) or 0
        rent_from = frm.get("rent_price", rent_to) or 0
        before, after = dep_from + rent_from * RENT_TO_DEPOSIT, dep_to + rent_to * RENT_TO_DEPOSIT
    else:
        after = entry.get("total_price") or entry.get("price")
        before = frm.get("total_price") or frm.get("price")
    if not before or after is None:
        return None
    return int(before), int(after), (prop.listing_type or "buy")


def drop_pct(before: int, after: int) -> int:
    return int(round((after - before) / before * 100)) if before else 0


async def _cursor(db) -> Optional[datetime]:
    try:
        raw = (await secret_box.get_many(db, (KEY_CURSOR,))).get(KEY_CURSOR)
        if not raw:
            return None
        dt = datetime.fromisoformat(raw)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


async def run_once(db, *, notify: bool = True, limit: int = BATCH) -> Dict:
    """One pass over the listings whose price moved since the last one."""
    since = await _cursor(db)
    q = select(Property).where(Property.is_active == True, Property.price_changed_at.isnot(None))   # noqa: E712
    if since is not None:
        q = q.where(Property.price_changed_at > since)
    props = (await db.execute(q.order_by(Property.price_changed_at.asc()).limit(limit))).scalars().all()
    if not props:
        return {"scanned": 0, "drops": 0, "matches": 0}

    created: List[PriceAlert] = []
    matches = 0
    for p in props:
        move = latest_move(p)
        if not move:
            continue
        before, after, kind = move
        pct = drop_pct(before, after)
        if pct > -MIN_DROP_PCT:
            continue
        moved_at = p.price_changed_at if p.price_changed_at.tzinfo else p.price_changed_at.replace(tzinfo=timezone.utc)
        dup = (await db.execute(select(PriceAlert.id).where(
            PriceAlert.property_id == p.id, PriceAlert.moved_at == moved_at))).scalar_one_or_none()
        if dup:
            continue
        alert = PriceAlert(property_id=p.id, listing_type=kind, from_amount=before, to_amount=after,
                           delta_pct=pct, moved_at=moved_at)
        db.add(alert)
        # cheaper now — maybe inside somebody's budget now
        try:
            fits = await customers_for_property(db, p, limit=MATCH_PER_LISTING, use_llm=False)
        except Exception as e:
            logger.warning(f"[price] matching {p.id} after the cut failed: {type(e).__name__}: {e}")
            fits = []
        for c in fits:
            if c["score"] < MATCH_MIN_SCORE:
                continue
            exists = (await db.execute(select(CustomerMatch.id).where(
                CustomerMatch.property_id == p.id, CustomerMatch.customer_id == c["id"]))).scalar_one_or_none()
            if exists:
                continue
            row = CustomerMatch(property_id=p.id, customer_id=c["id"], score=int(round(c["score"])),
                                reasons=["قیمت کم شد", *(c.get("reasons") or [])])
            stamp_owner(row, c.get("consultant_name") or None, c.get("consultant_user_id"))
            db.add(row)
            alert.matches_created = (alert.matches_created or 0) + 1
            matches += 1
        created.append(alert)

    last = props[-1].price_changed_at
    last = last if last.tzinfo else last.replace(tzinfo=timezone.utc)
    await secret_box.put(db, KEY_CURSOR, last.isoformat(), "price_watch")
    await db.commit()

    if created and notify:
        await _announce(db, created)
    logger.info(f"[price] scanned {len(props)} moves, {len(created)} cuts, {matches} new matches")
    return {"scanned": len(props), "drops": len(created), "matches": matches}


def _fa(n: int) -> str:
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.2f}".rstrip("0").rstrip(".") + " میلیارد"
    return f"{round(n / 1_000_000)} میلیون"


async def _announce(db, alerts: List[PriceAlert]) -> None:
    try:
        from app.services.backup_service import chat_ids, resolve_route, resolve_telegram, tg_request
        cfg = await resolve_telegram(db)
        chats = chat_ids(cfg["chat_id"])
        if not (cfg["token"] and chats):
            return
        props = {p.id: p for p in (await db.execute(select(Property).where(
            Property.id.in_({a.property_id for a in alerts})))).scalars().all()}
        lines = [f"📉 {len(alerts)} آگهی ارزان شدند"]
        for a in sorted(alerts, key=lambda x: x.delta_pct)[:15]:
            p = props.get(a.property_id)
            if not p:
                continue
            where = " · ".join(x for x in (p.district or p.city_name, f"{p.area} متر" if p.area else "") if x)
            extra = f" — {a.matches_created} مشتری هم‌خوان" if a.matches_created else ""
            lines.append(f"• {p.title[:40]} — {where} — {_fa(a.from_amount)} ← {_fa(a.to_amount)} ({a.delta_pct}٪){extra}")
        if len(alerts) > 15:
            lines.append(f"… و {len(alerts) - 15} مورد دیگر")
        domain = (getattr(settings, "domain", "") or "").strip() or "sorinflow.com"
        lines.append(f"https://{domain}/dashboard/#crm")
        sent_any = False
        route = await resolve_route(db)
        for chat in chats:
            resp, _ = await tg_request(cfg["token"], "sendMessage", route, timeout=15,
                                       json={"chat_id": chat, "text": "\n".join(lines),
                                             "disable_web_page_preview": True})
            if resp.status_code == 200:
                sent_any = True
            else:
                logger.warning(f"[price] telegram refused the announcement for {chat}: {resp.status_code} {resp.text[:120]}")
        if sent_any:
            now = datetime.now(timezone.utc)
            for a in alerts:
                a.notified_at = now
            await db.commit()
    except Exception as e:
        logger.warning(f"[price] announcement not sent: {type(e).__name__}: {e}")


async def tick() -> Dict:
    async with async_session_maker() as db:
        try:
            return await run_once(db)
        except Exception as e:
            logger.warning(f"[price] tick failed: {type(e).__name__}: {e}")
            try:
                await db.rollback()
            except Exception:
                pass
            return {"error": type(e).__name__}


async def watch_loop() -> None:
    """Runs for the life of the process. MATCH_ENGINE=0 switches this off too."""
    if not getattr(settings, "match_engine", True):
        logger.info("[price] disabled")
        return
    await asyncio.sleep(120)
    logger.info(f"[price] watch armed — every {TICK_SECONDS // 60} min, cuts of {MIN_DROP_PCT}٪ and more")
    from app.services.supervisor import beat
    while True:
        beat("price_watch")
        await tick()
        await asyncio.sleep(TICK_SECONDS)
