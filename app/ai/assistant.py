"""
دستیار دفتر «سورین» — the office asks its own database, in Telegram.

«امروز چند آگهی زیر ۵ میلیارد در گلها اومد؟» «مشتری‌های داغ بدون تماس این هفته؟»
«صف تماس چطوره؟» — questions the panel answers with four clicks, asked in
the chat the office already lives in. The bot is the backup's bot and the
way out is the backup's route.

Who asks is a person, not a chat: a panel user who linked their own
Telegram account from their profile (a one-time code, sent to the bot in a
private chat), and only in that private chat. Each question is answered
with the asker's own panel rights — the permissions their account holds and
the who-sees-what rules of app/auth/visibility.py, applied inside every
tool. The backup's chat list is the backup's again: where backups and the
digest go, not who may read the database.

Only the assistant is a real agent — the model chooses which tool to call
and reads what comes back — and its tools are the whole of its power:
every one is a read, none takes a phone number out of the office, and a
question none of them can answer gets «نمی‌دانم», never an invented figure.
Every question and answer lands in ai_chats, so the panel can show who
asked what.

A customer never reaches the model by name: the tools say «مشتری-<id>», a
visible customer's name in the question is swapped for the same token on
the way out, and the answer's tokens become names again only for customers
the asker may see.

Polling, not a webhook: the server is in Iran behind a proxy, and
getUpdates through the route needs no inbound address. The backup card's
«پیدا کن» used getUpdates to list chats; consuming updates here would leave
it nothing to find, so the chats seen here are recorded for it.
"""
import asyncio
import hashlib
import hmac
import json
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import delete, func, or_, select

from app.auth.permissions import STAFF_ROLES, has_permission
from app.auth.visibility import actor, customers_visible_to, is_super, listings_visible_to, matches_visible_to
from app.config import get_settings
from app.database import async_session_maker
from app.models.telegram_link import TelegramLink
from app.models.user import User
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
    f"تو «{NAME}» هستی، دستیار دفتر املاک سورین در ارومیه. یک کاربر پنل دفتر در چت خصوصی تلگرام از تو "
    "دربارهٔ آگهی‌ها، مشتری‌ها و صف تماس خودش می‌پرسد؛ ابزارها فقط چیزی را برمی‌گردانند که خود او در پنل می‌بیند.\n"
    "قواعد:\n"
    "۱. فقط از نتیجهٔ ابزارها جواب بده. عددی که ابزار نداده، نگو. اگر ابزاری برای سؤال نیست یا "
    "نتیجه خالی است، بگو «نمی‌دانم» یا «چیزی پیدا نکردم» و بگو در پنل کجا می‌شود دید. "
    "اگر ابزار «دسترسی ندارید» داد، همین را بگو.\n"
    "۲. کوتاه و فارسی بنویس؛ اعداد را با ارقام فارسی و جداکنندهٔ هزارگان بنویس؛ قیمت‌ها را به «میلیون» و "
    "«میلیارد» تومان گرد کن. بدون واژهٔ انگلیسی، بدون اغراق.\n"
    "۳. شمارهٔ تلفن مالک یا مشتری را هرگز ننویس — بگو «شماره در پنل».\n"
    "۴. برای فهرست‌ها حداکثر ۵ مورد بنویس، هر کدام یک خط با کد ملک.\n"
    "۵. اسم مشتری‌ها را نمی‌دانی و حدس نمی‌زنی: هر مشتری را فقط با شناسه‌اش، همان‌طور که ابزار داده "
    "(مثل «مشتری-12»)، بنویس و «مشتری» را پیش از هیچ عدد دیگری نگذار.\n"
    "۶. اگر سؤال مبهم است، یک سؤال کوتاه بپرس به‌جای حدس‌زدن."
)

HELP = (
    f"سلام، {NAME} هستم — دستیار دفتر.\n"
    "می‌توانید بپرسید:\n"
    "• «امروز چند آگهی تازه اومد؟» / «آگهی‌های زیر ۵ میلیارد در گلها»\n"
    "• «صف تماس من چطوره؟» / «چند تطبیق منتظر تماسه؟»\n"
    "• «مشتری‌های داغ من» / «ملک ۱۰۰۷ چیه؟»\n"
    "• «خلاصهٔ امروز» (فقط مدیر)\n"
    "فقط در همین چت خصوصی و فقط از چیزهایی جواب می‌دهم که شما در پنل می‌بینید؛ شماره‌ای بیرون نمی‌دهم."
)
PRIVATE_ONLY = "لطفاً در چت خصوصی با من بپرسید"
NO_ACCESS = "دسترسی ندارید"


