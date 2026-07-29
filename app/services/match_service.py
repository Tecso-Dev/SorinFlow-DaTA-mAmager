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
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.property import Property

settings = get_settings()

# ── tunables ────────────────────────────────────────────────────────────
PRICE_TOLERANCE = 0.30   # ±30% is still "similar"
AREA_TOLERANCE = 0.35    # ±35%
CANDIDATE_POOL = 300     # rows scored before trimming to the top N


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

    tp, cp = _price_of(target), _price_of(cand)
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
    tf, cf = property_family(target), property_family(cand)
    if tf and cf:
        if tf == cf:
            parts.append((10, 1.0, "همان نوع ملک"))
        else:
            parts.append((10, 0.0, ""))
            family_penalty = 0.2

    # amenities the target has, that the candidate also has
    amen = [("has_elevator", "آسانسور"), ("has_parking", "پارکینگ"),
            ("has_storage", "انباری"), ("has_balcony", "بالکن")]
    wanted = [(f, fa) for f, fa in amen if getattr(target, f, False)]
    if wanted:
        have = [fa for f, fa in wanted if getattr(cand, f, False)]
        parts.append((5, len(have) / len(wanted), "امکانات: " + "، ".join(have) if have else "بدون امکانات مشترک"))

    total_w = sum(w for w, _v, _r in parts) or 1
    score = sum(w * v for w, v, _r in parts) / total_w * 100 * price_penalty * family_penalty
    reasons = [r for w, v, r in parts if v > 0.5]
    if price_penalty < 0.9:
        reasons.append("خارج از محدوده قیمت")
    if family_penalty < 1:
        reasons.append("نوع ملک متفاوت است")
    return {"score": round(score), "reasons": reasons}


