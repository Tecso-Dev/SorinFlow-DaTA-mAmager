"""
Property matching — «تطابق‌سازی».

Two directions:
  • similar_to_property(): the customer liked a listing → show ones like it
  • matches_for_customer(): rank listings against a customer's BANT profile

Scoring is local and deterministic (fast, free, always available). When an
LLM key is configured the top candidates are additionally re-ranked and
given a Persian reason, but the local order is what ships if the LLM is
unavailable — the feature never breaks because of a missing key.
"""
import asyncio
import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

import httpx
from loguru import logger
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.property import Property
from app.ai.listing_reader import effective

settings = get_settings()

# ── tunables ────────────────────────────────────────────────────────────
PRICE_TOLERANCE = 0.20   # closeness curve: 0 at ±20%
AREA_TOLERANCE = 0.35    # ±35%
DEPOSIT_TOLERANCE = 0.50 # rentals: the deposit's own closeness, 0 at ±50%
DEPOSIT_SHAPE_MAX = 3    # …and beyond 3× apart the deal is a different shape, whatever the total
CANDIDATE_POOL = 300     # rows scored before trimming to the top N (customer matches)

# «مشابه» for a listing means the same neighbourhood at about the same price.
# The tight band is what a person calls the same price; the wide band is the
# outer fence — nothing beyond it is offered at all, and inside it only when
# the tight band has too little to show.
PRICE_BAND = 0.15
PRICE_BAND_WIDE = 0.35
SIMILAR_POOL = 1500      # same city + same deal type, inside the wide band
SEMANTIC_EXTRA = 30      # candidates the customer's own words add to the pool (app/ai/embeddings.py)
MIN_CLOSE = 3            # below this many tight matches the wide band is shown too

# The parts of a district string that say nothing about WHICH district:
# «خ گلها» and «خیابان گلها» are one place, so is «بلوار سعدی» and «سعدی».
_DISTRICT_NOISE = ("خیابان", "خ.", "خ", "بلوار", "بلوار.", "کوی", "کوچه", "میدان", "شهرک",
                   "بزرگراه", "اتوبان", "جاده", "محله", "منطقه", "شهید", "دکتر", "استاد")


def district_key(text: Optional[str]) -> str:
    """One spelling per district, so «خ گلها» and «خیابان گلها» compare equal.

    Persian/Arabic letter variants unified, the road-type words dropped,
    digits normalised, spaces collapsed. Empty when nothing is left."""
    if not text:
        return ""
    # Arabic yeh/kaf and the two heh forms → Persian; tashkeel dropped
    t = str(text).translate(str.maketrans({"ي": "ی", "ك": "ک", "ۀ": "ه", "ة": "ه"}))
    t = "".join(ch for ch in t if not ("\u064b" <= ch <= "\u0652"))
    t = t.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
    t = t.replace("\u200c", " ").replace("-", " ").replace("،", " ")
    words = [w for w in t.split() if w not in _DISTRICT_NOISE]
    return " ".join(words).strip()


# Canonical property families. Scraped rows carry whatever Persian text Divar
# put in «نوع ملک» while manual leads carry an English kind, so both
# vocabularies are normalised before anything is compared. Order matters:
# «خانه کلنگی» is land being sold, not a house to live in.
_FAMILY_WORDS = (
    ("apartment", ("آپارتمان", "اپارتمان", "پارتمان", "واحد مسکونی", "apartment", "apt")),
    ("land",      ("زمین", "کلنگی", "قطعه", "باغ", "land", "plot")),
    ("shop",      ("مغازه", "تجاری", "سوپر", "shop", "store")),
    ("office",    ("دفتر", "اداری", "office")),
    ("house",     ("ویلا", "ویلایی", "خانه", "دوبلکس", "حیاط", "villa", "house", "duplex")),
)
# a bedroom count means nothing for these, so a "۲ خواب" request excludes them
_ROOMLESS_FAMILIES = {"land", "shop", "office"}