# ── who may ask ──────────────────────────────────────────────────────────────

def may_ask(user) -> bool:
    """An existing, active panel account with a staff role — whatever is linked."""
    return bool(user) and bool(user.is_active) and user.role in STAFF_ROLES


async def linked_user(db, telegram_user_id) -> Optional[User]:
    """The panel user this Telegram account is linked to, if they may ask."""
    # the link counts only while the account's token_version is the one it
    # was made under: a password change or «خروج از همه‌جا» signs it out too
    user = (await db.execute(select(User).join(TelegramLink, TelegramLink.user_id == User.id)
                             .where(TelegramLink.telegram_user_id == int(telegram_user_id),
                                    TelegramLink.token_version == func.coalesce(User.token_version, 0))
                             )).scalars().first()
    return user if may_ask(user) else None


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


# ── linking a Telegram account ───────────────────────────────────────────────
# The profile card asks for a code (app/api/routes/telegram_link.py); the
# user sends it to the bot in a private chat — «/start CODE» from the deep
# link, or typed. The code proves the panel user, Telegram proves the
# Telegram user. Codes live in Redis, hashed like the verification codes, so
# a Redis dump holds no live one.

LINK_TTL = 600                                     # seconds a code lives
LINK_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"   # no 0/O or 1/I/L to misread off a screen
LINK_LEN = 8
BAD_CODES, BAD_WINDOW = 5, 900                     # wrong codes per Telegram account per 15 min; then silence
HINT_EVERY = 3600                                  # «ask me in private»: once an hour per person per group
_NS = "sf:tglink"
_CODE = re.compile(rf"[{LINK_ALPHABET}]{{{LINK_LEN}}}")
_bot = {"token": "", "name": "", "retry_at": 0.0}


async def _redis():
    from app.database import get_redis
    return await get_redis()


def _code_key(code: str) -> str:
    digest = hmac.new(settings.secret_key.encode(), code.strip().upper().encode(), hashlib.sha256).hexdigest()
    return f"{_NS}:code:{digest}"


async def issue_link_code(user_id: int) -> str:
    """A fresh code for this panel user; the one before it stops working."""
    code = "".join(secrets.choice(LINK_ALPHABET) for _ in range(LINK_LEN))
    r = await _redis()
    await r.set(_code_key(code), str(int(user_id)), ex=LINK_TTL)
    old = await r.set(f"{_NS}:user:{int(user_id)}", _code_key(code), ex=LINK_TTL, get=True)
    if old and old != _code_key(code):
        await r.delete(old)
    return code


def _code_in(text: str) -> Optional[str]:
    """The code in «/start CODE», «/start@bot CODE» or a bare code, upper-cased."""
    parts = text.split()
    if parts[0].split("@")[0].lower() == "/start":
        return parts[1].upper() if len(parts) == 2 else None
    return parts[0].upper() if len(parts) == 1 and _CODE.fullmatch(parts[0].upper()) else None


async def _redeem(db, code: str, sender: Dict[str, Any]) -> Optional[str]:
    """Spend the code and link the sender's Telegram account to its user —
    in place of that user's old link, and of this account's old user. The
    reply for the chat, or None (silence) once this account has spent its
    wrong guesses: from then on nothing it sends is even tried."""
    r = await _redis()
    tid = int(sender["id"])
    bad = f"{_NS}:bad:{tid}"
    if int(await r.get(bad) or 0) >= BAD_CODES:
        return None
    uid = await r.getdel(_code_key(code))
    user = await db.get(User, int(uid)) if uid else None
    if not may_ask(user):
        if await r.incr(bad) == 1:
            await r.expire(bad, BAD_WINDOW)
        return "این کد درست نیست یا منقضی شده است؛ از پروفایل پنل کد تازه بگیرید."
    await db.execute(delete(TelegramLink).where(
        or_(TelegramLink.user_id == user.id, TelegramLink.telegram_user_id == tid)))
    db.add(TelegramLink(user_id=user.id, telegram_user_id=tid,
                        telegram_username=(sender.get("username") or "")[:64] or None,
                        token_version=int(user.token_version or 0)))
    await db.commit()
    logger.info(f"[assistant] telegram account {tid} linked to {user.username}")
    from app.services import audit
    await audit.record("telegram_link", actor=user, target_type="telegram", target_id=tid,
                       summary=f"حساب تلگرام {tid} به {user.username} وصل شد",
                       detail={"telegram_username": sender.get("username") or None})
    return f"حساب شما به {user.full_name or user.username} وصل شد"


