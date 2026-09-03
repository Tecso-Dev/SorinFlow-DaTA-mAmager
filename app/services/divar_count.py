"""
«چند آگهی با این فیلترها هست؟» — ask Divar before scraping.

Divar's own search page answers this above the results («۳۴۳ آگهی در این
محدوده»), and its search API carries the same number at map_data.post_count.
Asking it costs one HTTP request, where finding out by scraping costs opening
every ad in the city.

Everything here except fetch_post_count() is pure and testable: the slug and
filter mapping is the part that can silently go wrong.
"""
from typing import Any, Dict, Optional, Tuple

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
