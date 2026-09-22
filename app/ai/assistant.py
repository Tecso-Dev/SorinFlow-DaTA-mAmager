"""
دستیار دفتر «سورین» — the office asks its own database, in Telegram.

«امروز چند آگهی زیر ۵ میلیارد در گلها اومد؟» «مشتری‌های داغ بدون تماس این هفته؟»
«صف تماس چطوره؟» — questions the panel answers with four clicks, asked in
the chat the office already lives in. The bot is the backup's bot, the way
out is the backup's route, and the people allowed to ask are the chats the
backup and the digest already go to: nothing new to configure.

Only the assistant is a real agent — the model chooses which tool to call
and reads what comes back — and its tools are the whole of its power:
every one is a read, none takes a phone number out of the office, and a
question none of them can answer gets «نمی‌دانم», never an invented figure.
Every question and answer lands in ai_chats, so the panel can show who
asked what.

Polling, not a webhook: the server is in Iran behind a proxy, and
getUpdates through the route needs no inbound address. The backup card's
«پیدا کن» used getUpdates to list chats; consuming updates here would leave
it nothing to find, so the chats seen here are recorded for it.
"""
import asyncio
import json
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import func, or_, select

from app.config import get_settings
from app.database import async_session_maker
from app.services import llm, secret_box

settings = get_settings()

NAME = "سورین"
KEY_OFFSET = "assistant_update_offset"     # Telegram's update_id we have consumed up to
KEY_ENABLED = "assistant_enabled"          # the card's switch; default on when the model is configured
KEY_SEEN = "assistant_seen_chats"          # chats that wrote to the bot, for the backup card's «پیدا کن»
POLL_TIMEOUT = 25                          # long-poll seconds; the route's client waits a little longer
MAX_ROUNDS = 4                             # tool calls before the model must answer
MAX_REPLY = 3500                           # Telegram's limit is 4096
MAX_QUESTION = 1000
SCAN_LIMIT = 2000                          # rows a counting tool reads before it says «تقریباً»
TEHRAN = timezone(timedelta(hours=3, minutes=30), "Asia/Tehran")
_WEEKDAYS = ("دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه", "یکشنبه")

PERSONA = (
    f"تو «{NAME}» هستی، دستیار دفتر املاک سورین در ارومیه. مشاورهای دفتر در تلگرام از تو "
    "دربارهٔ آگهی‌ها، مشتری‌ها و صف تماس می‌پرسند.\n"
    "قواعد:\n"
    "۱. فقط از نتیجهٔ ابزارها جواب بده. عددی که ابزار نداده، نگو. اگر ابزاری برای سؤال نیست یا "
    "نتیجه خالی است، بگو «نمی‌دانم» یا «چیزی پیدا نکردم» و بگو در پنل کجا می‌شود دید.\n"
    "۲. کوتاه و فارسی بنویس؛ اعداد را با ارقام فارسی و جداکنندهٔ هزارگان بنویس؛ قیمت‌ها را به «میلیون» و "
    "«میلیارد» تومان گرد کن. بدون واژهٔ انگلیسی، بدون اغراق.\n"
    "۳. شمارهٔ تلفن مالک یا مشتری را هرگز ننویس — بگو «شماره در پنل».\n"
    "۴. برای فهرست‌ها حداکثر ۵ مورد بنویس، هر کدام یک خط با کد ملک.\n"
    "۵. اگر سؤال مبهم است، یک سؤال کوتاه بپرس به‌جای حدس‌زدن."
)

HELP = (
    f"سلام، {NAME} هستم — دستیار دفتر.\n"
    "می‌توانید بپرسید:\n"
    "• «امروز چند آگهی تازه اومد؟» / «آگهی‌های زیر ۵ میلیارد در گلها»\n"
    "• «صف تماس چطوره؟» / «چند تطبیق منتظر تماسه؟»\n"
    "• «مشتری‌های داغ مینا» / «ملک ۱۰۰۷ چیه؟»\n"
    "• «خلاصهٔ امروز»\n"
    "فقط از دیتابیس دفتر جواب می‌دهم و شماره‌ای بیرون نمی‌دهم."
)


