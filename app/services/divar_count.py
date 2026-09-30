"""
«چند آگهی با این فیلترها هست؟» — ask Divar before scraping.

Divar's own search page answers this above the results («۳۴۳ آگهی در این
محدوده»), and its search API carries the same number at map_data.post_count.
Asking it costs one HTTP request, where finding out by scraping costs opening
every ad in the city.

Everything here except fetch_post_count() is pure and testable: the slug and
filter mapping is the part that can silently go wrong.
"""
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import httpx
from loguru import logger

SEARCH_URL = "https://api.divar.ir/v8/postlist/w/search"
CITIES_URL = "https://api.divar.ir/v8/places/cities"
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

# Our category slugs are not Divar's internal tokens — «rent-residential» is
# «residential-rent» there, and «buy-store» is «shop-sell». Each of these was
# read off Divar itself, either from the category page's embedded search form
# or by confirming the token returns a count.
CATEGORY_TOKENS: Dict[str, str] = {
    "buy-residential":                       "residential-sell",
    "buy-apartment":                         "apartment-sell",
    "buy-villa":                             "house-villa-sell",
    "buy-old-house":                         "plot-old",
    "rent-residential":                      "residential-rent",
    "rent-apartment":                        "apartment-rent",
    "rent-villa":                            "house-villa-rent",
    "buy-commercial-property":               "commercial-sell",
    "buy-office":                            "office-sell",
    "buy-store":                             "shop-sell",
    "buy-industrial-agricultural-property":  "industry-agriculture-business-sell",
    "rent-commercial-property":              "commercial-rent",
    "rent-office":                           "office-rent",
    "rent-store":                            "shop-rent",
    "rent-industrial-agricultural-property": "industry-agriculture-business-rent",
    "rent-temporary":                        "temporary-rent",
    "real-estate-services":                  "real-estate-services",
    # Categories the scraper does not offer yet, read off Divar with the rest
    # of its filter forms (#27); the schema covers them already.
    "real-estate":                           "real-estate",
    "contribution-construction":             "partnership",
    "pre-sell-home":                         "presell",
    "rent-temporary-suite-apartment":        "suite-apartment",
    "rent-temporary-villa":                  "villa",
    "rent-temporary-workspace":              "workspace",
}

# Divar's own words for the two kinds of advertiser.
BUSINESS_TYPES = {"personal": "personal", "agency": "real-estate-business"}

# slug/name → Divar city id, filled once from CITIES_URL
_city_cache: Dict[str, int] = {}


def _range(minimum: Optional[int], maximum: Optional[int]) -> Optional[dict]:
    """Divar takes numeric bands as strings inside a number_range."""
    band = {}
    if minimum is not None:
        band["minimum"] = str(int(minimum))
    if maximum is not None:
        band["maximum"] = str(int(maximum))
    return {"number_range": band} if band else None


# ── the category's own filters (#27) ────────────────────────────────────────
#
# Divar's form is different for every category, and a filter the category does
# not have makes it refuse the search from the second page on. So what is sent
# is decided against the category's schema (app/services/divar_filters.py):
# every filter the person set that the category has goes out, in Divar's shape;
# one it does not have never does, and the plan says so in Persian for the run
# log and the estimate.
#
# The form's own fields (min_price, has_elevator, …) predate the schema and are
# still what the panel, saved runs and schedules send. Each maps onto one of
# Divar's keys. Everything else Divar has arrives as `divar_filters`, keyed by
# Divar's own names: {"building-age": {"max": 5}, "deed_type": ["single_page"]}.

# Divar key, the form's two fields, the form's name for it.
_RANGE_FIELDS = (
    ("price", "min_price", "max_price", "قیمت کل"),
    ("price_per_square", "min_price_per_meter", "max_price_per_meter", "قیمت هر متر"),
    ("credit", "min_deposit", "max_deposit", "ودیعه"),
    ("rent", "min_rent", "max_rent", "اجارهٔ ماهانه"),
    ("size", "min_area", "max_area", "متراژ"),
)
# Divar key, the form's switch, its name. Only «must have» is Divar's: a
# switch set to False («without a lift») has no Divar filter and stays local.
_SWITCH_FIELDS = (
    ("has-photo", "has_images", "عکس‌دار"),
    ("elevator", "has_elevator", "آسانسور"),
    ("parking", "has_parking", "پارکینگ"),
    ("warehouse", "has_storage", "انباری"),
    ("balcony", "has_balcony", "بالکن"),
)
_FORM_KEYS = ({k for k, *_ in _RANGE_FIELDS} | {k for k, *_ in _SWITCH_FIELDS}
              | {"rooms", "business-type"})

