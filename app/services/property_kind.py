"""
نوع ملک — telling an apartment from a villa from a کلنگی.

Divar's «خرید مسکونی» is an umbrella: it holds apartments, villas and کلنگی
alike, so filtering the leads list by that category cannot narrow a search to
one kind of property. This is the missing axis.

The awkward part is that property_type is written two different ways. The
scraper takes it off Divar's breadcrumb, so it lands in Persian («آپارتمان»);
the manual lead form writes an English slug («apartment»). Both are in the
table, so a filter has to accept either — and fall back to the ad's title when
the column was never filled, which is common for older rows.

Pure module: no DB, no imports from the app, so the matching rules can be
tested directly.
"""
from typing import Dict, List, Optional

# canonical kind → how it is stored, and how it reads in an ad title.
#   values     — accepted contents of Property.property_type, either language
#   title_any  — a title containing one of these suggests this kind
#   title_none — …unless it also contains one of these, which means another kind
PROPERTY_KINDS: Dict[str, Dict[str, object]] = {
    "apartment": {
        "label": "آپارتمان",
        "values": ["apartment", "آپارتمان", "اپارتمان", "آپارتمانی"],
        "title_any": ["آپارتمان", "اپارتمان", "واحد", "طبقه"],
        "title_none": ["کلنگی", "تخریبی", "زمین", "مغازه", "ویلا", "ویلایی"],
    },
    "villa": {
        "label": "ویلایی",
        "values": ["villa", "ویلا", "ویلایی", "خانه", "منزل", "خانه ویلایی"],
        "title_any": ["ویلا", "ویلایی", "خانه", "منزل", "حیاط دار", "حیاطدار"],
        "title_none": ["کلنگی", "تخریبی", "آپارتمان", "اپارتمان"],
    },
    "old_house": {
        "label": "کلنگی",
        "values": ["old_house", "کلنگی", "خانه کلنگی", "تخریبی"],
        "title_any": ["کلنگی", "تخریبی"],
        "title_none": [],
    },
    "land": {
        "label": "زمین",
        "values": ["land", "زمین", "کشاورزی", "صنعتی و کشاورزی"],
        "title_any": ["زمین", "قطعه"],
        "title_none": ["کلنگی", "آپارتمان"],
    },
    "shop": {
        "label": "مغازه",
        "values": ["shop", "مغازه", "تجاری", "غرفه"],
        "title_any": ["مغازه", "غرفه"],
        "title_none": [],
    },
    "office": {
        "label": "دفتر کار",
        "values": ["office", "دفتر کار", "دفتر", "اداری"],
        "title_any": ["دفتر", "اداری"],
        "title_none": ["مغازه"],
    },
}

VALID_KINDS = set(PROPERTY_KINDS)


def kind_options() -> List[Dict[str, str]]:
    """(key, label) pairs for the dropdown, in a sensible order."""
    return [{"key": k, "label": str(v["label"])} for k, v in PROPERTY_KINDS.items()]


def _norm(text: Optional[str]) -> str:
    return (text or "").replace("‌", " ").strip().lower()


def matches_stored_type(kind: str, property_type: Optional[str]) -> bool:
    """Does a stored property_type mean this kind, in either language?"""
    spec = PROPERTY_KINDS.get(kind)
    if not spec or not property_type:
        return False
    stored = _norm(property_type)
    return any(_norm(v) == stored for v in spec["values"])


def title_suggests(kind: str, title: Optional[str]) -> bool:
    """Does an ad title read like this kind?

    Only consulted when property_type is empty. «فروش کلنگی خ کاشانی» must not
    count as a villa just because a کلنگی is a house, so each kind names the
    words that rule it out.
    """
    spec = PROPERTY_KINDS.get(kind)
    if not spec or not title:
        return False
    t = _norm(title)
    if any(_norm(w) in t for w in spec["title_none"]):
        return False
    return any(_norm(w) in t for w in spec["title_any"])


def classify(title: Optional[str] = None,
             property_type: Optional[str] = None) -> Optional[str]:
    """Best guess at one property's kind. The stored column wins when set.

    Used for the label shown on a row; the list filter builds SQL from the same
    tables rather than calling this per row.
    """
    for kind in PROPERTY_KINDS:
        if matches_stored_type(kind, property_type):
            return kind
    # most specific first, so «خانه کلنگی» is کلنگی and not ویلایی
    for kind in ("old_house", "shop", "office", "land", "apartment", "villa"):
        if title_suggests(kind, title):
            return kind
    return None