# ── who may ask ──────────────────────────────────────────────────────────────

async def allowed_chats(db) -> List[str]:
    from app.services.backup_service import chat_ids, resolve_telegram
    cfg = await resolve_telegram(db)
    return chat_ids(cfg["chat_id"]) if cfg["token"] else []


async def enabled(db) -> bool:
    """Its own switch on the AI screen, and the global one under it. The old
    key is kept so a switch thrown before the screen existed still holds."""
    try:
        rows = await secret_box.get_many(db, (KEY_ENABLED,))
    except Exception:
        return True
    if (rows.get(KEY_ENABLED) or "true").lower() == "false":
        return False
    return await llm.agent_enabled(db, "assistant")


async def seen_chats(db) -> List[Dict[str, str]]:
    """Chats that wrote to the bot while the assistant was polling."""
    try:
        raw = (await secret_box.get_many(db, (KEY_SEEN,))).get(KEY_SEEN)
        return json.loads(raw) if raw else []
    except Exception:
        return []


async def _remember_chat(db, chat: Dict[str, Any]) -> None:
    cid = chat.get("id")
    if cid is None:
        return
    name = chat.get("title") or " ".join(
        x for x in (chat.get("first_name"), chat.get("last_name")) if x) or chat.get("username") or ""
    seen = [c for c in await seen_chats(db) if c.get("id") != str(cid)]
    seen.insert(0, {"id": str(cid), "name": name, "type": chat.get("type", "")})
    await secret_box.put(db, KEY_SEEN, json.dumps(seen[:20], ensure_ascii=False), "assistant")


# ── the tools ────────────────────────────────────────────────────────────────

def _fa_num(n) -> str:
    return f"{int(n):,}".replace(",", "٬").translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


def _fa_price(n: Optional[int]) -> str:
    if not n:
        return "توافقی"
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}".rstrip("0").rstrip(".").translate(str.maketrans("0123456789.", "۰۱۲۳۴۵۶۷۸۹٫")) + " میلیارد"
    return _fa_num(round(n / 1_000_000)) + " میلیون"


def _money(p) -> str:
    if p.listing_type == "rent":
        return f"ودیعه {_fa_price(p.deposit)} · اجاره {_fa_price(p.rent_price) if p.rent_price else 'ندارد'}"
    return _fa_price(p.total_price or p.price)


def _listing_line(p) -> Dict[str, Any]:
    from app.ai.listing_reader import effective
    e = effective(p)
    facts = getattr(p, "ai_facts", None) or {}
    return {
        "serial_no": p.serial_no, "title": (p.title or "")[:60], "kind": e.get("kind"),
        "district": p.district or p.city_name, "area": p.area, "rooms": p.rooms,
        "listing_type": p.listing_type, "price": _money(p),
        "flags": [k for k in ("convertible", "exchange", "vacant") if e.get(k)],
        "summary": (facts.get("summary") or "")[:100] or None,
    }


async def tool_count_listings(db, listing_type: Optional[str] = None, kind: Optional[str] = None,
                              district: Optional[str] = None, max_price: Optional[int] = None,
                              min_area: Optional[int] = None, max_area: Optional[int] = None,
                              rooms: Optional[int] = None, days: Optional[int] = None) -> Dict[str, Any]:
    from app.models.property import Property
    from app.ai.listing_reader import effective
    from app.services.match_service import _comparable_sql
    q = select(Property).where(Property.is_active == True)   # noqa: E712
    if listing_type in ("buy", "rent"):
        q = q.where(Property.listing_type == listing_type)
    if district:
        like = f"%{district.strip()}%"
        q = q.where(or_(Property.district.ilike(like), Property.neighborhood.ilike(like), Property.address.ilike(like)))
    if max_price:
        q = q.where(_comparable_sql(listing_type or "buy") <= int(max_price))
    if min_area:
        q = q.where(Property.area >= int(min_area))
    if max_area:
        q = q.where(Property.area <= int(max_area))
    if rooms is not None:
        q = q.where(Property.rooms == int(rooms))
    if days:
        q = q.where(Property.created_at >= datetime.now(timezone.utc) - timedelta(days=int(days)))
    rows = (await db.execute(q.order_by(Property.id.desc()).limit(SCAN_LIMIT))).scalars().all()
    if kind:
        rows = [p for p in rows if effective(p).get("kind") == kind]
    return {"count": len(rows), "approximate": len(rows) >= SCAN_LIMIT,
            "examples": [_listing_line(p) for p in rows[:5]]}