# The price filters mean something in one family only. Asked for elsewhere —
# a deposit on a sale — they are not a narrower search but a wrong one.
_FAMILY_OF = {"price": "buy", "price_per_square": "buy", "credit": "rent", "rent": "rent",
              "daily_rent": "temporary", "person_capacity": "temporary"}
# Which listing types the scraper's own check applies a form field to after
# opening an ad (DivarScraper.local_filter_skip); None is every type.
_LOCAL_FOR = {"price": ("buy",), "price_per_square": ("buy",),
              "credit": ("rent",), "rent": ("rent",)}

# Divar's room options, and the count each stands for; «بیشتر» is five and up.
ROOM_OPTIONS = (("بدون اتاق", 0), ("یک", 1), ("دو", 2), ("سه", 3), ("چهار", 4), ("بیشتر", 5))

# recent_ads: each option and the hours it reaches back.
RECENT_ADS = (("3h", 3), ("12h", 12), ("1d", 24), ("3d", 72), ("7d", 168))

_MAX_VALUES = 12            # choices in one repeated filter
_MAX_TEXT = 60              # characters in one choice


def tehran_today(now: Optional[datetime] = None) -> date:
    """Today's date in Tehran (a fixed +03:30, like everything else here)."""
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(TEHRAN).date()


def recent_ads_for(posted_date: Optional[str] = None, max_age_hours: Optional[int] = None,
                   *, today: Optional[date] = None) -> Optional[str]:
    """The smallest recent_ads that safely covers what the run asks for, or None.

    recent_ads is Divar's sort time — the last bump — not the publish time, so
    it only pre-filters: the scraper still checks the exact day. A post
    published on a day was bumped on it or later, so a window reaching back
    over that whole Tehran day, with a margin, never hides one: today → 1d,
    yesterday or the day before → 3d, three to six days ago → 7d, older →
    nothing. A maximum age takes the smallest window at least that long.
    """
    if posted_date:
        try:
            day = datetime.fromisoformat(str(posted_date).strip()).date()
        except ValueError:
            return None
        ago = ((today or tehran_today()) - day).days
        if ago <= 0:
            return "1d"
        if ago <= 2:
            return "3d"
        if ago <= 6:
            return "7d"
        return None
    if max_age_hours and max_age_hours > 0:
        for value, hours in RECENT_ADS:
            if hours >= max_age_hours:
                return value
    return None


def rooms_options(lo: Optional[int], hi: Optional[int]) -> Tuple[List[str], bool]:
    """(Divar's room options for a min/max band, whether they are exact).

    Exact unless «بیشتر» (five and up) is in and the band stops short of it or
    starts above five: then Divar lets some through that the band does not,
    and the scraper keeps checking the rooms itself.
    """
    lo_n = 0 if lo is None else int(lo)
    out = [name for name, n in ROOM_OPTIONS
           if n >= lo_n and (hi is None or n <= int(hi)) and n < 5]
    more = hi is None or int(hi) >= 5
    if more:
        out.append("بیشتر")
    exact = not more or (hi is None and lo_n <= 5)
    return out, exact


def _category_name(slug: Optional[str]) -> str:
    from app.config import CATEGORIES
    return (CATEGORIES.get(slug or "") or {}).get("name") or (slug or "بدون دسته")


def _int_or_none(v: Any) -> Optional[int]:
    if v is None or v == "" or isinstance(v, bool):
        return None
    try:
        n = int(float(str(v).strip()))
    except (TypeError, ValueError):
        raise ValueError(v) from None
    if n < 0:
        raise ValueError(v)
    return n


def _as_range(raw: Any) -> Optional[Tuple[Optional[int], Optional[int]]]:
    """{"min": 3, "max": 5} (or Divar's minimum/maximum) → (3, 5)."""
    if not isinstance(raw, dict):
        raise ValueError(raw)
    lo = _int_or_none(raw.get("min", raw.get("minimum")))
    hi = _int_or_none(raw.get("max", raw.get("maximum")))
    return None if lo is None and hi is None else (lo, hi)