async def _burn(code: str) -> None:
    """A code shown to a group is no longer its owner's alone."""
    await (await _redis()).delete(_code_key(code))


async def _hint_due(chat_id, telegram_user_id) -> bool:
    """«Ask me in private» once an hour per person per group, not under
    every message: a bot that is a group's admin reads all of them."""
    r = await _redis()
    return bool(await r.set(f"{_NS}:hint:{chat_id}:{telegram_user_id}", "1", ex=HINT_EVERY, nx=True))


async def bot_username(db) -> str:
    """The bot's @name for the profile's deep link, from getMe through the
    backup's route — asked once per token; a failure is retried after five
    minutes rather than on every click."""
    from app.services.backup_service import resolve_route, resolve_telegram, tg_request
    cfg = await resolve_telegram(db)
    if not cfg["token"]:
        return ""
    if _bot["token"] == cfg["token"] and (_bot["name"] or time.monotonic() < _bot["retry_at"]):
        return _bot["name"]
    try:
        resp, _ = await tg_request(cfg["token"], "getMe", await resolve_route(db), timeout=10)
        name = str(((resp.json() or {}).get("result") or {}).get("username") or "")
    except Exception as e:
        logger.info(f"[assistant] bot name unavailable: {type(e).__name__}")
        name = ""
    name = name if re.fullmatch(r"[A-Za-z0-9_]{3,64}", name) else ""
    _bot.update(token=cfg["token"], name=name, retry_at=time.monotonic() + 300)
    return name


# ── customers stay «مشتری-<id>» on the way out ───────────────────────────────

_FA = str.maketrans("يك", "یک")         # Arabic yeh and kaf, as some keyboards type them
_ASCII = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
# a token as a model may write it back: Persian or Arabic-Indic digits, a
# space, «#», any hyphen, a zero-width non-joiner, an Arabic yeh
_TOKEN = re.compile(r"مشتر[یي][\s\u200c\u200e\u200f#\-‐‑–—_ـ]*([0-9۰-۹٠-٩]+)")


def customer_token(customer_id: int) -> str:
    return f"مشتری-{int(customer_id)}"


async def _customer_names(db, user) -> Dict[int, str]:
    """Every customer this user may see, by id — the ones whose names are
    swapped for tokens going out and back into names coming in."""
    from app.models.crm_models import Customer
    if not may_ask(user) or not has_permission(user, "crm"):
        return {}
    # ponytail: every visible name is read per question; fine for thousands
    # of customers, a lookup by the question's words if the book grows past that
    rows = (await db.execute(customers_visible_to(select(Customer.id, Customer.full_name), user))).all()
    return {cid: name.strip() for cid, name in rows if name and name.strip()}


def _hide_names(text: str, names: Dict[int, str]) -> str:
    """Each name in `names` that appears in `text` → its token, longest name
    first, as whole words — a suffix after a zero-width non-joiner
    («امیری‌ها») does not save it. A one-word name that is also a street's
    («بهشتی») is swapped too: a search for that street then finds nothing,
    which is the price of the name never leaving."""
    text = text.translate(_FA)
    low = text.lower()
    for cid, name in sorted(names.items(), key=lambda kv: -len(kv[1])):
        words = name.translate(_FA).split()
        if words[0].lower() not in low:
            continue
        pattern = r"(?<!\w)" + r"[\s\u200c]+".join(map(re.escape, words)) + r"(?!\w)"
        text = re.sub(pattern, customer_token(cid), text, flags=re.I)
    return text


def _show_names(text: str, names: Dict[int, str]) -> str:
    """Tokens back into names — only for the customers in `names`, the ones
    this user may see. Any other token stays a token."""
    return _TOKEN.sub(lambda m: names.get(int(m.group(1).translate(_ASCII)), m.group(0)), text)


# ── the tools ────────────────────────────────────────────────────────────────
# Every tool reads with the asker's rights: `user` is the linked panel user,
# and the who-sees-what rules of app/auth/visibility.py narrow every query.
# Which permission each needs is checked once, in run_tool.

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


