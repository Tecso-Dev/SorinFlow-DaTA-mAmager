"""Write app/services/divar_filters.json: the filters Divar has per category (#27).

    python scripts/fetch_divar_filters.py            # read Divar, 15 s between pages
    python scripts/fetch_divar_filters.py --seed     # only the table below, no network

Live, it opens divar.ir/s/<city>/<slug> for every category, reads the form
Divar itself shows (window.__PRELOADED_STATE__.nb.filtersPage.widgetList),
and writes it sorted — categories by slug, filters by key — so a change shows
as a small diff. What a page does not say (a Persian title, a unit, the
options of a choice) is kept from the table below. A page that does not
answer keeps its table entry, and the script says which.

The committed file was written with --seed. The table is the one read off
Divar on 1405/07/07 and published in issue #27, value shapes included; the
machine that wrote it could not reach divar.ir. The four choice filters whose
options the issue does not list (cooling_system, heating_system, floor_type,
warm_water_provider) are there with options null: the panel does not offer
them until a live read fills them in, and the server's daily read does that
by itself (app/services/divar_filters.py).
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT = ROOT / "app" / "services" / "divar_filters.json"

TOMAN = "تومان"


def _opts(*pairs):
    return [{"value": v, "title": t} for v, t in pairs]


# Every filter any category has: type, Persian title, unit, options.
FILTERS = {
    # ranges — {"number_range": {"minimum": "…", "maximum": "…"}}
    "price":            {"type": "number_range", "title": "قیمت کل", "unit": TOMAN},
    "credit":           {"type": "number_range", "title": "ودیعه", "unit": TOMAN},
    "rent":             {"type": "number_range", "title": "اجارهٔ ماهانه", "unit": TOMAN},
    "daily_rent":       {"type": "number_range", "title": "اجارهٔ روزانه", "unit": TOMAN},
    "size":             {"type": "number_range", "title": "متراژ", "unit": "متر مربع"},
    "price_per_square": {"type": "number_range", "title": "قیمت هر متر", "unit": TOMAN},
    "building-age":     {"type": "number_range", "title": "سن بنا", "unit": "سال"},
    "floor":            {"type": "number_range", "title": "طبقه"},
    "person_capacity":  {"type": "number_range", "title": "ظرفیت", "unit": "نفر"},
    "floors_count":     {"type": "number_range", "title": "تعداد طبقات"},
    "unit_per_floor":   {"type": "number_range", "title": "تعداد واحد در هر طبقه"},
    # switches — {"boolean": {"value": true}}
    "parking":   {"type": "boolean", "title": "پارکینگ"},
    "elevator":  {"type": "boolean", "title": "آسانسور"},
    "warehouse": {"type": "boolean", "title": "انباری"},
    "balcony":   {"type": "boolean", "title": "بالکن"},
    "rebuilt":   {"type": "boolean", "title": "بازسازی‌شده"},
    "has-photo": {"type": "boolean", "title": "عکس‌دار"},
    "has-video": {"type": "boolean", "title": "ویدئودار"},
    "bizzDeed":  {"type": "boolean", "title": "سند اداری"},
    # several choices — {"repeated_string": {"value": [...]}}
    "rooms": {"type": "repeated_string", "title": "تعداد اتاق", "options": _opts(
        ("بدون اتاق", "بدون اتاق"), ("یک", "یک"), ("دو", "دو"), ("سه", "سه"),
        ("چهار", "چهار"), ("بیشتر", "بیشتر"))},
    "business-type": {"type": "repeated_string", "title": "آگهی‌دهنده", "options": _opts(
        ("personal", "شخصی"), ("real-estate-business", "مشاور املاک"))},
    "deed_type": {"type": "repeated_string", "title": "نوع سند", "options": _opts(
        ("single_page", "تک‌برگ"), ("multi_page", "منگوله‌دار"),
        ("written_agreement", "قولنامه‌ای"), ("other", "سایر"))},
    "building_direction": {"type": "repeated_string", "title": "جهت ساختمان", "options": _opts(
        ("east", "شرقی"), ("west", "غربی"), ("north", "شمالی"), ("south", "جنوبی"))},
    # options not in the issue: kept, not offered until a live read fills them
    "cooling_system":      {"type": "repeated_string", "title": "سرمایش", "options": None},
    "heating_system":      {"type": "repeated_string", "title": "گرمایش", "options": None},
    "floor_type":          {"type": "repeated_string", "title": "جنس کف", "options": None},
    "warm_water_provider": {"type": "repeated_string", "title": "تأمین‌کنندهٔ آب گرم", "options": None},
    # one choice — {"str": {"value": "…"}}
    "recent_ads": {"type": "str", "title": "آگهی‌های اخیر", "options": _opts(
        ("3h", "۳ ساعت اخیر"), ("12h", "۱۲ ساعت اخیر"), ("1d", "یک روز اخیر"),
        ("3d", "۳ روز اخیر"), ("7d", "۷ روز اخیر"))},
    "toilet": {"type": "str", "title": "سرویس بهداشتی", "options": _opts(
        ("squat", "ایرانی"), ("seat", "فرنگی"), ("squat_seat", "ایرانی و فرنگی"))},
}

# Every category has these four as well.
COMMON = ["has-photo", "has-video", "business-type", "recent_ads"]

_HOME = ["building_direction", "cooling_system", "heating_system", "floor_type",
         "toilet", "warm_water_provider"]

# slug → (Divar's internal name, family, the category's own filters), from
# the issue's table. family says which price filters mean anything there:
# buy (price, price per metre), rent (deposit, monthly rent), temporary
# (daily rent, capacity), and all/other/service for none of them.
CATEGORIES = {
    "real-estate": ("real-estate", "all", []),
    "rent-residential": ("residential-rent", "rent",
                         ["credit", "rent", "size", "rooms", "parking", "balcony", "warehouse"]),
    "rent-apartment": ("apartment-rent", "rent",
                       ["credit", "rent", "size", "rooms", "building-age", "floor", "parking",
                        "elevator", "warehouse", "balcony", "rebuilt", "floors_count",
                        "unit_per_floor"] + _HOME),
    "rent-villa": ("house-villa-rent", "rent",
                   ["credit", "rent", "size", "rooms", "building-age", "parking", "balcony",
                    "warehouse", "rebuilt"] + _HOME),
    "buy-residential": ("residential-sell", "buy",
                        ["price", "size", "price_per_square", "parking", "balcony", "warehouse"]),
    "buy-apartment": ("apartment-sell", "buy",
                      ["price", "size", "price_per_square", "rooms", "building-age", "floor",
                       "deed_type", "parking", "elevator", "warehouse", "balcony", "rebuilt",
                       "floors_count", "unit_per_floor"] + _HOME),
    "buy-villa": ("house-villa-sell", "buy",
                  ["price", "size", "price_per_square", "rooms", "building-age", "deed_type",
                   "parking", "balcony", "warehouse", "rebuilt"] + _HOME),
    "buy-old-house": ("plot-old", "buy", ["price", "size", "price_per_square", "parking"]),
    "buy-commercial-property": ("commercial-sell", "buy",
                                ["price", "size", "price_per_square", "bizzDeed"]),
    "buy-office": ("office-sell", "buy",
                   ["price", "size", "price_per_square", "rooms", "building-age", "floor",
                    "elevator", "parking", "warehouse", "bizzDeed"]),
    "buy-store": ("shop-sell", "buy",
                  ["price", "size", "price_per_square", "rooms", "building-age", "bizzDeed"]),
    "buy-industrial-agricultural-property": ("industry-agriculture-business-sell", "buy",
                                             ["price", "size", "price_per_square", "rooms",
                                              "building-age", "bizzDeed"]),
    "rent-commercial-property": ("commercial-rent", "rent", ["credit", "rent", "size"]),
    "rent-office": ("office-rent", "rent",
                    ["size", "credit", "rent", "rooms", "building-age", "floor", "elevator",
                     "parking", "warehouse"]),
    "rent-store": ("shop-rent", "rent", ["credit", "rent", "size"]),
    "rent-industrial-agricultural-property": ("industry-agriculture-business-rent", "rent",
                                              ["credit", "rent", "size", "rooms", "building-age"]),
    "real-estate-services": ("real-estate-services", "service", []),
    "contribution-construction": ("partnership", "other", []),
    "pre-sell-home": ("presell", "buy", []),
    "rent-temporary": ("temporary-rent", "temporary",
                       ["daily_rent", "person_capacity", "size", "rooms"]),
    "rent-temporary-suite-apartment": ("suite-apartment", "temporary",
                                       ["person_capacity", "daily_rent", "size", "rooms"]),
    "rent-temporary-villa": ("villa", "temporary",
                             ["person_capacity", "daily_rent", "size", "rooms"]),
    "rent-temporary-workspace": ("workspace", "temporary",
                                 ["person_capacity", "daily_rent", "size", "rooms"]),
}


def _filter(key):
    d = dict(FILTERS[key])
    out = {"key": key, "type": d["type"], "title": d["title"]}
    if d.get("unit"):
        out["unit"] = d["unit"]
    if d["type"] in ("repeated_string", "str"):
        out["options"] = d.get("options")
    return out


def seed_schema() -> dict:
    cats = {}
    for slug, (token, family, own) in CATEGORIES.items():
        keys = sorted(set(own) | set(COMMON))
        cats[slug] = {"token": token, "family": family,
                      "filters": [_filter(k) for k in keys]}
    return {
        "_about": ("Divar's search filters per category. Written by "
                   "scripts/fetch_divar_filters.py; options null = not known yet."),
        "source": "issue-27-table",
        "read_on": "1405-07-07",
        "categories": dict(sorted(cats.items())),
    }


def write(schema: dict, path: Path = OUT) -> None:
    path.write_text(json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    shown = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
    print(f"wrote {shown} ({len(schema['categories'])} categories)")


async def live(city: str, gap: float) -> dict:
    from app.services import divar_filters as df
    base = seed_schema()
    slugs = sorted(base["categories"])
    print(f"reading {len(slugs)} category pages from divar.ir/s/{city}/…, {gap:g} s apart")
    got, names, failed = await df.read_divar(slugs, city=city, gap=gap)
    schema = df.merge_live(got, base)
    for slug, name in names.items():
        schema["categories"][slug]["token"] = name
    schema["source"] = "divar"
    from datetime import date
    schema["read_on"] = date.today().isoformat()
    for line in df.changes(base, schema):
        print("  changed:", line)
    if failed:
        print(f"  not read, kept from the table: {', '.join(failed)}")
    return schema


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seed", action="store_true", help="write the table only, no network")
    ap.add_argument("--city", default="tehran")
    ap.add_argument("--gap", type=float, default=15.0, help="seconds between two pages")
    args = ap.parse_args()
    write(seed_schema() if args.seed else asyncio.run(live(args.city, args.gap)))


if __name__ == "__main__":
    main()
