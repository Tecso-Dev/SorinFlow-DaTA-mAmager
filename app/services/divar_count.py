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


def build_form_data(
    category: Optional[str] = None,
    *,
    advertiser_type: Optional[str] = None,
    has_images: Optional[bool] = None,
    min_price: Optional[int] = None, max_price: Optional[int] = None,
    min_deposit: Optional[int] = None, max_deposit: Optional[int] = None,
    min_rent: Optional[int] = None, max_rent: Optional[int] = None,
    min_area: Optional[int] = None, max_area: Optional[int] = None,
) -> Dict[str, Any]:
    """The filters Divar can apply itself, in the shape its API expects.

    Only what Divar actually honours goes in. Anything it ignores would make
    the count a promise the scrape could not keep, so rooms and the amenity
    toggles are deliberately left out — Divar does not narrow on them here, and
    the scraper still applies those itself after opening each ad.
    """
    data: Dict[str, Any] = {}
    token = CATEGORY_TOKENS.get((category or "").strip())
    if token:
        data["category"] = {"str": {"value": token}}

    kind = BUSINESS_TYPES.get((advertiser_type or "").strip())
    if kind:
        data["business-type"] = {"repeated_string": {"value": [kind]}}

    if has_images:
        data["has-photo"] = {"boolean": {"value": True}}

    for field, lo, hi in (
        ("price",  min_price,   max_price),
        ("credit", min_deposit, max_deposit),   # ودیعه
        ("rent",   min_rent,    max_rent),
        ("size",   min_area,    max_area),      # متراژ
    ):
        band = _range(lo, hi)
        if band:
            data[field] = band
    return data


def build_search_query(
    *,
    advertiser_type: Optional[str] = None,
    has_images: Optional[bool] = None,
    min_price: Optional[int] = None, max_price: Optional[int] = None,
    min_deposit: Optional[int] = None, max_deposit: Optional[int] = None,
    min_rent: Optional[int] = None, max_rent: Optional[int] = None,
    min_area: Optional[int] = None, max_area: Optional[int] = None,
) -> str:
    """The same filters as build_form_data, in the shape a divar.ir URL wants.

    The scraper loaded «/s/{city}/{category}» with no filters at all and then
    threw away whatever did not match. On one real run that meant collecting
    204 listings to keep 14, with 131 dropped on deposit alone — and the 201
    that DID match were never looked at, because they were further down a feed
    the run had already stopped reading.

    Divar narrows on exactly the fields build_form_data lists, so asking it to
    is both far fewer requests and the only way to actually reach the listings
    the filter promised. Ranges are «min-max», with either side allowed to be
    empty.
    """
    parts = []
    for field, lo, hi in (
        ("price",  min_price,   max_price),
        ("credit", min_deposit, max_deposit),   # ودیعه
        ("rent",   min_rent,    max_rent),
        ("size",   min_area,    max_area),      # متراژ
    ):
        if lo is None and hi is None:
            continue
        parts.append(f"{field}={'' if lo is None else int(lo)}-"
                     f"{'' if hi is None else int(hi)}")

    kind = BUSINESS_TYPES.get((advertiser_type or "").strip())
    if kind:
        parts.append(f"business-type={kind}")
    if has_images:
        parts.append("has-photo=true")

    return "&".join(parts)


def unsupported_filters(**kwargs) -> list:
    """Filters the caller asked for that Divar will not narrow on, so the
    estimate can say the real number is at most this."""
    names = {
        "min_rooms": "حداقل اتاق", "max_rooms": "حداکثر اتاق",
        "has_elevator": "آسانسور", "has_parking": "پارکینگ",
        "has_storage": "انباری", "has_balcony": "بالکن",
        "min_price_per_meter": "قیمت هر متر", "max_price_per_meter": "قیمت هر متر",
    }
    return [fa for key, fa in names.items() if kwargs.get(key) not in (None, False, "")]


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
    words = "|".join(map(re.escape, _FILTER_FA))
    found = re.findall(rf"(?<![\w-])({words})(?![\w-])", message)
    return _FILTER_FA[found[-1]] if found else None