@dataclass
class FilterPlan:
    """What a run sends Divar for one category, and what it does not.

    form     — search_data.form_data.data for the search API, category included
    query    — the same filters for a divar.ir/s/<city>/<slug> link
    sent     — Divar key → the value sent (tuple, True, list or str)
    notes    — one Persian sentence per filter that was not sent, and why
    not_applied — names of filters that do nothing for this category
    after_scrape — names of filters the scraper checks itself after opening an ad
    local_off — the form's fields the scraper need not check again: Divar
                applied them exactly, or they mean nothing for this category
    """
    category: Optional[str]
    form: Dict[str, Any] = field(default_factory=dict)
    query: str = ""
    sent: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    not_applied: List[str] = field(default_factory=list)
    after_scrape: List[str] = field(default_factory=list)
    local_off: set = field(default_factory=set)
    recent_ads: Optional[str] = None


def _form_value(ftype: str, value: Any) -> Dict[str, Any]:
    if ftype == "number_range":
        return _range(*value) or {"number_range": {}}
    if ftype == "boolean":
        return {"boolean": {"value": True}}
    if ftype == "repeated_string":
        return {"repeated_string": {"value": list(value)}}
    return {"str": {"value": value}}


def _query_value(ftype: str, value: Any) -> str:
    if ftype == "number_range":
        lo, hi = value
        return f"{'' if lo is None else int(lo)}-{'' if hi is None else int(hi)}"
    if ftype == "boolean":
        return "true"
    if ftype == "repeated_string":
        return ",".join(value)
    return str(value)