async def tool_search_listings(db, query: str, listing_type: Optional[str] = None, limit: int = 6) -> Dict[str, Any]:
    from app.models.property import Property
    limit = max(1, min(int(limit or 6), 10))
    ids: List[int] = []
    try:
        from app.ai import embeddings as _emb
        ids = [pid for pid, _ in await _emb.semantic_candidates(db, query, listing_type=listing_type, limit=limit)]
    except Exception as e:
        logger.info(f"[assistant] semantic search unavailable: {type(e).__name__}: {e}")
    if ids:
        rows = (await db.execute(select(Property).where(Property.id.in_(ids), Property.is_active == True))).scalars().all()   # noqa: E712
        by_id = {p.id: p for p in rows}
        rows = [by_id[i] for i in ids if i in by_id]
    else:
        like = f"%{query.strip()[:60]}%"
        q = select(Property).where(Property.is_active == True,   # noqa: E712
                                   or_(Property.title.ilike(like), Property.description.ilike(like)))
        if listing_type in ("buy", "rent"):
            q = q.where(Property.listing_type == listing_type)
        rows = (await db.execute(q.order_by(Property.id.desc()).limit(limit))).scalars().all()
    return {"results": [_listing_line(p) for p in rows], "how": "semantic" if ids else "text"}


async def tool_queue_status(db) -> Dict[str, Any]:
    from app.models.lead import Lead
    from app.models.crm_models import CustomerMatch, PriceAlert
    from app.models.property import Property
    now = datetime.now(timezone.utc)
    day = now.astimezone(TEHRAN).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)

    async def count(stmt):
        return int((await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one())
    due = await count(select(Lead.id).where(
        Lead.status.in_(("new", "contacted")), Lead.phone_number.isnot(None),
        or_(Lead.next_call_at.is_(None), Lead.next_call_at <= now)))
    return {"calls_due": due,
            "matches_waiting": await count(select(CustomerMatch.id).where(CustomerMatch.status == "new")),
            "price_drops_new": await count(select(PriceAlert.id).where(PriceAlert.status == "new")),
            "listings_today": await count(select(Property.id).where(Property.created_at >= day)),
            "listings_active": await count(select(Property.id).where(Property.is_active == True))}   # noqa: E712


async def tool_customers(db, consultant: Optional[str] = None, temperature: Optional[str] = None,
                         limit: int = 8) -> Dict[str, Any]:
    from app.models.crm_models import Customer, CustomerMatch
    limit = max(1, min(int(limit or 8), 15))
    q = select(Customer)
    if consultant:
        q = q.where(Customer.consultant_name.ilike(f"%{consultant.strip()}%"))
    if temperature in ("hot", "warm", "cold"):
        q = q.where(Customer.temperature == temperature)
    rows = (await db.execute(q.order_by(Customer.updated_at.desc().nullslast()).limit(limit))).scalars().all()
    waiting = dict((await db.execute(
        select(CustomerMatch.customer_id, func.count(CustomerMatch.id))
        .where(CustomerMatch.status == "new", CustomerMatch.customer_id.in_([c.id for c in rows] or [0]))
        .group_by(CustomerMatch.customer_id))).all())
    return {"customers": [{
        "name": c.full_name, "temperature": c.temperature, "consultant": c.consultant_name,
        "wants": " · ".join(filter(None, (c.desired_district, c.desired_specs,
                                          f"تا {_fa_price(c.budget_max)}" if c.budget_max else None))),
        "deal_type": c.deal_type, "matches_waiting": int(waiting.get(c.id, 0)),
        "phone": "در پنل"} for c in rows]}


async def tool_property(db, serial_no: int) -> Dict[str, Any]:
    from app.models.property import Property
    from app.ai.photo_tagger import tags_fa
    p = (await db.execute(select(Property).where(Property.serial_no == int(serial_no)))).scalars().first()
    if not p:
        return {"found": False}
    facts = getattr(p, "ai_facts", None) or {}
    return {"found": True, **_listing_line(p),
            "year_built": p.year_built, "floor": p.floor, "document": facts.get("document") or p.document_type,
            "condition": facts.get("condition"), "suitable_for": facts.get("suitable_for") or [],
            "red_flags": facts.get("red_flags") or [], "photos": tags_fa(getattr(p, "ai_photo_tags", None)),
            "advertiser": "مشاور" if p.agency_suspected else "شخصی", "owner_phone": "در پنل"}


async def tool_today_digest(db) -> Dict[str, Any]:
    from app.crm import digest
    return {"text": (await digest.build(db))["text"]}


TOOLS: List[Dict[str, Any]] = [
    {"type": "function", "function": {
        "name": "count_listings",
        "description": "شمارش آگهی‌های فعال با فیلتر، و پنج نمونه. برای سؤال‌های «چند تا…» و فهرست‌های فیلترشده.",
        "parameters": {"type": "object", "properties": {
            "listing_type": {"type": "string", "enum": ["buy", "rent"], "description": "خرید یا اجاره"},
            "kind": {"type": "string", "enum": ["apartment", "house", "land", "shop", "office"]},
            "district": {"type": "string", "description": "نام محله یا خیابان، مثل گلها"},
            "max_price": {"type": "integer", "description": "سقف قیمت به تومان (برای اجاره: ودیعه + ۳۰ × اجاره)"},
            "min_area": {"type": "integer"}, "max_area": {"type": "integer"}, "rooms": {"type": "integer"},
            "days": {"type": "integer", "description": "فقط آگهی‌های N روز اخیر؛ «امروز» = 1"}}}}},
    {"type": "function", "function": {
        "name": "search_listings",
        "description": "جستجوی آگهی با یک جملهٔ آزاد (معنایی)، مثل «خانهٔ حیاط‌دار برای کافه».",
        "parameters": {"type": "object", "required": ["query"], "properties": {
            "query": {"type": "string"}, "listing_type": {"type": "string", "enum": ["buy", "rent"]},
            "limit": {"type": "integer"}}}}},
    {"type": "function", "function": {
        "name": "queue_status",
        "description": "وضعیت امروز: صف تماس، تطبیق‌های منتظر، کاهش قیمت‌های تازه، آگهی‌های امروز و کل آگهی‌های فعال.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "customers",
        "description": "فهرست مشتری‌های CRM با معیارشان و تعداد تطبیق منتظر، به تفکیک مشاور یا حرارت (hot/warm/cold). بدون شماره.",
        "parameters": {"type": "object", "properties": {
            "consultant": {"type": "string"}, "temperature": {"type": "string", "enum": ["hot", "warm", "cold"]},
            "limit": {"type": "integer"}}}}},
    {"type": "function", "function": {
        "name": "property",
        "description": "جزئیات یک ملک با کد ملک (شمارهٔ سریال): مشخصات، برداشت هوش مصنوعی، برچسب عکس‌ها. بدون شمارهٔ مالک.",
        "parameters": {"type": "object", "required": ["serial_no"], "properties": {"serial_no": {"type": "integer"}}}}},
    {"type": "function", "function": {
        "name": "today_digest",
        "description": "همان خلاصهٔ صبحگاهی: آگهی‌های ۲۴ ساعت اخیر، اسکرپ‌ها، تطبیق‌ها، کاهش قیمت، صف تماس، بکاپ.",
        "parameters": {"type": "object", "properties": {}}}},
]

_TOOL_FUNCS = {
    "count_listings": tool_count_listings, "search_listings": tool_search_listings,
    "queue_status": tool_queue_status, "customers": tool_customers,
    "property": tool_property, "today_digest": tool_today_digest,
}


async def run_tool(db, name: str, arguments: Any) -> Dict[str, Any]:
    """One tool, by name, with the model's arguments — a bad name or a bad
    argument is an answer the model can read, not an exception."""
    fn = _TOOL_FUNCS.get(name)
    if fn is None:
        return {"error": f"ابزار «{name}» وجود ندارد"}
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else dict(arguments or {})
    except (TypeError, ValueError):
        args = {}
    try:
        return await fn(db, **args)
    except TypeError as e:
        return {"error": f"آرگومان نادرست: {str(e)[:120]}"}
    except Exception as e:
        logger.warning(f"[assistant] tool {name} failed: {type(e).__name__}: {e}")
        return {"error": f"ابزار جواب نداد: {type(e).__name__}"}


# ── the conversation ─────────────────────────────────────────────────────────

def _now_line() -> str:
    local = datetime.now(TEHRAN)
    return f"الان {_WEEKDAYS[local.weekday()]} {local.strftime('%H:%M')} به وقت تهران است."


async def answer(db, question: str, *, who: str = "", chat_id: str = "") -> Dict[str, Any]:
    """The tool-calling loop: the model asks for what it needs, reads it, and
    answers — at most MAX_ROUNDS tool rounds, then it must speak. Every
    question and answer is written to ai_chats."""
    from app.models.ai_chat import AiChat
    question = (question or "").strip()[:MAX_QUESTION]
    t0 = time.monotonic()
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": PERSONA + "\n" + _now_line()},
        {"role": "user", "content": llm.mask_pii(question)},
    ]
    used: List[str] = []
    text, ok, error = "", True, ""
    try:
        for _round in range(MAX_ROUNDS + 1):
            out = await llm.chat("write", messages, agent="assistant", db=db,
                                 tools=TOOLS if _round < MAX_ROUNDS else None, max_tokens=600, temperature=0.2)
            calls = out.get("tool_calls") or []
            if not calls:
                text = (out.get("content") or "").strip()
                break
            messages.append(out["message"])
            for call in calls:
                fn = call.get("function") or {}
                name = fn.get("name") or ""
                used.append(name)
                result = await run_tool(db, name, fn.get("arguments"))
                messages.append({"role": "tool", "tool_call_id": call.get("id"),
                                 "content": llm.mask_pii(json.dumps(result, ensure_ascii=False))[:6000]})
        if not text:
            text = "چیزی برای گفتن پیدا نکردم — در پنل نگاه کنید."
    except llm.LLMError as e:
        ok, error, text = False, str(e), "الان به مدل دسترسی ندارم؛ کمی بعد دوباره بپرسید."
    text = text[:MAX_REPLY]
    ms = int((time.monotonic() - t0) * 1000)
    try:
        db.add(AiChat(chat_id=str(chat_id or "")[:40], who=(who or "")[:120], question=question,
                      answer=text, tools=used, ms=ms, ok=ok, error=error[:300] or None))
        await db.commit()
    except Exception as e:
        logger.warning(f"[assistant] could not record the question: {e}")
        try:
            await db.rollback()
        except Exception:
            pass
    return {"text": text, "tools": used, "ms": ms, "ok": ok}