async def tool_count_listings(db, user, listing_type: Optional[str] = None, kind: Optional[str] = None,
                              district: Optional[str] = None, max_price: Optional[int] = None,
                              min_area: Optional[int] = None, max_area: Optional[int] = None,
                              rooms: Optional[int] = None, days: Optional[int] = None) -> Dict[str, Any]:
    from app.models.property import Property
    from app.ai.listing_reader import effective
    from app.services.match_service import _comparable_sql
    q = listings_visible_to(select(Property).where(Property.is_active == True), user)   # noqa: E712
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


async def tool_search_listings(db, user, query: str, listing_type: Optional[str] = None,
                               limit: int = 6) -> Dict[str, Any]:
    from app.models.property import Property
    limit = max(1, min(int(limit or 6), 10))
    ids: List[int] = []
    try:
        from app.ai import embeddings as _emb
        ids = [pid for pid, _ in await _emb.semantic_candidates(db, query, listing_type=listing_type, limit=limit)]
    except Exception as e:
        logger.info(f"[assistant] semantic search unavailable: {type(e).__name__}: {e}")
    if ids:
        # the nearest listings office-wide, then only the ones this user may see
        rows = (await db.execute(listings_visible_to(
            select(Property).where(Property.id.in_(ids), Property.is_active == True), user))).scalars().all()   # noqa: E712
        by_id = {p.id: p for p in rows}
        rows = [by_id[i] for i in ids if i in by_id]
    else:
        like = f"%{query.strip()[:60]}%"
        q = listings_visible_to(select(Property).where(
            Property.is_active == True,   # noqa: E712
            or_(Property.title.ilike(like), Property.description.ilike(like))), user)
        if listing_type in ("buy", "rent"):
            q = q.where(Property.listing_type == listing_type)
        rows = (await db.execute(q.order_by(Property.id.desc()).limit(limit))).scalars().all()
    return {"results": [_listing_line(p) for p in rows], "how": "semantic" if ids else "text"}