def plan_filters(
    category: Optional[str] = None,
    *,
    schema: Optional[Dict[str, Any]] = None,
    today: Optional[date] = None,
    advertiser_type: Optional[str] = None,
    has_images: Optional[bool] = None,
    min_price: Optional[int] = None, max_price: Optional[int] = None,
    min_deposit: Optional[int] = None, max_deposit: Optional[int] = None,
    min_rent: Optional[int] = None, max_rent: Optional[int] = None,
    min_price_per_meter: Optional[int] = None, max_price_per_meter: Optional[int] = None,
    min_area: Optional[int] = None, max_area: Optional[int] = None,
    min_rooms: Optional[int] = None, max_rooms: Optional[int] = None,
    has_elevator: Optional[bool] = None, has_parking: Optional[bool] = None,
    has_storage: Optional[bool] = None, has_balcony: Optional[bool] = None,
    posted_date: Optional[str] = None, max_age_hours: Optional[int] = None,
    divar_filters: Optional[Dict[str, Any]] = None,
) -> FilterPlan:
    """Decide, against the category's schema, what goes to Divar."""
    from app.services import divar_filters as df

    fields = {
        "min_price": min_price, "max_price": max_price,
        "min_deposit": min_deposit, "max_deposit": max_deposit,
        "min_rent": min_rent, "max_rent": max_rent,
        "min_price_per_meter": min_price_per_meter, "max_price_per_meter": max_price_per_meter,
        "min_area": min_area, "max_area": max_area,
        "has_images": has_images, "has_elevator": has_elevator, "has_parking": has_parking,
        "has_storage": has_storage, "has_balcony": has_balcony,
    }
    slug = (category or "").strip()
    schema = schema or df.snapshot()
    entry = df.category(slug, schema)
    own = df.filters_for(slug, schema)
    family = (entry or {}).get("family")
    cat_fa = _category_name(slug)
    from app.config import CATEGORIES
    listing_type = (CATEGORIES.get(slug) or {}).get("type")

    plan = FilterPlan(category=slug or None)
    sent: Dict[str, Any] = {}

    def locally_checked(key: str) -> bool:
        types = _LOCAL_FOR.get(key)
        return types is None or listing_type in types

    def not_here(key: str, name: str, names: Tuple[str, ...]) -> None:
        """A filter the person set that this category does not have."""
        if _FAMILY_OF.get(key) and _FAMILY_OF[key] != family:
            plan.notes.append(f"{name} برای دستهٔ «{cat_fa}» معنا ندارد و اعمال نشد")
            plan.not_applied.append(name)
            plan.local_off.update(names)
        elif names and locally_checked(key):
            plan.notes.append(f"دیوار برای دستهٔ «{cat_fa}» فیلتر «{name}» ندارد؛ "
                              "اسکرپر بعد از باز کردن هر آگهی خودش آن را بررسی می‌کند")
            plan.after_scrape.append(name)
        else:
            plan.notes.append(f"دیوار برای دستهٔ «{cat_fa}» فیلتر «{name}» ندارد و اعمال نشد")
            plan.not_applied.append(name)
            plan.local_off.update(names)

    # The form's bands.
    for key, lo_name, hi_name, name in _RANGE_FIELDS:
        lo, hi = fields[lo_name], fields[hi_name]
        if lo is None and hi is None:
            continue
        if key in own:
            sent[key] = (lo, hi)
            plan.local_off.update((lo_name, hi_name))
        else:
            not_here(key, name, (lo_name, hi_name))

    # The form's switches: only «must have» is something Divar can say.
    for key, form_name, name in _SWITCH_FIELDS:
        if fields[form_name] is not True:
            continue
        if key in own:
            sent[key] = True
            plan.local_off.add(form_name)
        else:
            not_here(key, name, (form_name,))

    # Rooms: the form's band, as Divar's options.
    if min_rooms is not None or max_rooms is not None:
        if "rooms" in own:
            options, exact = rooms_options(min_rooms, max_rooms)
            if options:
                sent["rooms"] = options
                if exact:
                    plan.local_off.update(("min_rooms", "max_rooms"))
        else:
            not_here("rooms", "تعداد اتاق", ("min_rooms", "max_rooms"))

    kind = BUSINESS_TYPES.get((advertiser_type or "").strip())
    if kind:
        if "business-type" in own:
            sent["business-type"] = [kind]
            plan.local_off.add("advertiser_type")
        else:
            not_here("business-type", "نوع آگهی‌دهنده", ("advertiser_type",))

    # The rest of Divar's filters, by Divar's own names.
    for key, raw in (divar_filters or {}).items():
        key = str(key)[:_MAX_TEXT]
        if key == "recent_ads" or key == "category" or raw in (None, "", [], {}, False):
            continue
        if key in _FORM_KEYS and key in sent:
            continue                      # the form's own field already said it
        f = own.get(key)
        title = df.title_of(key, schema)
        if f is None:
            not_here(key, title, ())
            continue
        ftype = f.get("type")
        try:
            if ftype == "number_range":
                value: Any = _as_range(raw)
                if value is None:
                    continue
            elif ftype == "boolean":
                if raw is not True and str(raw).lower() not in ("true", "1"):
                    continue
                value = True
            elif ftype == "repeated_string":
                wanted = raw if isinstance(raw, list) else [raw]
                wanted = [str(v).strip()[:_MAX_TEXT] for v in wanted[:_MAX_VALUES] if str(v).strip()]
                allowed = df.option_values(f) if df.options_known(f) else None
                value = [v for v in wanted if allowed is None or v in allowed]
                if len(value) < len(wanted):
                    plan.notes.append(f"بعضی گزینه‌های «{title}» در دیوار نیست و فرستاده نشد")
                if not value:
                    continue
            elif ftype == "str":
                value = str(raw).strip()[:_MAX_TEXT]
                if df.options_known(f) and value not in df.option_values(f):
                    raise ValueError(value)
            else:
                continue
        except (ValueError, TypeError):
            plan.notes.append(f"مقدار «{title}» پذیرفته نشد و اعمال نشد")
            continue
        sent[key] = value

    # The publish date or the maximum age, as Divar's recent_ads — and a
    # recent_ads picked by hand only when neither is set: a «3 hours» on a run
    # for yesterday would hide the whole day.
    recent = recent_ads_for(posted_date, max_age_hours, today=today)
    if recent is None and not posted_date and not max_age_hours:
        hand = (divar_filters or {}).get("recent_ads")
        if isinstance(hand, str) and hand in {v for v, _ in RECENT_ADS}:
            recent = hand
    if recent and "recent_ads" in own:
        sent["recent_ads"] = recent
        plan.recent_ads = recent

    token = CATEGORY_TOKENS.get(slug) or (entry or {}).get("token")
    if token:
        plan.form["category"] = {"str": {"value": token}}
    parts = []
    for key in sorted(sent):
        ftype = (own.get(key) or {}).get("type") or "str"
        plan.form[key] = _form_value(ftype, sent[key])
        parts.append(f"{key}={quote(_query_value(ftype, sent[key]), safe='-,')}")
    plan.query = "&".join(parts)
    plan.sent = sent
    return plan