# ── Telegram ─────────────────────────────────────────────────────────────────

async def _offset(db) -> int:
    try:
        raw = (await secret_box.get_many(db, (KEY_OFFSET,))).get(KEY_OFFSET)
        return int(raw) if raw else 0
    except Exception:
        return 0


async def handle_update(db, update: Dict[str, Any], *, allowed: List[str], token: str, route: dict) -> Optional[str]:
    """One update: a text message from an allowed chat is answered; every
    chat that writes is remembered for the backup card; the rest is ignored
    without a word — an unknown chat does not learn that a bot listens."""
    from app.services.backup_service import tg_request
    msg = update.get("message") or {}
    chat = msg.get("chat") or {}
    cid = chat.get("id")
    text = (msg.get("text") or "").strip()
    if cid is None:
        return None
    await _remember_chat(db, chat)
    if str(cid) not in allowed or not text:
        return None
    who = " ".join(x for x in ((msg.get("from") or {}).get("first_name"), (msg.get("from") or {}).get("last_name")) if x) \
        or chat.get("title") or str(cid)
    if text.split()[0].split("@")[0] in ("/start", "/help"):
        reply = HELP
    else:
        try:
            await tg_request(token, "sendChatAction", route, timeout=10, json={"chat_id": cid, "action": "typing"})
        except Exception:
            pass
        reply = (await answer(db, text, who=who, chat_id=str(cid)))["text"]
    try:
        resp, _ = await tg_request(token, "sendMessage", route, timeout=20,
                                   json={"chat_id": cid, "text": reply, "disable_web_page_preview": True})
        if resp.status_code != 200:
            logger.warning(f"[assistant] telegram refused the reply to {cid}: {resp.status_code} {resp.text[:100]}")
    except Exception as e:
        logger.warning(f"[assistant] reply to {cid} not sent: {type(e).__name__}: {e}")
    return reply