def _family(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    blob = str(text)
    for fam, words in _FAMILY_WORDS:
        if any(w in blob for w in words):
            return fam
    return None


def property_family(p: Property) -> Optional[str]:
    """apartment | house | land | shop | office, or None when unreadable.

    The explicit type wins over the category, which wins over the title —
    titles are marketing copy and mention everything.
    """
    return _family(p.property_type) or _family(p.category_name) or _family(p.title)


def _price_of(p: Property) -> Optional[int]:
    """The comparable headline number for a listing."""
    if p.listing_type == "rent":
        return p.deposit or p.rent_price
    return p.total_price or p.price


# «تبدیل»: the market swaps roughly 30 million of deposit for one million of
# monthly rent, so 350/10 and 350/25 are not the same price even though the
# deposits match. Rentals are compared on this one figure.
RENT_TO_DEPOSIT = 30


def _comparable(p: Property) -> Optional[int]:
    """One number to compare prices on: the total for a sale, the deposit
    plus the converted rent for a rental."""
    if p.listing_type == "rent":
        if not (p.deposit or p.rent_price):
            return None
        return (p.deposit or 0) + (p.rent_price or 0) * RENT_TO_DEPOSIT
    return p.total_price or p.price


def _closeness(a: Optional[float], b: Optional[float], tolerance: float) -> Optional[float]:
    """1.0 when identical, 0.0 once the gap exceeds `tolerance` (relative)."""
    if not a or not b:
        return None
    diff = abs(a - b) / max(a, b)
    if diff >= tolerance:
        return 0.0
    return 1.0 - (diff / tolerance)


def _text_overlap(a: Optional[str], b: Optional[str]) -> Optional[float]:
    """Rough token overlap for district / neighborhood names."""
    if not a or not b:
        return None
    ta = {t for t in str(a).split() if len(t) > 2}
    tb = {t for t in str(b).split() if len(t) > 2}
    if not ta or not tb:
        return None
    return len(ta & tb) / len(ta | tb)


def score_similarity(target: Property, cand: Property) -> Dict[str, Any]:
    """Weighted similarity of `cand` to `target`. Returns score 0..100 + reasons."""
    parts: List[tuple] = []   # (weight, value 0..1, reason)
    # the listing as the reader completed it: the scraped column when set, the
    # fact from the ad's own text where the scraper left a gap
    te, ce = effective(target), effective(cand)

    # The same figure the fence and the gap are measured on: the total for a
    # sale, deposit + 30 × rent for a rental. This used to be the deposit
    # alone, so a 150M/40M listing scored a 1.5B full-deposit one as «اختلاف
    # قیمت» and «خارج از محدوده» while the tier said it was 9% apart.
    tp, cp = _comparable(target), _comparable(cand)
    price_close = _closeness(tp, cp, PRICE_TOLERANCE)
    if price_close is not None:
        parts.append((30, price_close, "قیمت نزدیک" if price_close > .5 else "اختلاف قیمت"))
    # Budget is the deal-breaker in practice: a listing far outside the
    # target's price band is not a substitute no matter how alike the rest is.
    price_penalty = 1.0
    if tp and cp:
        gap = abs(tp - cp) / max(tp, cp)
        if gap > PRICE_TOLERANCE:
            # fade out smoothly; ~2x the price lands near a third of the score
            price_penalty = max(0.15, 1 - (gap - PRICE_TOLERANCE) * 1.6)

    # A rental is a shape as well as a total: a person who put 150M down and
    # pays rent does not have 1.5B to put down, however the totals compare —
    # and conversion («تبدیل») is negotiable within limits, not from anything
    # to anything. Same total, different shape: close, not the same.
    shape_penalty = 1.0
    if target.listing_type == "rent" and cand.listing_type == "rent":
        td, cd = target.deposit or 0, cand.deposit or 0
        if td and cd:
            shape = _closeness(td, cd, DEPOSIT_TOLERANCE)
            parts.append((15, shape, "ودیعه نزدیک" if shape > .5 else "ودیعهٔ متفاوت"))
            # «قابل تبدیل» in either ad: the owner said the shape is negotiable
            if max(td, cd) / min(td, cd) > DEPOSIT_SHAPE_MAX and not (te["convertible"] or ce["convertible"]):
                shape_penalty = 0.6

    area_close = _closeness(target.area, cand.area, AREA_TOLERANCE)
    if area_close is not None:
        parts.append((20, area_close, "متراژ مشابه" if area_close > .5 else "متراژ متفاوت"))

    if target.rooms is not None and cand.rooms is not None:
        same = 1.0 if target.rooms == cand.rooms else (0.5 if abs(target.rooms - cand.rooms) == 1 else 0.0)
        parts.append((15, same, f"{cand.rooms} خواب"))

    # location: same district is the strongest signal after price
    loc = _text_overlap(target.district, cand.district)
    if loc is None:
        loc = _text_overlap(target.neighborhood, cand.neighborhood)
    if loc is not None:
        parts.append((20, loc, "همان منطقه" if loc > .3 else "منطقه دیگر"))
    elif target.city_name and cand.city_name:
        parts.append((10, 1.0 if target.city_name == cand.city_name else 0.0, "همان شهر"))

    # Type is a gate, not a nudge: a shop with the right area and price is not
    # a substitute for an apartment, so a mismatch collapses the score instead
    # of costing it ten points.
    family_penalty = 1.0
    tf, cf = te["kind"], ce["kind"]
    if tf and cf:
        if tf == cf:
            parts.append((10, 1.0, "همان نوع ملک"))
        else:
            parts.append((10, 0.0, ""))
            family_penalty = 0.2

    # amenities the target has, that the candidate also has
    amen = [("has_elevator", "آسانسور"), ("has_parking", "پارکینگ"),
            ("has_storage", "انباری"), ("has_balcony", "بالکن")]
    wanted = [(f, fa) for f, fa in amen if te.get(f)]
    if wanted:
        have = [fa for f, fa in wanted if ce.get(f)]
        parts.append((5, len(have) / len(wanted), "امکانات: " + "، ".join(have) if have else "بدون امکانات مشترک"))

    # «متن مشابه»: how alike the two ads read, when both carry a vector
    # (app/ai/embeddings.py). Wording, not numbers — a nudge, never a gate.
    # Raw cosine of two real ads sits around 0.5–0.7 and a near rewrite above
    # 0.9, so 0.5..1 is rescaled to 0..1.
    from app.ai.embeddings import text_similarity
    txt = text_similarity(target, cand)
    if txt is not None:
        parts.append((10, max(0.0, min(1.0, (txt - 0.5) / 0.5)), "متن مشابه"))

    total_w = sum(w for w, _v, _r in parts) or 1
    score = sum(w * v for w, v, _r in parts) / total_w * 100 * price_penalty * family_penalty * shape_penalty
    reasons = [r for w, v, r in parts if v > 0.5]
    if price_penalty < 0.9:
        reasons.append("خارج از محدوده قیمت")
    if shape_penalty < 1:
        reasons.append("ودیعه خیلی متفاوت — تبدیل لازم")
    if family_penalty < 1:
        reasons.append("نوع ملک متفاوت است")
    return {"score": round(score), "reasons": reasons}


def customer_intent(customer) -> Dict[str, Any]:
    """What the customer is actually shopping for.

    The intake form asks for city, type and buy-or-rent outright. Those answers
    win. Customers recorded before those fields existed — or left blank — fall
    back to reading the free-text BANT answers, so nothing stops matching.
    red_lines is deliberately excluded from that fallback: it lists what the
    customer does NOT want, and reading a type out of it would invert the gate.
    """
    import re as _re
    blob = " ".join(filter(None, [customer.desired_specs, getattr(customer, "notes", None)]))

    family = getattr(customer, "desired_type", None) or _family(blob)
    deal = getattr(customer, "deal_type", None)
    if deal not in ("buy", "rent"):
        deal = "rent" if any(w in blob for w in ("رهن", "اجاره", "ودیعه")) else "buy"

    return {
        "family": family or None,
        "listing_type": deal,
        "city": (getattr(customer, "desired_city", None) or "").strip() or None,
        # «۲ خواب» only makes sense for somewhere to live
        "wants_rooms": bool(_re.search(r"خواب", blob)) or family in ("apartment", "house"),
        "explicit": bool(getattr(customer, "desired_type", None)
                         or getattr(customer, "desired_city", None)),
    }


def customer_wants(customer, cand: Property, intent: Optional[Dict[str, Any]] = None) -> bool:
    """Hard gate: could this listing ever be the right answer for them?

    Scoring alone cannot express this. A rental deposit looks like a bargain
    next to a purchase budget, and a shop with the right area and price
    outscores a real apartment — both have to be excluded outright.
    """
    intent = intent or customer_intent(customer)
    if cand.listing_type and cand.listing_type != intent["listing_type"]:
        return False
    if intent.get("city") and cand.city_name and cand.city_name != intent["city"]:
        return False
    fam = effective(cand)["kind"]
    if intent["family"]:
        # An explicitly chosen type is binding even when the ad is unreadable:
        # the agent said what they want, so do not fall back to guessing.
        if intent.get("explicit") and fam is None:
            return False
        if fam and fam != intent["family"]:
            return False
    if intent["wants_rooms"] and fam in _ROOMLESS_FAMILIES:
        return False
    return True


def score_for_customer(customer, cand: Property) -> Dict[str, Any]:
    """How well a listing fits a customer's BANT profile. 0..100 + reasons."""
    parts: List[tuple] = []
    reasons: List[str] = []
    budget_penalty = 1.0

    price = _price_of(cand)
    if customer.budget_max and price:
        if price <= customer.budget_max:
            # closer to (but under) budget scores higher than far below
            ratio = price / customer.budget_max
            val = 0.7 + 0.3 * ratio if ratio >= 0.5 else 0.7
            parts.append((35, val, ""))
            reasons.append("داخل بودجه")
        else:
            over = (price - customer.budget_max) / customer.budget_max
            # up to 10% over budget is still worth showing
            parts.append((35, max(0.0, 1 - over / 0.10) * 0.4, ""))
            if over <= 0.10:
                reasons.append("کمی بالاتر از بودجه")
            else:
                # budget is the hardest constraint there is: a perfect match
                # they cannot afford must not sit near the top of the list
                budget_penalty = max(0.15, 1 - (over - 0.10) * 1.6)
                reasons.append("بالاتر از بودجه")

    # The best of the three places a district can be written. Chained with
    # `or`, a zero overlap on the district fell through to the (empty)
    # neighbourhood and address and came back None — so a listing in the
    # WRONG district scored as if the district were unknown, and the engine
    # rang a گلها customer about a سعدی flat.
    known = [o for o in (_text_overlap(customer.desired_district, cand.district),
                         _text_overlap(customer.desired_district, cand.neighborhood),
                         _text_overlap(customer.desired_district, cand.address)) if o is not None]
    loc = max(known) if known else None
    district_penalty = 1.0
    if loc is not None:
        parts.append((30, loc, ""))
        if loc > 0.3:
            reasons.append("منطقه درخواستی")
        elif loc == 0:
            # they named a district and this is not it: a real reason to
            # rank it low, not a missing criterion
            district_penalty = 0.6
            reasons.append("منطقهٔ دیگر")

    # desired_specs is free text like «۱۰۰ متر / ۲ خواب» — pull numbers out
    specs = str(customer.desired_specs or "")
    import re as _re
    nums = [int(n) for n in _re.findall(r"\d+", specs)]
    want_area = next((n for n in nums if n >= 30), None)
    want_rooms = next((n for n in nums if n < 10), None)
    if want_area and cand.area:
        close = _closeness(want_area, cand.area, AREA_TOLERANCE) or 0.0
        parts.append((20, close, ""))
        if close > 0.5:
            reasons.append(f"متراژ حدود {cand.area} متر")
    if want_rooms is not None and cand.rooms is not None:
        same = 1.0 if cand.rooms == want_rooms else (0.5 if abs(cand.rooms - want_rooms) == 1 else 0.0)
        parts.append((15, same, ""))
        if same == 1.0:
            reasons.append(f"{cand.rooms} خواب")

    total_w = sum(w for w, _v, _r in parts) or 1
    # The score is shown to an agent as a percentage, so it must not read as
    # certainty when only one criterion was known. A customer with nothing but
    # a budget cannot produce a 100% match, and neither can a listing whose
    # area was never recorded.
    coverage = 0.55 + 0.45 * (total_w / 100)
    score = sum(w * v for w, v, _r in parts) / total_w * 100 * budget_penalty * district_penalty * coverage

    # red lines act as a hard-ish filter
    red = str(customer.red_lines or "")
    if red:
        blob = " ".join(filter(None, [cand.title, cand.description, cand.district,
                                      cand.neighborhood, cand.unit_status]))
        for token in [t.strip() for t in _re.split(r"[،,\n]", red) if len(t.strip()) > 2]:
            if token in blob:
                score *= 0.35
                reasons.append(f"⚠ شامل خط قرمز: {token}")
                break

    return {"score": round(score), "reasons": reasons}


async def _llm_rerank(prompt_items: List[Dict[str, Any]], context: str) -> Dict[int, str]:
    """Ask the configured LLM for a Persian reason per candidate.

    Returns {property_id: reason}. Any failure returns {} so callers keep the
    local ranking untouched — the ranking is ours, the sentence is the
    model's, and a missing sentence costs nothing.

    Goes through app/services/llm.py like every other agent: the same key,
    the same per-job model, the same daily cap, one row in the ledger.
    """
    from app.services import llm as _llm
    if not prompt_items or not _llm.configured():
        return {}

    listing_lines = "\n".join(
        f"- id={i['id']} | {_llm.mask_pii(i['title'])} | {i['area'] or '?'}m² | {i['rooms'] if i['rooms'] is not None else '?'}خواب"
        f" | {i['price'] or '?'} تومان | {i['district'] or i['city'] or '-'}"
        f" | امتیاز تطابق: {i.get('score', '?')}٪"
        for i in prompt_items
    )
    # The model only writes the sentence next to each row — the ranking is the
    # local score and never changes. Telling it the score keeps the sentence
    # from contradicting the number the agent is reading beside it.
    prompt = (
        "تو یک مشاور املاک حرفه‌ای هستی. معیار زیر و فهرست آگهی‌ها را ببین و برای هر آگهی "
        "یک دلیل کوتاه فارسی (حداکثر ۱۲ کلمه) بنویس که چرا مناسب است یا نیست.\n"
        "امتیاز تطابق هر آگهی محاسبه شده و درست است؛ دلیل تو باید با آن هم‌خوان باشد — "
        "برای امتیاز پایین ننویس که مناسب است.\n\n"
        f"معیار: {context}\n\nآگهی‌ها:\n{listing_lines}\n\n"
        'فقط JSON برگردان به شکل: {"results":[{"id":123,"reason":"..."}]}'
    )
    try:
        out = await _llm.chat("write", [{"role": "user", "content": prompt}],
                              agent="explainer", json_mode=True,
                              max_tokens=60 * max(1, len(prompt_items)), timeout=25)
        data = out.get("data") or {}
        return {int(r["id"]): str(r.get("reason", ""))[:120] for r in data.get("results", []) if r.get("id")}
    except Exception as e:
        logger.warning(f"[match] LLM re-rank skipped: {e}")
        return {}


# ── reasons and semantic candidates: cached, never inline ───────────────────
# A model call can take up to 90 s (app/services/llm.py, reasoning models), so
# a page must never wait on one. What each depends on is fingerprinted into
# the Redis key; a miss schedules the one call that will fill it — behind a
# short lock, so a burst of page loads for the same row set costs one call —
# and the request answers with the deterministic ranking right away. A Redis
# outage is treated exactly like a model outage: quietly nothing extra, ever.
REASON_CACHE_TTL = 7 * 24 * 3600      # a week — candidates turn over faster than this
# The reasons' key carries the candidates, so a new listing is a new key; the
# semantic key carries only the need text, so a week-old answer would hide
# every listing embedded since. The embed pass runs every few minutes.
SEMANTIC_CACHE_TTL = 6 * 3600
LOCK_TTL = 200                        # a reasoning model's 90s, twice (chat()'s one retry on a malformed answer)

_background_tasks: set = set()


def _spawn(coro) -> None:
    """Fire-and-forget on the running loop, with a strong reference — an
    asyncio task nothing holds can be garbage-collected mid-flight."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def _fingerprint(*parts: Any) -> str:
    """One short key for whatever a cached answer depends on. Any change of
    any part invalidates it instead of serving a stale sentence."""
    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:24]


async def _compute_and_cache_reasons(key: str, lock_key: str,
                                     prompt_items: List[Dict[str, Any]], context: str) -> None:
    from app.database import get_redis
    try:
        reasons = await _llm_rerank(prompt_items, context)
        if reasons:
            r = await get_redis()
            await r.set(key, json.dumps(reasons), ex=REASON_CACHE_TTL)
    except Exception as e:
        logger.warning(f"[match] background re-rank failed: {type(e).__name__}: {e}")
    finally:
        try:
            r = await get_redis()
            await r.delete(lock_key)
        except Exception:
            pass   # the lock's own TTL clears it either way


async def _attach_reasons(kind: str, source_id: int, results: List[Dict[str, Any]], context: str) -> bool:
    """Fill `ai_reason` on `results` from the cache. On a miss, schedule the
    one model call that will fill it and report the rows as pending — never
    raises, so a Redis outage costs the reasons, not the ranking."""
    if not results:
        return False
    fp = _fingerprint(context, *(f"{row['id']}:{row['score']}:{row.get('price')}" for row in results))
    key = f"match:reason:{kind}:{source_id}:{fp}"
    try:
        from app.database import get_redis
        r = await get_redis()
        cached = await r.get(key)
    except Exception as e:
        logger.info(f"[match] reason cache unavailable: {type(e).__name__}: {e}")
        return False
    if cached:
        try:
            reasons = {int(k): v for k, v in json.loads(cached).items()}
        except Exception:
            reasons = {}
        for row in results:
            if reasons.get(row["id"]):
                row["ai_reason"] = reasons[row["id"]]
        return False
    lock_key = f"match:reason:lock:{kind}:{source_id}:{fp}"
    try:
        got_lock = await r.set(lock_key, "1", nx=True, ex=LOCK_TTL)
    except Exception:
        got_lock = False
    if got_lock:
        prompt_items = [{"id": row["id"], "title": row["title"], "area": row["area"], "rooms": row["rooms"],
                         "price": row["price"], "district": row["district"], "city": row["city_name"],
                         "score": row["score"]} for row in results]
        _spawn(_compute_and_cache_reasons(key, lock_key, prompt_items, context))
    return True


async def _compute_and_cache_semantic(key: str, lock_key: str, need: str,
                                      city: Optional[str], listing_type: Optional[str]) -> None:
    from app.database import async_session_maker, get_redis
    from app.ai import embeddings as _emb
    from app.services import llm as _llm
    try:
        async with async_session_maker() as session:
            pairs = list(await _emb.semantic_candidates(
                session, need, city=city, listing_type=listing_type, limit=SEMANTIC_EXTRA))
        r = await get_redis()
        await r.set(key, json.dumps(pairs), ex=SEMANTIC_CACHE_TTL)
    except _llm.LLMError as e:
        logger.info(f"[match] semantic candidates skipped: {e}")
    except Exception as e:
        logger.warning(f"[match] semantic candidates failed: {type(e).__name__}: {e}")
    finally:
        try:
            r = await get_redis()
            await r.delete(lock_key)
        except Exception:
            pass


async def _cached_semantic_candidates(need: str, city: Optional[str],
                                      listing_type: Optional[str]) -> Dict[int, float]:
    """The cached id→score map for this need text, or {} while a background
    call fills it (a fresh session of its own — the request's is gone by
    then). Never raises: a Redis outage just means no extras this pass, the
    same as an LLM outage today."""
    fp = _fingerprint(need, city, listing_type)
    key = f"match:semantic:{fp}"
    try:
        from app.database import get_redis
        r = await get_redis()
        cached = await r.get(key)
    except Exception as e:
        logger.info(f"[match] semantic cache unavailable: {type(e).__name__}: {e}")
        return {}
    if cached is not None:
        try:
            return {int(pid): score for pid, score in json.loads(cached)}
        except Exception:
            return {}
    lock_key = f"match:semantic:lock:{fp}"
    try:
        got_lock = await r.set(lock_key, "1", nx=True, ex=LOCK_TTL)
    except Exception:
        got_lock = False
    if got_lock:
        _spawn(_compute_and_cache_semantic(key, lock_key, need, city, listing_type))
    return {}


def _brief(p: Property, score: int, reasons: List[str]) -> Dict[str, Any]:
    return {
        "id": p.id,
        "serial_no": p.serial_no,
        "title": p.title,
        "city_name": p.city_name,
        "district": p.district,
        "area": p.area,
        "rooms": p.rooms,
        "listing_type": p.listing_type,
        "price": _price_of(p),
        # for a rental both halves, and the one figure everything is compared on
        "deposit": p.deposit if p.listing_type == "rent" else None,
        "rent_price": p.rent_price if p.listing_type == "rent" else None,
        "comparable": _comparable(p),
        # the reader's one line and its warnings, for the card
        "ai_summary": (getattr(p, "ai_facts", None) or {}).get("summary"),
        "red_flags": (getattr(p, "ai_facts", None) or {}).get("red_flags") or [],
        "thumbnail_url": p.thumbnail_url,
        "url": p.url,
        "phone_number": p.phone_number,
        "score": score,
        "reasons": reasons,
    }


def _comparable_sql(listing_type: Optional[str]):
    """_comparable() as a SQL expression, so the fence is in the query."""
    if listing_type == "rent":
        return (func.coalesce(Property.deposit, 0)
                + func.coalesce(Property.rent_price, 0) * RENT_TO_DEPOSIT)
    return func.coalesce(Property.total_price, Property.price)


def rank_similar(prop: Property, cands, limit: int = 12) -> List[Dict[str, Any]]:
    """Order candidates the way a consultant would: this neighbourhood first,
    then by price closeness — and never beyond the wide price band.

    Tiers, in order: (1) same district, price within ±15%; (2) same district,
    within ±35%; (3) another district of the city, within ±15%; (4) another
    district, within ±35%. The wide tiers are shown only when the tight ones
    have fewer than MIN_CLOSE listings, so a lead in a well-covered street
    never sees the other side of town.
    """
    tp = _comparable(prop)
    tkey = district_key(prop.district) or district_key(prop.neighborhood)
    target_family = effective(prop)["kind"]
    rows = []
    for c in cands:
        if c.id == prop.id:
            continue
        if target_family and effective(c)["kind"] not in (None, target_family):
            continue
        cp = _comparable(c)
        # relative to THIS listing's price: 150 against 100 is 50% off, not 33%
        gap = abs(tp - cp) / tp if tp and cp else None
        if gap is not None and gap > PRICE_BAND_WIDE:
            continue
        ckey = district_key(c.district) or district_key(c.neighborhood)
        same = bool(tkey) and ckey == tkey
        if not same and tkey and ckey:
            same = (_text_overlap(tkey, ckey) or 0) >= 0.5
        close = gap is not None and gap <= PRICE_BAND
        tier = (1 if same and close else 2 if same else 3 if close else 4)
        # within a tier, a rental of the same shape (deposit within 3×) comes
        # before one that needs converting — the total may match to the
        # toman and still be a deal this person cannot do
        far_shape = bool(prop.listing_type == "rent" and c.listing_type == "rent"
                         and (prop.deposit or 0) and (c.deposit or 0)
                         and max(prop.deposit, c.deposit) / min(prop.deposit, c.deposit) > DEPOSIT_SHAPE_MAX)
        s = score_similarity(prop, c)
        rows.append({"tier": tier, "same_district": same, "gap": gap, "far_shape": far_shape,
                     "score": s["score"], "reasons": s["reasons"], "cand": c})
    rows.sort(key=lambda r: (r["tier"], r["far_shape"], r["gap"] if r["gap"] is not None else 1.0, -r["score"]))
    tight = [r for r in rows if r["tier"] in (1, 3)]
    chosen = rows if len(tight) < MIN_CLOSE else tight
    out = []
    for r in chosen[:limit]:
        b = _brief(r["cand"], r["score"], r["reasons"])
        b["same_district"] = r["same_district"]
        b["price_gap_pct"] = round(r["gap"] * 100) if r["gap"] is not None else None
        b["price_direction"] = (None if r["gap"] is None or not tp
                                else "higher" if (_comparable(r["cand"]) or 0) > tp
                                else "lower" if (_comparable(r["cand"]) or 0) < tp else "same")
        out.append(b)
    return out


async def similar_to_property(db: AsyncSession, prop: Property, limit: int = 12,
                              use_llm: bool = True) -> Tuple[List[Dict[str, Any]], bool]:
    """Listings most like `prop`: same city, same deal type, same
    neighbourhood first, price within a tight band.

    Returns (results, reasons_pending) — pending is true when a background
    call was just scheduled to fill `ai_reason` on some rows; the ranking
    itself never waits on it."""
    q = select(Property).where(
        Property.is_active == True,
        Property.id != prop.id,
    )
    if prop.city_name:
        q = q.where(Property.city_name == prop.city_name)
    if prop.listing_type:
        q = q.where(Property.listing_type == prop.listing_type)
    # The outer fence goes into the query, so the pool is the listings that
    # could be offered at all — not the first 300 rows of the city, which is
    # what it used to be, and which missed most of the same street.
    tp = _comparable(prop)
    if tp:
        lo, hi = int(tp * (1 - PRICE_BAND_WIDE)), int(tp * (1 + PRICE_BAND_WIDE))
        q = q.where(_comparable_sql(prop.listing_type).between(lo, hi))
    cands = (await db.execute(q.limit(SIMILAR_POOL))).scalars().all()
    results = rank_similar(prop, cands, limit)

    pending = False
    if use_llm and results:
        from app.services import llm as _llm
        ctx = (f"ملکی مشابه این: {_llm.mask_pii(prop.title)} — {prop.area or '?'} متر، "
               f"{prop.rooms if prop.rooms is not None else '?'} خواب، "
               f"{_price_of(prop) or '?'} تومان، منطقه {prop.district or prop.city_name or '-'}")
        pending = await _attach_reasons("property", prop.id, results, ctx)
    return results, pending


async def matches_for_customer(db: AsyncSession, customer, limit: int = 12,
                               use_llm: bool = True, city: Optional[str] = None
                               ) -> Tuple[List[Dict[str, Any]], bool]:
    """Listings that fit a customer's budget / district / specs.

    Returns (results, reasons_pending) — see similar_to_property."""
    intent = customer_intent(customer)
    q = select(Property).where(
        Property.is_active == True,
        # buying and renting are different searches; a deposit is not a price
        or_(Property.listing_type == intent["listing_type"], Property.listing_type.is_(None)),
    )
    # the customer's own city wins; the parameter is a per-search override
    city = city or intent.get("city")
    if city:
        q = q.where(Property.city_name == city)
    # newest first, so the pool is the freshest rows rather than an arbitrary cut
    cands = (await db.execute(
        q.order_by(Property.id.desc()).limit(CANDIDATE_POOL * 2))).scalars().all()

    # The customer's own words → the listings that read closest, on top of
    # the pool; still gated below, so nothing enters that the exact filters
    # would have refused. Gated on use_llm so the engine's offline pass and
    # «use_llm=false» stay free. The gateway masks the text; the name is
    # never sent.
    sem: Dict[int, float] = {}
    if use_llm:
        need = " ".join(filter(None, (customer.desired_specs, customer.desired_district,
                                      getattr(customer, "notes", None)))).strip()
        if need:
            sem = await _cached_semantic_candidates(need, city, intent["listing_type"])
            have = {c.id for c in cands}
            missing = [pid for pid in sem if pid not in have]
            if missing:
                cands = list(cands) + (await db.execute(
                    select(Property).where(Property.id.in_(missing), Property.is_active == True)   # noqa: E712
                )).scalars().all()

    scored = []
    for c in cands:
        if not customer_wants(customer, c, intent):
            continue
        s = score_for_customer(customer, c)
        if c.id in sem:
            s["reasons"].append("شباهت متن")
        if s["score"] > 0:
            scored.append((s["score"], s["reasons"], c))
    scored.sort(key=lambda t: t[0], reverse=True)
    top = scored[:limit]
    results = [_brief(c, sc, rs) for sc, rs, c in top]

    pending = False
    if use_llm and results:
        # the customer's own words go to a third party masked — their name is
        # never sent at all, only what they are looking for
        from app.services import llm as _llm
        ctx = (f"مشتری با بودجه {customer.budget_max or '?'} تومان، منطقه درخواستی "
               f"{_llm.mask_pii(customer.desired_district) or '-'}، مشخصات {_llm.mask_pii(customer.desired_specs) or '-'}"
               + (f"، نمی‌خواهد: {_llm.mask_pii(customer.red_lines)}" if customer.red_lines else ""))
        pending = await _attach_reasons("customer", customer.id, results, ctx)
    return results, pending


async def customers_for_property(db: AsyncSession, prop: Property, limit: int = 12,
                                 use_llm: bool = True) -> List[Dict[str, Any]]:
    """The other direction: which of our customers were looking for this?

    A new file arrives and the question is who to ring, not what to show —
    so this scores the file against every customer's criteria and returns
    the people, ranked.
    """
    from app.models.crm_models import Customer

    customers = (await db.execute(
        select(Customer).order_by(Customer.id.desc()).limit(CANDIDATE_POOL))).scalars().all()

    scored = []
    for c in customers:
        if not customer_wants(c, prop):
            continue
        s = score_for_customer(c, prop)
        if s["score"] > 0:
            scored.append((s["score"], s["reasons"], c))
    scored.sort(key=lambda t: t[0], reverse=True)

    results = []
    for score, reasons, c in scored[:limit]:
        results.append({
            "id": c.id, "full_name": c.full_name,
            "mobile1": c.mobile1, "mobile2": c.mobile2,
            "temperature": c.temperature, "consultant_name": c.consultant_name,
            "budget_max": c.budget_max,
            "desired_district": c.desired_district,
            "desired_specs": c.desired_specs,
            "score": score, "reasons": reasons,
        })
    return results