def build_form_data(category: Optional[str] = None, **filters) -> Dict[str, Any]:
    """The filters Divar applies itself, in the shape its search API expects:
    every one the category has, and nothing it does not (see plan_filters)."""
    return plan_filters(category, **filters).form


def build_search_query(category: Optional[str] = None, **filters) -> str:
    """The same filters as build_form_data, in the shape a divar.ir URL wants.

    The scraper loaded «/s/{city}/{category}» with no filters at all and then
    threw away whatever did not match. On one real run that meant collecting
    204 listings to keep 14, with 131 dropped on deposit alone — and the 201
    that DID match were never looked at, because they were further down a feed
    the run had already stopped reading.

    It needs the category now: a filter the category does not have is what
    makes Divar refuse the search (#27). Ranges are «min-max», with either
    side allowed to be empty; several choices are comma-separated.
    """
    return plan_filters(category, **filters).query


def unsupported_filters(category: Optional[str] = None, **kwargs) -> list:
    """Filters the caller asked for that Divar does not narrow on in this
    category — the scraper checks them itself, so the estimate is at most."""
    return plan_filters(category, **kwargs).after_scrape


async def resolve_city_id(city: str, client: Optional[httpx.AsyncClient] = None) -> Optional[int]:
    """Our city slug → Divar's numeric id, by slug first and then by name."""
    if not city:
        return None
    if not _city_cache:
        await _load_cities(client)
    return _city_cache.get(city) or _city_cache.get(city.strip())


async def _load_cities(client: Optional[httpx.AsyncClient] = None) -> None:
    own = client is None
    client = client or httpx.AsyncClient(timeout=20.0)
    try:
        resp = await client.get(CITIES_URL, headers={"User-Agent": _UA})
        if resp.status_code != 200:
            logger.warning(f"[count] cities lookup returned {resp.status_code}")
            return
        payload = resp.json()
        rows = payload.get("cities") or []
        rows = rows if isinstance(rows, list) else list(rows.values())
        for row in rows:
            if not isinstance(row, dict) or not row.get("id"):
                continue
            for key in (row.get("slug"), row.get("second_slug"), row.get("name")):
                if key and key not in _city_cache:
                    _city_cache[key] = row["id"]
        logger.info(f"[count] cached {len(_city_cache)} Divar city keys")
    except Exception as e:
        logger.warning(f"[count] cities lookup failed: {e}")
    finally:
        if own:
            await client.aclose()


async def fetch_post_count(city: str, form_data: Dict[str, Any]) -> Tuple[Optional[int], Optional[str]]:
    """(count, error). Divar's own total for these filters."""
    async with httpx.AsyncClient(timeout=25.0) as client:
        city_id = await resolve_city_id(city, client)
        if not city_id:
            return None, f"شهر «{city}» در دیوار پیدا نشد"
        body = {
            "city_ids": [str(city_id)],
            "search_data": {"form_data": {"data": form_data}},
        }
        try:
            resp = await client.post(
                SEARCH_URL, json=body,
                headers={"User-Agent": _UA, "Content-Type": "application/json",
                         "Accept": "application/json"},
            )
        except Exception as e:
            return None, f"دیوار پاسخ نداد: {e}"
        if resp.status_code != 200:
            return None, f"دیوار خطا داد ({resp.status_code})"
        try:
            payload = resp.json()
        except Exception:
            return None, "پاسخ دیوار قابل خواندن نبود"
        count = (payload.get("map_data") or {}).get("post_count")
        if count is None:
            return None, "دیوار تعداد را برنگرداند"
        return int(count), None


# ── the listings themselves, over the same request ─────────────────────────
#
# The same POST that answers «how many» returns the first 24 listings and a
# cursor for the next 24. Paging it is one request per 24 ads over plain
# HTTP — no browser, no session, no scrolling — because the search page is
# public and this is exactly what Divar's own frontend sends.
#
# It is the reason the scraper no longer spends minutes scrolling a listing
# page before it opens its first ad. That phase — a real Chromium walking the
# feed with a five-second sleep per batch, then six empty cycles to confirm
# the end — was the whole of «the wait before the first listing»: two and a
# half minutes on the run that prompted this, ten on another. The same pool
# is here in about two seconds.