async def poll_once(db) -> Dict[str, Any]:
    """One long poll: fetch what arrived, answer what may be answered, move
    the offset past everything seen — whether answered or not."""
    from app.services.backup_service import chat_ids, resolve_route, resolve_telegram, tg_request
    cfg = await resolve_telegram(db)
    allowed = chat_ids(cfg["chat_id"]) if cfg["token"] else []
    if not cfg["token"] or not allowed:
        return {"skipped": "unconfigured"}
    if not await enabled(db):
        return {"skipped": "disabled"}
    route = await resolve_route(db)
    offset = await _offset(db)
    resp, _ = await tg_request(cfg["token"], "getUpdates", route, timeout=POLL_TIMEOUT + 15,
                               params={"offset": offset, "timeout": POLL_TIMEOUT, "allowed_updates": '["message"]'})
    body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    if resp.status_code != 200 or not body.get("ok"):
        return {"error": f"{resp.status_code} {resp.text[:100]}"}
    updates = body.get("result") or []
    answered = 0
    for u in updates:
        try:
            if await handle_update(db, u, allowed=allowed, token=cfg["token"], route=route):
                answered += 1
        except Exception as e:
            logger.warning(f"[assistant] update {u.get('update_id')} failed: {type(e).__name__}: {e}")
        offset = max(offset, int(u.get("update_id", 0)) + 1)
        await secret_box.put(db, KEY_OFFSET, str(offset), "assistant")
    return {"updates": len(updates), "answered": answered, "offset": offset}