async def tool_queue_status(db, user=None) -> Dict[str, Any]:
    """The day's queues. For the assistant they are `user`'s: their calls
    exactly as the panel's «تماس‌های امروز» counts them (unassigned leads and
    theirs), the matches and the listings they may see. Without a user they
    are the office's — what the dashboard's «امروز» (/stats/today) shows."""
    from app.models.lead import Lead
    from app.models.crm_models import CustomerMatch, PriceAlert
    from app.models.property import Property
    now = datetime.now(timezone.utc)
    day = now.astimezone(TEHRAN).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)

    async def count(stmt):
        return int((await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one())
    due = select(Lead.id).where(
        Lead.status.in_(("new", "contacted")), Lead.phone_number.isnot(None),
        or_(Lead.next_call_at.is_(None), Lead.next_call_at <= now))
    matches = select(CustomerMatch.id).where(CustomerMatch.status == "new")
    drops = select(PriceAlert.id).where(PriceAlert.status == "new")
    today = select(Property.id).where(Property.created_at >= day)
    active = select(Property.id).where(Property.is_active == True)   # noqa: E712
    if user is not None:
        from app.api.routes.crm import _queue_query
        due = _queue_query(actor(user))
        matches = matches_visible_to(matches, user)
        drops, today, active = (listings_visible_to(q, user) for q in (
            drops.join(Property, Property.id == PriceAlert.property_id), today, active))
    return {"calls_due": await count(due),
            "matches_waiting": await count(matches),
            "price_drops_new": await count(drops),
            "listings_today": await count(today),
            "listings_active": await count(active)}


async def tool_customers(db, user, consultant: Optional[str] = None, temperature: Optional[str] = None,
                         customer_id: Optional[int] = None, limit: int = 8) -> Dict[str, Any]:
    """The customers this user may see, each only as «مشتری-<id>»: no name,
    no number, no notes — nothing a name or a number could hide in."""
    from app.models.crm_models import Customer, CustomerMatch
    limit = max(1, min(int(limit or 8), 15))
    q = customers_visible_to(select(Customer), user)
    if customer_id:
        q = q.where(Customer.id == int(customer_id))
    if consultant:
        q = q.where(Customer.consultant_name.ilike(f"%{consultant.strip()}%"))
    if temperature in ("hot", "warm", "cold"):
        q = q.where(Customer.temperature == temperature)
    rows = (await db.execute(q.order_by(Customer.updated_at.desc().nullslast()).limit(limit))).scalars().all()
    waiting = dict((await db.execute(matches_visible_to(
        select(CustomerMatch.customer_id, func.count(CustomerMatch.id))
        .where(CustomerMatch.status == "new", CustomerMatch.customer_id.in_([c.id for c in rows] or [0]))
        .group_by(CustomerMatch.customer_id), user))).all())
    return {"customers": [{
        "customer": customer_token(c.id), "temperature": c.temperature, "consultant": c.consultant_name,
        "wants": " · ".join(filter(None, (c.desired_district, c.desired_specs,
                                          f"تا {_fa_price(c.budget_max)}" if c.budget_max else None))),
        "deal_type": c.deal_type, "matches_waiting": int(waiting.get(c.id, 0)),
        "phone": "در پنل"} for c in rows]}


async def tool_property(db, user, serial_no: int) -> Dict[str, Any]:
    from app.models.property import Property
    from app.ai.photo_tagger import tags_fa
    p = (await db.execute(listings_visible_to(
        select(Property).where(Property.serial_no == int(serial_no)), user))).scalars().first()
    if not p:
        return {"found": False}   # a listing this user may not see does not exist for them
    facts = getattr(p, "ai_facts", None) or {}
    return {"found": True, **_listing_line(p),
            "year_built": p.year_built, "floor": p.floor, "document": facts.get("document") or p.document_type,
            "condition": facts.get("condition"), "suitable_for": facts.get("suitable_for") or [],
            "red_flags": facts.get("red_flags") or [], "photos": tags_fa(getattr(p, "ai_photo_tags", None)),
            "advertiser": "مشاور" if p.agency_suspected else "شخصی", "owner_phone": "در پنل"}


async def tool_today_digest(db, user) -> Dict[str, Any]:
    """The whole office's day: root and super_admin only (run_tool)."""
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
        "description": "فهرست مشتری‌های CRM با معیارشان و تعداد تطبیق منتظر، به تفکیک مشاور یا حرارت (hot/warm/cold). "
                       "هر مشتری فقط با شناسه‌اش مثل «مشتری-12» می‌آید؛ بدون اسم و شماره.",
        "parameters": {"type": "object", "properties": {
            "consultant": {"type": "string"}, "temperature": {"type": "string", "enum": ["hot", "warm", "cold"]},
            "customer_id": {"type": "integer", "description": "فقط یک مشتری: عدد شناسهٔ «مشتری-12»"},
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

# The panel permission each tool needs, checked as the panel checks it (root
# and super_admin hold them all); None: root and super_admin only.
_NEEDS = {"count_listings": "properties", "search_listings": "properties", "property": "properties",
          "customers": "crm", "queue_status": "crm", "today_digest": None}


async def run_tool(db, user, name: str, arguments: Any) -> Dict[str, Any]:
    """One tool, by name, with the model's arguments, read as `user` — a bad
    name, a bad argument or a permission the user does not hold is an
    answer the model can read, not an exception."""
    fn = _TOOL_FUNCS.get(name)
    if fn is None:
        return {"error": f"ابزار «{name}» وجود ندارد"}
    need = _NEEDS.get(name)
    if not may_ask(user) or not (is_super(user) if need is None else has_permission(user, need)):
        return {"error": NO_ACCESS}
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else dict(arguments or {})
    except (TypeError, ValueError):
        args = {}
    try:
        return await fn(db, user, **args)
    except TypeError as e:
        return {"error": f"آرگومان نادرست: {str(e)[:120]}"}
    except Exception as e:
        logger.warning(f"[assistant] tool {name} failed: {type(e).__name__}: {e}")
        return {"error": f"ابزار جواب نداد: {type(e).__name__}"}


# ── the conversation ─────────────────────────────────────────────────────────

def _now_line() -> str:
    local = datetime.now(TEHRAN)
    return f"الان {_WEEKDAYS[local.weekday()]} {local.strftime('%H:%M')} به وقت تهران است."


async def answer(db, question: str, *, user, chat_id: str = "") -> Dict[str, Any]:
    """The tool-calling loop for one panel user: the model asks for what it
    needs, reads it, and answers — at most MAX_ROUNDS tool rounds, then it
    must speak. Every tool reads with `user`'s rights. A customer the user
    may see goes out as «مشتری-<id>», in the question as in the tool results,
    and comes back by name in the answer. Every question and answer is
    written to ai_chats."""
    from app.models.ai_chat import AiChat
    if not may_ask(user):
        return {"text": NO_ACCESS, "tools": [], "ms": 0, "ok": False}   # no rights to read with: no model call
    question = (question or "").strip()[:MAX_QUESTION]
    t0 = time.monotonic()
    names = await _customer_names(db, user)
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": PERSONA + "\n" + _now_line()},
        {"role": "user", "content": llm.mask_pii(_hide_names(question, names))},
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
                # the arguments keep their tokens: search_listings sends its
                # query to the embedding model, which is a model too
                result = await run_tool(db, user, name, fn.get("arguments"))
                messages.append({"role": "tool", "tool_call_id": call.get("id"),
                                 "content": llm.mask_pii(_hide_names(json.dumps(result, ensure_ascii=False),
                                                                     names))[:6000]})
        if not text:
            text = "چیزی برای گفتن پیدا نکردم — در پنل نگاه کنید."
    except llm.LLMError as e:
        ok, error, text = False, str(e), "الان به مدل دسترسی ندارم؛ کمی بعد دوباره بپرسید."
    text = _show_names(text, names)[:MAX_REPLY]
    ms = int((time.monotonic() - t0) * 1000)
    try:
        db.add(AiChat(chat_id=str(chat_id or "")[:40], who=user.username[:120], question=question,
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


async def handle_update(db, update: Dict[str, Any], *, token: str, route: dict) -> Optional[str]:
    """One update. In a private chat: a link code, or a linked user's
    question. In a group: a linked user is asked to write in private, and a
    code shown there is burnt. Anyone else gets no word — an unknown chat
    does not learn that a bot listens. Every chat that writes is remembered
    for the backup card."""
    from app.services.backup_service import tg_request
    msg = update.get("message") or {}
    chat = msg.get("chat") or {}
    sender = msg.get("from") or {}
    cid = chat.get("id")
    text = (msg.get("text") or "").strip()
    if cid is None:
        return None
    await _remember_chat(db, chat)
    if not text or sender.get("id") is None or sender.get("is_bot"):
        return None
    code = _code_in(text)
    if chat.get("type") != "private":
        if code:
            await _burn(code)
        if not await linked_user(db, sender["id"]) or not await _hint_due(cid, sender["id"]):
            return None
        reply = PRIVATE_ONLY
    elif code:
        reply = await _redeem(db, code, sender)
        if reply is None:
            return None
    else:
        user = await linked_user(db, sender["id"])
        if not user:
            return None
        if text.split()[0].split("@")[0] in ("/start", "/help"):
            reply = HELP
        else:
            try:
                await tg_request(token, "sendChatAction", route, timeout=10, json={"chat_id": cid, "action": "typing"})
            except Exception:
                pass
            reply = (await answer(db, text, user=user, chat_id=str(cid)))["text"]
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
    from app.services.backup_service import resolve_route, resolve_telegram, tg_request
    cfg = await resolve_telegram(db)
    if not cfg["token"]:     # the bot is enough: who may ask is the links, not the backup's chats
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
            if await handle_update(db, u, token=cfg["token"], route=route):
                answered += 1
        except Exception as e:
            logger.warning(f"[assistant] update {u.get('update_id')} failed: {type(e).__name__}: {e}")
            await db.rollback()   # a failed write must not take the offset's write down with it
        offset = max(offset, int(u.get("update_id", 0)) + 1)
        await secret_box.put(db, KEY_OFFSET, str(offset), "assistant")
    return {"updates": len(updates), "answered": answered, "offset": offset}


async def assistant_loop() -> None:
    """Runs for the life of the process. MATCH_ENGINE=0 disables, with the
    other agents; the card's switch and the backup's bot token gate each
    poll."""
    if not getattr(settings, "match_engine", True):
        logger.info("[assistant] disabled")
        return
    await asyncio.sleep(60)
    logger.info(f"[assistant] «{NAME}» armed — long-polling Telegram, answers linked users in private chats")
    from app.services.supervisor import beat
    while True:
        beat("assistant")
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
    from app.services.backup_service import resolve_telegram
    cfg = await resolve_telegram(db)
    day = datetime.now(TEHRAN).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    today = int((await db.execute(select(func.count(AiChat.id)).where(AiChat.created_at >= day))).scalar_one())
    last = (await db.execute(select(AiChat).order_by(AiChat.created_at.desc()).limit(1))).scalars().first()
    linked = (await db.execute(select(User.username).join(TelegramLink, TelegramLink.user_id == User.id)
                               .order_by(User.username))).scalars().all()
    return {"name": NAME, "enabled": await enabled(db), "configured": llm.configured(),
            "telegram_configured": bool(cfg["token"]),
            "linked_users": list(linked),
            "questions_today": today, "last": last.to_dict() if last else None,
            "offset": await _offset(db)}