_PAGE_PAUSE = 0.35     # between pages: a person does not page faster than this
_MAX_PAGES = 80        # 24 a page; more than this is not a run, it is a mirror


def _row_from_widget(w: dict) -> Optional[dict]:
    """One POST_ROW widget → the listing shape the scraper walks."""
    if not isinstance(w, dict) or w.get("widget_type") != "POST_ROW":
        return None
    d = w.get("data") or {}
    token = d.get("token") or ((d.get("action") or {}).get("payload") or {}).get("token")
    if not token or not isinstance(token, str):
        return None
    # Everything Divar put on the card, kept: the title feeds the category
    # check, and the description lines carry the deposit/rent at no cost.
    descs = [d.get(k) for k in ("top_description_text", "middle_description_text",
                                "bottom_description_text") if d.get(k)]
    return {
        "divar_id": token,
        "url": f"https://divar.ir/v/{token}",
        "title": (d.get("title") or "")[:200] or None,
        "descriptions": descs,
    }


# The day a person picks in the panel is a Tehran day. A fixed offset, not
# ZoneInfo: the container has no tz database (and Iran has kept +03:30 all
# year since 2022).
TEHRAN = timezone(timedelta(hours=3, minutes=30), "Asia/Tehran")

_ISO_MOMENT = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?"
    r"(Z|[+-]\d{2}:?\d{2})?$")


def cursor_moment(raw: Any) -> Optional[datetime]:
    """Divar's last_post_date cursor as a moment in Tehran time, or None.

    The cursor comes in two shapes: RFC 3339 text from the search API this
    module talks to (UTC, «Z», with anything from no fraction to
    nanoseconds), and an epoch number from the older shapes the browser path
    still reads (seconds, milliseconds, microseconds or nanoseconds).

    Parsed by hand rather than with datetime.fromisoformat: on the image's
    Python 3.10 that refuses a nanosecond fraction outright, and a cursor
    that does not parse is a date run that never stops on its date.
    """
    if raw is None or isinstance(raw, bool) or raw == "":
        return None
    text = str(raw).strip()
    if isinstance(raw, (int, float)) or re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        value = float(text)
        for div in (1, 1e3, 1e6, 1e9):
            ts = value / div
            if 1e9 <= ts < 4e9:        # a plausible epoch in seconds: 2001..2096
                return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(TEHRAN)
        return None
    m = _ISO_MOMENT.match(text)
    if not m:
        return None
    y, mo, d, h, mi, sec, frac, zone = m.groups()
    try:
        moment = datetime(int(y), int(mo), int(d), int(h), int(mi), int(sec),
                          int((frac or "0")[:6].ljust(6, "0")), tzinfo=timezone.utc)
    except ValueError:
        return None
    if zone and zone != "Z":           # an explicit offset: undo it to reach UTC
        sign = 1 if zone[0] == "+" else -1
        moment -= sign * timedelta(hours=int(zone[1:3]), minutes=int(zone[-2:]))
    return moment.astimezone(TEHRAN)


def _cursor_day(pagination: Optional[dict]) -> Optional[date]:
    """The Tehran date of the last post on this page, from the cursor, or None.

    It was the UTC date. Divar's stamps are UTC, and a Tehran day starts at
    20:30 UTC the evening before — so a date run for the 14th read a cursor
    at 01:30 on the 14th as «the 13th», decided the feed had moved past its
    day, and never saw anything posted between midnight and 03:30.
    """
    raw = ((pagination or {}).get("data") or {}).get("last_post_date")
    moment = cursor_moment(raw)
    return moment.date() if moment else None


# How a walk of the search API ended. Only STOP_END and STOP_DAY mean the
# list for these filters was read to its bottom (or past the day); STOP_TARGET
# is the caller holding what it asked for. The other three stopped short, and
# a pool that ends on one of them is not «everything Divar had».
STOP_TARGET = "target"
STOP_END = "end"        # Divar said there is no next page
STOP_DAY = "day"        # the cursor moved past until_day
STOP_STUCK = "stuck"    # a page with nothing new, though Divar promised more
STOP_CAP = "cap"        # _MAX_PAGES read and the list still going
STOP_ERROR = "error"    # a page was refused, unreadable, or never answered