async def assistant_loop() -> None:
    """Runs for the life of the process. MATCH_ENGINE=0 disables, with the
    other agents; the card's switch and the backup's configuration gate
    each poll."""
    if not getattr(settings, "match_engine", True):
        logger.info("[assistant] disabled")
        return
    await asyncio.sleep(60)
    logger.info(f"[assistant] «{NAME}» armed — long-polling Telegram, answers for the backup's chats")
    while True:
        try:
            async with async_session_maker() as db:
                res = await poll_once(db)
            if res.get("skipped") or res.get("error"):
                if res.get("error"):
                    logger.warning(f"[assistant] poll failed: {res['error']}")
                await asyncio.sleep(60)
        except Exception as e:
            logger.warning(f"[assistant] poll crashed: {type(e).__name__}: {e}")
            await asyncio.sleep(30)


async def status(db) -> Dict[str, Any]:
    from app.models.ai_chat import AiChat
    from app.services.backup_service import chat_ids, resolve_telegram
    cfg = await resolve_telegram(db)
    day = datetime.now(TEHRAN).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    today = int((await db.execute(select(func.count(AiChat.id)).where(AiChat.created_at >= day))).scalar_one())
    last = (await db.execute(select(AiChat).order_by(AiChat.created_at.desc()).limit(1))).scalars().first()
    return {"name": NAME, "enabled": await enabled(db), "configured": llm.configured(),
            "telegram_configured": bool(cfg["token"] and chat_ids(cfg["chat_id"])),
            "allowed_chats": chat_ids(cfg["chat_id"]) if cfg["token"] else [],
            "questions_today": today, "last": last.to_dict() if last else None,
            "offset": await _offset(db)}