def customer_intent(customer) -> Dict[str, Any]:
    """What the customer is actually shopping for.

    The intake form has no «نوع ملک» or «خرید/اجاره» field, so this is read out
    of the free-text BANT answers. red_lines is deliberately excluded — it
    lists what the customer does NOT want, and reading a type out of it would
    invert the filter.
    """
    import re as _re
    blob = " ".join(filter(None, [customer.desired_specs, getattr(customer, "notes", None)]))
    rent = any(w in blob for w in ("رهن", "اجاره", "ودیعه"))
    return {
        "family": _family(blob),
        "listing_type": "rent" if rent else "buy",
        # «۲ خواب» only makes sense for somewhere to live
        "wants_rooms": bool(_re.search(r"خواب", blob)),
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
    fam = property_family(cand)
    if intent["family"] and fam and fam != intent["family"]:
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

    loc = _text_overlap(customer.desired_district, cand.district) \
        or _text_overlap(customer.desired_district, cand.neighborhood) \
        or _text_overlap(customer.desired_district, cand.address)
    if loc is not None:
        parts.append((30, loc, ""))
        if loc > 0.3:
            reasons.append("منطقه درخواستی")

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
    score = sum(w * v for w, v, _r in parts) / total_w * 100 * budget_penalty * coverage

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
    local ranking untouched.
    """
    key = getattr(settings, "llm_api_key", "") or ""
    if not key or not prompt_items:
        return {}

    base = getattr(settings, "llm_base_url", "https://api.openai.com/v1")
    model = getattr(settings, "llm_model", "gpt-4o-mini")
    listing_lines = "\n".join(
        f"- id={i['id']} | {i['title']} | {i['area'] or '?'}m² | {i['rooms'] if i['rooms'] is not None else '?'}خواب"
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
        async with httpx.AsyncClient(timeout=25) as client:
            resp = await client.post(
                f"{base.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2,
                    "response_format": {"type": "json_object"},
                },
            )
        if resp.status_code != 200:
            logger.warning(f"[match] LLM returned {resp.status_code}: {resp.text[:160]}")
            return {}
        import json as _json
        content = resp.json()["choices"][0]["message"]["content"]
        data = _json.loads(content)
        return {int(r["id"]): str(r.get("reason", ""))[:120] for r in data.get("results", []) if r.get("id")}
    except Exception as e:
        logger.warning(f"[match] LLM re-rank skipped: {e}")
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
        "thumbnail_url": p.thumbnail_url,
        "url": p.url,
        "phone_number": p.phone_number,
        "score": score,
        "reasons": reasons,
    }


async def similar_to_property(db: AsyncSession, prop: Property, limit: int = 12,
                              use_llm: bool = True) -> List[Dict[str, Any]]:
    """Listings most like `prop` (same city & listing type, ranked by score)."""
    q = select(Property).where(
        Property.is_active == True,
        Property.id != prop.id,
    )
    if prop.city_name:
        q = q.where(Property.city_name == prop.city_name)
    if prop.listing_type:
        q = q.where(Property.listing_type == prop.listing_type)

    cands = (await db.execute(q.limit(CANDIDATE_POOL))).scalars().all()
    # «مشابه این ملک» means the same kind of thing, so a different family is
    # dropped rather than ranked low. Unreadable ones are kept — better a
    # slightly noisy list than silently hiding real matches.
    target_family = property_family(prop)
    scored = []
    for c in cands:
        if target_family and property_family(c) not in (None, target_family):
            continue
        s = score_similarity(prop, c)
        if s["score"] > 0:
            scored.append((s["score"], s["reasons"], c))
    scored.sort(key=lambda t: t[0], reverse=True)
    top = scored[:limit]
    results = [_brief(c, sc, rs) for sc, rs, c in top]

    if use_llm and results:
        ctx = (f"ملکی مشابه این: {prop.title} — {prop.area or '?'} متر، "
               f"{prop.rooms if prop.rooms is not None else '?'} خواب، "
               f"{_price_of(prop) or '?'} تومان، منطقه {prop.district or prop.city_name or '-'}")
        reasons = await _llm_rerank(
            [{"id": r["id"], "title": r["title"], "area": r["area"], "rooms": r["rooms"],
              "price": r["price"], "district": r["district"], "city": r["city_name"],
              "score": r["score"]} for r in results],
            ctx,
        )
        for r in results:
            if r["id"] in reasons and reasons[r["id"]]:
                r["ai_reason"] = reasons[r["id"]]
    return results


async def matches_for_customer(db: AsyncSession, customer, limit: int = 12,
                               use_llm: bool = True, city: Optional[str] = None
                               ) -> List[Dict[str, Any]]:
    """Listings that fit a customer's budget / district / specs."""
    intent = customer_intent(customer)
    q = select(Property).where(
        Property.is_active == True,
        # buying and renting are different searches; a deposit is not a price
        or_(Property.listing_type == intent["listing_type"], Property.listing_type.is_(None)),
    )
    if city:
        q = q.where(Property.city_name == city)
    # newest first, so the pool is the freshest rows rather than an arbitrary cut
    cands = (await db.execute(
        q.order_by(Property.id.desc()).limit(CANDIDATE_POOL * 2))).scalars().all()

    scored = []
    for c in cands:
        if not customer_wants(customer, c, intent):
            continue
        s = score_for_customer(customer, c)
        if s["score"] > 0:
            scored.append((s["score"], s["reasons"], c))
    scored.sort(key=lambda t: t[0], reverse=True)
    top = scored[:limit]
    results = [_brief(c, sc, rs) for sc, rs, c in top]

    if use_llm and results:
        ctx = (f"مشتری با بودجه {customer.budget_max or '?'} تومان، منطقه درخواستی "
               f"{customer.desired_district or '-'}، مشخصات {customer.desired_specs or '-'}"
               + (f"، نمی‌خواهد: {customer.red_lines}" if customer.red_lines else ""))
        reasons = await _llm_rerank(
            [{"id": r["id"], "title": r["title"], "area": r["area"], "rooms": r["rooms"],
              "price": r["price"], "district": r["district"], "city": r["city_name"],
              "score": r["score"]} for r in results],
            ctx,
        )
        for r in results:
            if r["id"] in reasons and reasons[r["id"]]:
                r["ai_reason"] = reasons[r["id"]]
    return results
