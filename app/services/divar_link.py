"""
Read a Divar search link back into the scraper's own filter form.

Asked for as: «یه سکشن اضافه کن که فیلتر رو تو دیوار بزنم، لینکشو کپی کنم و
بدم به اسکرپر». Which is a better way to drive it than the form for anything
Divar can express and we cannot type quickly — someone narrowing a search by
hand on Divar has already done the work.

The mapping is short because the vocabulary already lines up: our city keys
ARE Divar's city slugs, and our category keys ARE the slugs in its URL path.
Only the query string needs translating, and build_search_query() in
divar_count writes exactly the same six parameters in the other direction —
the two are inverses and should move together.

What it deliberately does NOT do is guess. A filter Divar can express and the
scraper cannot — a polygon drawn on the map, a district list — is reported as
carried-over-nothing rather than silently dropped, because a scrape that
quietly ignores half of what you drew is worse than one that says so.
"""
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

from app.config import CATEGORIES, CITIES

# Divar's query names → ours. The other direction lives in
# divar_count.build_search_query(); if one moves, move both.
_RANGES = {
    "price":  ("min_price", "max_price"),
    "credit": ("min_deposit", "max_deposit"),      # ودیعه
    "rent":   ("min_rent", "max_rent"),
    "size":   ("min_area", "max_area"),            # متراژ
    "rooms":  ("min_rooms", "max_rooms"),
}

_BUSINESS = {"personal": "personal", "real-estate-business": "agency"}

# Named so the panel can say what was left behind, in the user's words.
_IGNORED_FA = {
    "bbox": "محدودهٔ نقشه",
    "map_bbox": "محدودهٔ نقشه",
    "districts": "منطقه/محله",
    "neighborhoods": "منطقه/محله",
    "geo_radius": "شعاع روی نقشه",
    "sort": "ترتیب نمایش",
    "q": "عبارت جست‌وجو",
    "user_type": "نوع کاربر",
}

# Query keys that carry no filtering at all — dropping them silently is right.
_NOISE = {"page", "cityIds", "city_ids", "utm_source", "utm_medium",
          "utm_campaign", "share", "ref"}


def _range(raw: str) -> Tuple[Optional[int], Optional[int]]:
    """«700000000-900000000», «-900000000» and «700000000-» all occur."""
    lo, _, hi = (raw or "").partition("-")
    def num(v):
        v = (v or "").strip()
        return int(v) if v.isdigit() else None
    return num(lo), num(hi)


def parse_search_url(url: str) -> Dict[str, Any]:
    """Translate a Divar search link into what the scrape form holds.

    Always returns a dict. `error` is a Persian sentence when the link cannot
    be used at all; otherwise it is None and the rest is filled in.
    """
    out: Dict[str, Any] = {
        "city": None, "city_name": None,
        "category": None, "category_name": None,
        "filters": {}, "ignored": [], "error": None,
    }
    raw = (url or "").strip()
    if not raw:
        out["error"] = "لینکی وارد نشده است"
        return out
    if "://" not in raw:
        raw = "https://" + raw.lstrip("/")

    try:
        parsed = urlparse(raw)
    except Exception:
        out["error"] = "این لینک خوانده نشد"
        return out

    host = (parsed.netloc or "").lower().removeprefix("www.")
    if not host.endswith("divar.ir"):
        out["error"] = "این لینک از دیوار نیست"
        return out

    segments = [unquote(s) for s in (parsed.path or "").split("/") if s]
    if segments and segments[0] == "v":
        out["error"] = ("این لینک یک آگهی است، نه یک جست‌وجو — "
                        "برای آن از «اسکرپ تکی» استفاده کنید")
        return out
    if not segments or segments[0] != "s":
        out["error"] = ("این لینک صفحهٔ جست‌وجوی دیوار نیست — "
                        "در دیوار فیلترها را بزنید و آدرس همان صفحه را کپی کنید")
        return out

    # /s/<city>[/<category>[/…]]
    city = segments[1] if len(segments) > 1 else None
    if not city or city not in CITIES:
        out["error"] = (f"شهر «{city}» شناخته نشد" if city
                        else "شهری در این لینک نیست")
        return out
    out["city"] = city
    out["city_name"] = CITIES[city].get("name", city)

    if len(segments) > 2:
        category = segments[2]
        if category in CATEGORIES:
            out["category"] = category
            out["category_name"] = CATEGORIES[category].get("name", category)
        else:
            # A category we do not scrape — say so rather than silently
            # scraping the whole city.
            out["error"] = (f"دسته‌بندی «{category}» در سامانه نیست — "
                            "یکی از دسته‌بندی‌های املاک را در دیوار انتخاب کنید")
            return out
    if len(segments) > 3:
        out["ignored"].append(_IGNORED_FA["districts"])

    query = parse_qs(parsed.query or "", keep_blank_values=False)
    filters: Dict[str, Any] = {}
    ignored: List[str] = list(out["ignored"])

    for key, values in query.items():
        value = (values[-1] or "").strip()
        if not value or key in _NOISE:
            continue
        if key in _RANGES:
            lo_name, hi_name = _RANGES[key]
            lo, hi = _range(value)
            if lo is not None:
                filters[lo_name] = lo
            if hi is not None:
                filters[hi_name] = hi
            if lo is None and hi is None:
                ignored.append(key)
        elif key == "business-type":
            kind = _BUSINESS.get(value)
            if kind:
                filters["advertiser_type"] = kind
            else:
                ignored.append(_IGNORED_FA.get("user_type", key))
        elif key == "has-photo":
            if value.lower() in ("true", "1", "yes"):
                filters["has_images"] = True
        else:
            ignored.append(_IGNORED_FA.get(key, key))

    out["filters"] = filters
    # Stable order, no repeats — this is read by a person.
    out["ignored"] = sorted(set(ignored))
    return out
