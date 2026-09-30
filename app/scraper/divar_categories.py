"""
Divar's own category tree, by the names Divar prints on an ad's breadcrumb (#57).

A run searches Divar with the category's own token, so every candidate it
collects is one Divar itself filed in that category. The scraper used to
decide again, by looking for words of ours in the ad's URL, its title and
its breadcrumb — «کلنگی», «دفتر», «صنعتی» — and every word missing from a
list was a listing thrown away as «خارج از دسته‌بندی» although Divar had
filed it exactly there. A short-term villa's breadcrumb («اجارهٔ کوتاه‌مدت ›
ویلا و باغ») named none of rent-temporary's words; neither did «دفتر کار و
فضای آموزشی».

Now the breadcrumb is read as what it is: a path in Divar's tree. Each crumb
is looked up among the names Divar gives its categories — the long name
(«فروش زمین و کلنگی») and the short one the menus use («زمین و کلنگی») —
and the deepest one found says where Divar filed the ad. A listing is off
the run's category only when that place is known and is neither the run's
category, nor inside it, nor one of its parents. A name we do not know is
never a reason to drop: a finer sub-category («فروش کارگاه، کارخانه و
سوله») stops at its known parent, and a breadcrumb we cannot place at all is
kept, because the search Divar filtered by category is the better evidence.
The one exception is a breadcrumb that is not real estate at all — a car,
a job — which says so in Divar's own words.

The names are Divar's, as shown on its category menus and breadcrumbs; the
comparison ignores the spellings Divar varies between (a zero-width
non-joiner or a space in «کوتاه‌مدت», «اجارهٔ» or «اجاره», Arabic «ي» and
«ك», «،» or «,»).
"""
import re
from typing import Dict, Iterable, List, Optional, Tuple

ROOT = "real-estate"

# slug → (parent slug, the names Divar prints for it). The slugs are the
# scraper's (app/config.py CATEGORIES, and the six the filter schema knows).
TREE: Dict[str, Tuple[Optional[str], Tuple[str, ...]]] = {
    ROOT: (None, ("املاک",)),

    "buy-residential": (ROOT, ("فروش مسکونی", "خرید مسکونی")),
    "buy-apartment": ("buy-residential", ("فروش آپارتمان", "خرید آپارتمان", "آپارتمان")),
    "buy-villa": ("buy-residential", ("فروش خانه و ویلا", "خرید خانه و ویلا", "خانه و ویلا")),
    "buy-old-house": ("buy-residential", ("فروش زمین و کلنگی", "خرید زمین و کلنگی",
                                          "زمین و کلنگی")),

    "rent-residential": (ROOT, ("اجاره مسکونی",)),
    "rent-apartment": ("rent-residential", ("اجاره آپارتمان", "آپارتمان")),
    "rent-villa": ("rent-residential", ("اجاره خانه و ویلا", "خانه و ویلا")),

    "buy-commercial-property": (ROOT, ("فروش اداری و تجاری", "خرید اداری و تجاری")),
    "buy-office": ("buy-commercial-property", ("فروش دفتر کار، اتاق اداری و مطب",
                                               "خرید دفتر کار، اتاق اداری و مطب",
                                               "دفتر کار، اتاق اداری و مطب")),
    "buy-store": ("buy-commercial-property", ("فروش مغازه و غرفه", "خرید مغازه و غرفه",
                                              "مغازه و غرفه")),
    "buy-industrial-agricultural-property": (
        "buy-commercial-property", ("فروش صنعتی، کشاورزی و تجاری",
                                    "خرید صنعتی، کشاورزی و تجاری",
                                    "صنعتی، کشاورزی و تجاری")),

    "rent-commercial-property": (ROOT, ("اجاره اداری و تجاری",)),
    "rent-office": ("rent-commercial-property", ("اجاره دفتر کار، اتاق اداری و مطب",
                                                 "دفتر کار، اتاق اداری و مطب")),
    "rent-store": ("rent-commercial-property", ("اجاره مغازه و غرفه", "مغازه و غرفه")),
    "rent-industrial-agricultural-property": (
        "rent-commercial-property", ("اجاره صنعتی، کشاورزی و تجاری",
                                     "صنعتی، کشاورزی و تجاری")),

    "rent-temporary": (ROOT, ("اجاره کوتاه مدت",)),
    "rent-temporary-suite-apartment": ("rent-temporary", ("اجاره کوتاه مدت آپارتمان و سوئیت",
                                                          "آپارتمان و سوئیت")),
    "rent-temporary-villa": ("rent-temporary", ("اجاره کوتاه مدت ویلا و باغ", "ویلا و باغ")),
    "rent-temporary-workspace": ("rent-temporary", (
        "اجاره کوتاه مدت دفتر کار و فضای آموزشی", "دفتر کار و فضای آموزشی")),

    "real-estate-services": (ROOT, ("پروژه های ساخت و ساز", "خدمات املاک")),
    "contribution-construction": ("real-estate-services", ("مشارکت در ساخت",)),
    "pre-sell-home": ("real-estate-services", ("پیش فروش",)),
}

# Which families a node's listings are in, by the node that decides it.
_LISTING_TYPE = {"buy-residential": "buy", "buy-commercial-property": "buy",
                 "rent-residential": "rent", "rent-commercial-property": "rent",
                 "rent-temporary": "rent"}