@dataclass
class FeedReport:
    """What fetch_listings saw, filled in when the caller passes one.

    The (listings, error) pair it returns cannot say why a walk with no error
    stopped, and its error is a sentence: the collector needs the facts
    behind it — which page, what status, Divar's own words — to tell the
    person what happened and what to do about it.
    """
    stop: str = ""
    # the last page Divar answered with a 200, numbered from the first page
    # of the whole feed, so a continuation keeps counting
    last_page: int = 0
    # the page that ended the walk, when it ended on a problem
    page: Optional[int] = None
    status: Optional[int] = None           # its HTTP status, if it had one
    divar_message: Optional[str] = None    # what Divar wrote about it, verbatim
    sentence: Optional[str] = None         # the error, as fetch_listings returned it
    # where the next page starts, while there is one — the way back in
    cursor: Optional[dict] = None
    # rows the last page carried past `target`: fetched, but not returned
    leftover: List[dict] = field(default_factory=list)


def _divar_says(resp: Any) -> Optional[str]:
    """What Divar wrote in a refusal, in its own words, or None.

    Its API errors are JSON — {"message": ...}, or the gRPC-gateway
    {"code": 3, "message": ...}, or {"error": {"message": ...}}. A proxy's
    HTML error page says nothing a person can act on, so it is not quoted.
    """
    body: Any = None
    try:
        body = resp.json()
    except Exception:
        body = None
    said: Any = None
    if isinstance(body, dict):
        for key in ("message", "detail", "error_message", "error", "title", "description"):
            value = body.get(key)
            if isinstance(value, dict):
                value = value.get("message") or value.get("detail") or value.get("title")
            if isinstance(value, str) and value.strip():
                said = value
                break
    if said is None and body is None:
        text = getattr(resp, "text", "") or ""
        if isinstance(text, str) and text.strip() and not text.lstrip().startswith("<"):
            said = text
    return " ".join(str(said).split())[:200] if said else None


async def fetch_listings(city: str, form_data: Dict[str, Any], *,
                         target: int, until_day=None,
                         on_page=None,
                         report: Optional[FeedReport] = None,
                         after: Optional[dict] = None,
                         first_page: int = 1,
                         exclude: Optional[set] = None) -> Tuple[List[dict], Optional[str]]:
    """(listings, error). Page Divar's search until `target` listings are in
    hand, or — with `until_day` — until the feed's cursor moves past that day.

    Never raises. An error is returned as a sentence so the caller can fall
    back to the browser and say why — and it comes back WITH the listings
    already gathered: a refusal on page 2 does not unmake page 1.

    `report`, when given, is filled in with how the walk ended. `after` is a
    cursor from an earlier walk's report (its pages numbered on from
    `first_page`), and `exclude` the tokens the caller already holds, so a
    pool can be topped up where it left off.
    """
    import asyncio

    rep = report if report is not None else FeedReport()
    out: List[dict] = []
    seen = set(exclude or ())

    def _stopped(why: str, page: Optional[int] = None, *, status: Optional[int] = None,
                 said: Optional[str] = None, sentence: Optional[str] = None):
        rep.stop, rep.page, rep.status = why, page, status
        rep.divar_message, rep.sentence = said, sentence
        return sentence

    async with httpx.AsyncClient(timeout=25.0) as client:
        city_id = await resolve_city_id(city, client)
        if not city_id:
            return out, _stopped(STOP_ERROR, sentence=f"شهر «{city}» در دیوار پیدا نشد")
        body: Dict[str, Any] = {
            "city_ids": [str(city_id)],
            "search_data": {"form_data": {"data": form_data}},
        }
        if after:
            body["pagination_data"] = after
        headers = {"User-Agent": _UA, "Content-Type": "application/json",
                   "Accept": "application/json"}
        for page in range(max(first_page, 1), _MAX_PAGES + 1):
            try:
                resp = await client.post(SEARCH_URL, json=body, headers=headers)
            except Exception as e:
                return out, _stopped(STOP_ERROR, page, sentence=(
                    f"دیوار به صفحهٔ {page} جست‌وجو پاسخ نداد ({type(e).__name__}: {e})"))
            if resp.status_code != 200:
                said = _divar_says(resp)
                return out, _stopped(
                    STOP_ERROR, page, status=resp.status_code, said=said, sentence=(
                        f"دیوار صفحهٔ {page} جست‌وجو را رد کرد "
                        f"(HTTP {resp.status_code}" + (f": {said}" if said else "") + ")"))
            try:
                payload = resp.json()
            except Exception:
                return out, _stopped(STOP_ERROR, page, status=resp.status_code, sentence=(
                    f"پاسخ دیوار به صفحهٔ {page} جست‌وجو قابل خواندن نبود"))
            rep.last_page = page

            fresh = 0
            for w in payload.get("list_widgets") or []:
                row = _row_from_widget(w)
                if row and row["divar_id"] not in seen:
                    seen.add(row["divar_id"])
                    out.append(row)
                    fresh += 1
            if on_page:
                try:
                    await on_page(page, fresh, len(out))
                except Exception:
                    pass

            pagination = payload.get("pagination") or {}
            more = bool(pagination.get("has_next_page") and pagination.get("data"))
            rep.cursor = pagination.get("data") if more else None
            if until_day is not None:
                day = _cursor_day(pagination)
                if day is not None and day < until_day:
                    _stopped(STOP_DAY, page)        # the feed has moved past the day
                    break
            elif len(out) >= target:
                _stopped(STOP_TARGET if more else STOP_END, page)
                break
            if not more:
                _stopped(STOP_END, page)
                break
            if fresh == 0:
                _stopped(STOP_STUCK, page)          # a stuck cursor; do not spin
                break
            body["pagination_data"] = pagination["data"]
            await asyncio.sleep(_PAGE_PAUSE)
        else:
            _stopped(STOP_CAP, _MAX_PAGES)
    if until_day is not None:
        return out, None
    rep.leftover = out[target:]
    return out[:target], None


