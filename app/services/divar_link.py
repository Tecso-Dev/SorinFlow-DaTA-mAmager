"""
Read a Divar search link back into the scraper's own filter form.

Asked for as: «یه سکشن اضافه کن که فیلتر رو تو دیوار بزنم، لینکشو کپی کنم و
بدم به اسکرپر». Which is a better way to drive it than the form for anything
Divar can express and we cannot type quickly — someone narrowing a search by
hand on Divar has already done the work.

The mapping is short because the vocabulary already lines up: our city keys
ARE Divar's city slugs, and our category keys ARE the slugs in its URL path.
Only the query string needs translating, and it is read against the
category's own filters (app/services/divar_filters.py, #27): every key the
category has comes into the form — the form's own fields where it has one
(price, deposit, rooms, the amenity switches…), the rest as `divar_filters`
under Divar's own name. build_search_query() in divar_count writes the same
parameters in the other direction; the two are inverses.

What it deliberately does NOT do is guess. A filter Divar can express and the
scraper cannot — a polygon drawn on the map, a district list — is reported as
carried-over-nothing rather than silently dropped, because a scrape that
quietly ignores half of what you drew is worse than one that says so.
"""
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

from app.config import CATEGORIES, CITIES

# Divar's query names → the form's own fields. The other direction lives in
# divar_count.plan_filters(); if one moves, move both.
_RANGES = {
    "price":  ("min_price", "max_price"),
    "credit": ("min_deposit", "max_deposit"),      # ودیعه
    "rent":   ("min_rent", "max_rent"),
    "size":   ("min_area", "max_area"),            # متراژ
    "price_per_square": ("min_price_per_meter", "max_price_per_meter"),
    "rooms":  ("min_rooms", "max_rooms"),          # an older numeric «2-3»
}
_SWITCHES = {"has-photo": "has_images", "elevator": "has_elevator",
             "parking": "has_parking", "warehouse": "has_storage",
             "balcony": "has_balcony"}
_ROOMS = {"بدون اتاق": 0, "یک": 1, "دو": 2, "سه": 3, "چهار": 4, "بیشتر": 5}

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
    extra: Dict[str, Any] = {}
    ignored: List[str] = list(out["ignored"])

    from app.services import divar_filters as df
    schema = df.snapshot()
    known = df.definitions(schema)
    # A link with no category: whatever any category has is taken, and the
    # run's plan drops what the category picked in the form does not have.
    own = df.filters_for(out["category"], schema) if out["category"] else known
    cat_fa = out["category_name"] or ""

    for key, values in query.items():
        # Divar writes list-valued filters comma-separated WITH a trailing
        # comma: «business-type=personal,». Read literally, «personal,» is
        # not «personal», and the one filter that most changes the count —
        # 702 ads for everyone against 185 for personal, on the same search —
        # silently fell off the link. Split and drop the empties.
        parts = [p.strip() for p in (values[-1] or "").split(",") if p.strip()]
        value = parts[0] if parts else ""
        if not value or key in _NOISE:
            continue
        if key in _IGNORED_FA:
            ignored.append(_IGNORED_FA[key])
            continue
        f = own.get(key)
        if f is None:
            # A filter Divar has, but not for this category: said in the
            # person's words, never carried into a run it would break (#27).
            if key in known:
                ignored.append(f"{df.title_of(key, schema)} (دستهٔ «{cat_fa}» این فیلتر را ندارد)")
            else:
                ignored.append(key)
            continue
        ftype = f.get("type")
        if key == "rooms" and not value[:1].isdigit() and not value.startswith("-"):
            rooms = sorted(_ROOMS[p] for p in parts if p in _ROOMS)
            if not rooms:
                ignored.append(df.title_of(key, schema))
                continue
            filters["min_rooms"] = rooms[0]
            if rooms[-1] < 5:
                filters["max_rooms"] = rooms[-1]
            if rooms != list(range(rooms[0], rooms[-1] + 1)):
                ignored.append("تعداد اتاق به بازهٔ پیوسته تبدیل شد")
        elif key in _RANGES:
            lo_name, hi_name = _RANGES[key]
            lo, hi = _range(value)
            if lo is not None:
                filters[lo_name] = lo
            if hi is not None:
                filters[hi_name] = hi
            if lo is None and hi is None:
                ignored.append(key)
        elif key == "business-type":
            kinds = {_BUSINESS[p] for p in parts if p in _BUSINESS}
            if len(kinds) == 1:
                filters["advertiser_type"] = kinds.pop()
            elif not kinds:
                ignored.append(_IGNORED_FA.get("user_type", key))
        elif key in _SWITCHES:
            if value.lower() in ("true", "1", "yes"):
                filters[_SWITCHES[key]] = True
        elif ftype == "number_range":
            lo, hi = _range(value)
            if lo is None and hi is None:
                ignored.append(df.title_of(key, schema))
            else:
                extra[key] = {k: v for k, v in (("min", lo), ("max", hi)) if v is not None}
        elif ftype == "boolean":
            if value.lower() in ("true", "1", "yes"):
                extra[key] = True
        elif ftype == "repeated_string":
            allowed = df.option_values(f) if df.options_known(f) else None
            chosen = [p for p in parts if allowed is None or p in allowed]
            if chosen:
                extra[key] = chosen
            if len(chosen) < len(parts):
                ignored.append(f"بعضی گزینه‌های «{df.title_of(key, schema)}»")
        elif ftype == "str":
            if not df.options_known(f) or value in df.option_values(f):
                extra[key] = value
            else:
                ignored.append(df.title_of(key, schema))
        else:
            ignored.append(key)

    if extra:
        filters["divar_filters"] = extra
    out["filters"] = filters
    # Stable order, no repeats — this is read by a person.
    out["ignored"] = sorted(set(ignored))
    return out