# Divar's other top-level categories. A breadcrumb that starts at one of them
# and never reaches «املاک» is Divar saying the ad is not real estate — even
# when a leaf shares a word with a property name («اداری و مدیریتی» is a job,
# «ماشین‌آلات صنعتی» a machine, «ابزار باغبانی و کشاورزی» a tool).
OTHER_ROOTS = ("استخدام و کاریابی", "وسایل نقلیه", "کالای دیجیتال", "لوازم الکترونیکی",
               "خانه و آشپزخانه", "خدمات", "وسایل شخصی", "سرگرمی و فراغت", "اجتماعی",
               "تجهیزات و صنعتی", "برای کسب و کار")

# Words a real-estate category name carries. Only asked of a breadcrumb that
# places the ad nowhere in the tree above — no «املاک», no name only one
# category has: Divar's own «سواری», «استخدام و کاریابی» or «خانه و آشپزخانه ›
# لوازم خانگی» has none of them.
_REAL_ESTATE_WORDS = ("املاک", "مسکونی", "آپارتمان", "ویلا", "زمین", "کلنگی",
                      "اداری", "تجاری", "مغازه", "غرفه", "صنعتی", "کشاورزی",
                      "سوله", "کارگاه", "سوئیت", "رهن", "ساخت و ساز", "پیش فروش")


def normalize(text: Optional[str]) -> str:
    """One spelling for the several Divar prints."""
    t = str(text or "")
    t = (t.replace("‌", " ").replace("‏", " ").replace("‎", " ")
         .replace("-", " ").replace("_", " ")
         .replace("ي", "ی").replace("ى", "ی").replace("ك", "ک")
         .replace("ۀ", "ه").replace("ٔ", "").replace("أ", "ا").replace("إ", "ا"))
    t = re.sub(r"\s*[،,]\s*", "، ", t)
    return " ".join(t.split())


def _index() -> Tuple[Dict[str, List[str]], Dict[Optional[str], Dict[str, str]]]:
    """(name → every slug that has it, parent → {name → child slug})."""
    anywhere: Dict[str, List[str]] = {}
    children: Dict[Optional[str], Dict[str, str]] = {}
    for slug, (parent, names) in TREE.items():
        for name in names:
            key = normalize(name)
            anywhere.setdefault(key, []).append(slug)
            children.setdefault(parent, {})[key] = slug
    return anywhere, children


_ANYWHERE, _CHILDREN = _index()


def known(slug: Optional[str]) -> bool:
    return bool(slug) and slug in TREE


def ancestors(slug: Optional[str]) -> List[str]:
    """slug, its parent, …, the root."""
    out: List[str] = []
    while slug and slug in TREE and slug not in out:
        out.append(slug)
        slug = TREE[slug][0]
    return out


def related(a: Optional[str], b: Optional[str]) -> bool:
    """One is the other, inside it, or around it."""
    return bool(a and b) and (a in ancestors(b) or b in ancestors(a))


def place(crumbs: Iterable[str]) -> Tuple[Optional[str], Optional[str]]:
    """(where Divar filed the ad, the crumb that says so): the deepest crumb
    that names a category.

    Walked from the root down. A crumb is first looked for among the children
    of the category found so far — the only way a short, shared name
    («آپارتمان», «خانه و ویلا») can be placed — and then among the names only
    one category has. A crumb that names nothing (the city, a finer
    sub-category, a neighbourhood) leaves the place where it was.
    """
    node: Optional[str] = None
    said: Optional[str] = None
    for crumb in crumbs:
        key = normalize(crumb)
        if not key:
            continue
        child = _CHILDREN.get(node, {}).get(key) if node is not None else None
        if child is None:
            only = _ANYWHERE.get(key) or []
            child = only[0] if len(only) == 1 else None
        if child is not None:
            node, said = child, crumb
    return node, said


def locate(crumbs: Iterable[str]) -> Optional[str]:
    """Where Divar filed the ad, or None (see place)."""
    return place(crumbs)[0]


def listing_type(node: Optional[str]) -> Optional[str]:
    """«buy» or «rent» for a node inside one of the buy or rent families."""
    for slug in ancestors(node):
        if slug in _LISTING_TYPE:
            return _LISTING_TYPE[slug]
    return None


def name_of(slug: Optional[str]) -> str:
    if not slug or slug not in TREE:
        return slug or ""
    return TREE[slug][1][0]


def judge(crumbs: Iterable[str], target: Optional[str]) -> Tuple[bool, Optional[str], str]:
    """(keep, where Divar filed it, why) for a listing collected for `target`.

    Dropped only on Divar's own word: a known category unrelated to the
    run's, or a breadcrumb that is not real estate at all.
    """
    crumbs = [c for c in (crumbs or []) if normalize(c)]
    if not crumbs:
        return True, None, "no breadcrumb — Divar's own category filter is the evidence"
    node = locate(crumbs)
    if node is not None:
        if not known(target) or related(node, target):
            return True, node, f"Divar files it under {node}"
        return False, node, f"Divar files it under {node}, not {target}"
    keys = {normalize(c) for c in crumbs}
    if keys & {normalize(r) for r in OTHER_ROOTS} and normalize("املاک") not in keys:
        return False, None, "Divar files it under another of its top-level categories"
    # A name several categories share, with nothing above it to say which
    # («آپارتمان» alone), is still one of Divar's real-estate names.
    if any(normalize(c) in _ANYWHERE for c in crumbs):
        return True, None, "a breadcrumb we cannot place — kept"
    flat = " ".join(normalize(c) for c in crumbs)
    if any(normalize(w) in flat for w in _REAL_ESTATE_WORDS):
        return True, None, "a breadcrumb we cannot place — kept"
    return False, None, "Divar's breadcrumb is not real estate"