def refusal_advice(status: Optional[int], divar_message: Optional[str] = None,
                   category_name: Optional[str] = None) -> str:
    """What a person can do about a search page Divar refused, in Persian.

    A 400 is Divar rejecting the search itself — almost always a filter the
    category does not take, which Divar names («invalid filter for
    shop-sell: credit»). Running it again unchanged gets the same answer, so
    the advice is to change the form, not to press «ادامه».
    """
    if status == 400:
        named = _filter_named_in(divar_message)
        where = f" برای دستهٔ «{category_name}»" if category_name else ""
        if named:
            return f"فیلتر «{named}»{where} معتبر نیست؛ آن را خالی کنید و دوباره اجرا کنید."
        return ("دیوار یکی از فیلترهای این جست‌وجو را نپذیرفت؛ فیلترها را بررسی کنید "
                "و دوباره اجرا کنید.")
    if status == 429:
        return "دیوار گفت درخواست‌ها زیاد است؛ چند دقیقه صبر کنید و «ادامه» را بزنید."
    if status in (401, 403):
        return "دیوار این درخواست را نپذیرفت؛ کمی بعد «ادامه» را بزنید."
    if status and status >= 500:
        return "سرور دیوار خطا داد؛ کمی بعد «ادامه» را بزنید."
    return "کمی بعد «ادامه» را بزنید."


# Divar's names for the filters it takes, as the form labels them.
_FILTER_FA = {
    "credit": "ودیعه", "rent": "اجاره", "price": "قیمت", "size": "متراژ",
    "business-type": "نوع آگهی‌دهنده", "has-photo": "عکس‌دار",
}


def _filter_named_in(message: Optional[str]) -> Optional[str]:
    """The form's name for the filter Divar's message complains about.

    Only a whole word counts: «shop-rent» in «invalid filter for shop-rent:
    price» is the category, not the rent filter.
    """
    if not message:
        return None
    # Every filter any category has can be the one named now (#27), each
    # under the title the form shows it by.
    from app.services import divar_filters as df
    names = {k: f.get("title") or k for k, f in df.definitions().items()}
    names.update(_FILTER_FA)
    words = "|".join(map(re.escape, sorted(names, key=len, reverse=True)))
    found = re.findall(rf"(?<![\w-])({words})(?![\w-])", message)
    return names[found[-1]] if found else None
