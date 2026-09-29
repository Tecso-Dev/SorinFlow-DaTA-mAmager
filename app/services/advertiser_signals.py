"""
Whether an ad was written by an agency, read from what the ad itself says.

Divar asks the poster to declare this and the panel can filter on it, but the
declaration is not reliable: a listing whose description ends «املاک هستم»
came back from a search filtered to «شخصی». Checked by hand on Divar's own
site, it comes back there too — so the filter is Divar's to fix and the ad is
ours to label.

That is the shape of this module. It does not correct Divar's answer, it adds
ours beside it: `advertiser_type` stays whatever Divar said, and the fields
here record what the words suggest, with the phrase that suggested it so a
person can disagree.

Negation is the whole difficulty, and it runs both ways.

Before the phrase: «بدون کمیسیون» and «بدون واسطه» are what a private seller
writes, and a matcher that only looks for «کمیسیون» reads them as the
opposite of what they say.

After it: «مشاورین املاک تماس نگیرند», «املاک نیاید» and «به املاکی واگذار
نمی‌شود» are a private seller keeping agencies AWAY, and «مشاور املاک نیستم»
is one saying what they are not. Every one of them contains the phrase, and
none of them was written by an agency. A self-declaration («املاک هستم») is
never taken back by what follows it.

Both are read as words, inside the clause they are in: «بدون» in «بدون
پارکینگ، مشاور املاک آرمان» is about the parking, and «نه» inside «ماهانه» is
not a negator at all.
"""
import re
from typing import Optional, Tuple

# What an agency says about itself. Ordered longest-first only for the sake of
# the evidence string: the most specific phrase is the most convincing one to
# show.
AGENCY_PHRASES = (
    "مشاورین املاک",
    "مشاور املاک",
    "آژانس املاک",
    "دفتر املاک",
    "بنگاه املاک",
    "املاک هستم",
    "مشاور املاکم",
    "همکاری با همکاران",
    "همکار محترم",
    "کارشناس املاک",
    "ثبت رایگان فایل",
    "فایل شما",
    "بنگاه",
    "کمیسیون",
    "کمسیون",
    "حق الزحمه",
    "املاک",
)

# A person saying what they are. Nothing after it takes it back.
_DECLARATIONS = frozenset({"املاک هستم", "مشاور املاکم"})

# Phrases about money: «کمیسیون ندارد» is a seller saying there is none.
_COMMISSION_PHRASES = frozenset({"کمیسیون", "کمسیون", "حق الزحمه"})

# Words that turn a phrase into its opposite when they come just before it.
# «بدون کمیسیون» is a private seller's boast, not an agency's disclosure.
# Matched as whole words: «نه» is inside «ماهانه» and «بی» inside «بیستم», and
# neither of those has ever negated anything.
NEGATORS = ("بدون", "بی", "نه", "غیر", "عدم", "فاقد")
_NEGATOR_RE = re.compile(r"(?<!\w)(?:" + "|".join(NEGATORS) + r")(?!\w)")

# How far back to look for one. Long enough for «بدون هیچ کمیسیون», short
# enough that a negator two sentences earlier does not reach.
_NEGATION_WINDOW = 18

# A negator's reach ends at any of these: «بدون پارکینگ، مشاور املاک آرمان» is
# a clause about parking followed by one about the agency.
_BEFORE_STOP = ".!؟?؛:،,\n—–|"

# How far after a phrase a refusal is looked for, and where it stops looking.
# The end of a sentence (or a dash, which starts an aside) ends it; a comma does
# not, because «مشاورین املاک، تماس نگیرند» is one sentence.
_REFUSAL_REACH = 60
_AFTER_STOP = re.compile(r"[.!؟?؛\n—–|]")

# What a private seller writes to keep agencies away. Every form is matched on
# text that has been through _flatten(), so «نمی‌شود» arrives as «نمی شود» and
# the pattern spells the gap as \s*.
_REFUSAL = re.compile("|".join((
    r"تماس\s*نگیر",                                    # تماس نگیرند / نگیرید / نگیرد
    r"مزاحم\s*نش",                                     # مزاحم نشوند / نشید / نشه
    r"مزاحم\s*نباش",
    r"(?<!\w)نیا(?:ید|یید|یند|د|ن|یین)(?!\w)",         # املاک نیاید / نیایند
    r"(?:واگذار|داده|فروخته|سپرده)\s*(?:نمی\s*(?:شو|شه|گرد)|نشده|نشد|نخواهد)",   # به املاکی واگذار نمی‌شود
    r"(?:نداد|نسپرد)(?:م|ه|یم)",                       # به بنگاه ندادم
    r"نمی\s*(?:خوا(?:هم|هیم|م|یم|د|هد)|پذیر)",         # نمی‌خواهم / نمی‌پذیرم
    r"نمی\s*د(?:هم|هیم|م|یم)(?!\w)",                   # به املاک نمی‌دهم / نمیدم
    r"خودداری",                                        # از تماس … خودداری کنید
    r"ممنوع",                                          # مراجعه املاکی ممنوع
    r"معذور",
    r"زنگ\s*نزن",
    r"پیام\s*(?:نده|ندید)",
    r"زحمت\s*نکش",
    r"کار\s*نمی\s*کن",                                 # با املاکی ها کار نمی‌کنم
    r"کاری\s*ندار",
    r"نیازی\s*(?:ندارم|ندارد|نیست)",
    r"قبول\s*نمی",
)))

# «مشاور املاک نیستم»: the poster saying what they are not. Only ever directly
# after the phrase (with the plural or «ی» that may be attached to it) — a
# «نیستم» at the end of the sentence is usually about something else, «مالک
# نیستم» from an agent.
_NOT_ONE = re.compile(r"^(?:ی|یی|ها|های)?[\s،,]*(?:نیستم|نیستیم|نمی\s*باشم|نمی\s*باشیم)")

# «کمیسیون ندارد», «کمسیون نمی‌گیرم»: directly after a phrase about money.
_NO_COMMISSION = re.compile(
    r"^[\s،,:]*(?:ندارد|ندارم|نداریم|نمی\s*گیر(?:م|یم|د)|نمی\s*باشد|نیست|صفر)")

# A phrase's own spacing may be a space or, across a line break, a newline.
_PHRASE_PATTERNS = tuple(
    (phrase, re.compile("[ \n]".join(re.escape(part) for part in phrase.split(" "))))
    for phrase in AGENCY_PHRASES
)

# Marks that are typed inside otherwise identical phrases: the zero-width
# non-joiner, the invisible direction marks, tatweel and the short vowels.
_ZWNJ = "‌"
_INVISIBLE = re.compile("[‍‎‏‪-‮⁦-⁩]")
_DECORATION = re.compile("[ـً-ٰٟ]")


def _flatten(text: Optional[str]) -> str:
    """One spelling, so one pattern can find it.

    Blanks collapse to one space but line breaks stay: a sentence often ends
    at the end of a line, and a refusal on the next line is not about the
    phrase on this one.
    """
    if not text or not isinstance(text, str):
        return ""
    out = text.replace(_ZWNJ, " ").replace("ي", "ی").replace("ك", "ک")
    out = _INVISIBLE.sub("", out)
    out = _DECORATION.sub("", out)
    out = re.sub(r"[^\S\n]+", " ", out)
    out = re.sub(r" ?\n[ \n]*", "\n", out)
    return out.strip()


def _negated(haystack: str, at: int) -> bool:
    """Is the match at `at` preceded by something that reverses it?"""
    lo = max(0, at - _NEGATION_WINDOW)
    for mark in _BEFORE_STOP:
        found = haystack.rfind(mark, lo, at)
        if found >= 0:
            lo = found + 1
    return _NEGATOR_RE.search(haystack, lo, at) is not None


def _refused(haystack: str, phrase: str, end: int) -> bool:
    """Is the match ending at `end` followed by a seller keeping agents away,
    or by the poster saying they are not one?"""
    if phrase in _DECLARATIONS:
        return False
    tail = haystack[end:end + _REFUSAL_REACH]
    stop = _AFTER_STOP.search(tail)
    if stop:
        tail = tail[:stop.start()]
    if _NOT_ONE.match(tail):
        return True
    if phrase in _COMMISSION_PHRASES and _NO_COMMISSION.match(tail):
        return True
    return _REFUSAL.search(tail) is not None


def detect(*texts: Optional[str]) -> Tuple[bool, Optional[str]]:
    """(looks like an agency, the phrase that said so).

    Every text is searched — a title can give it away where a description
    does not, and the other way round. An occurrence that is negated before it
    or refused after it does not count, and the next one is tried.
    """
    flats = [f for f in (_flatten(t) for t in texts) if f]
    for phrase, pattern in _PHRASE_PATTERNS:
        for flat in flats:
            for m in pattern.finditer(flat):
                if _negated(flat, m.start()) or _refused(flat, phrase, m.end()):
                    continue
                return True, phrase
    return False, None


def annotate(property_data: dict) -> dict:
    """Add the two fields to a scraped record, in place. Returns it.

    Never raises: a listing must not be lost over a label.
    """
    try:
        looks, phrase = detect(
            property_data.get("description"),
            property_data.get("title"),
            property_data.get("category_hint"),
        )
        property_data["agency_suspected"] = looks
        property_data["agency_evidence"] = phrase
    except Exception:
        property_data.setdefault("agency_suspected", False)
        property_data.setdefault("agency_evidence", None)
    return property_data


def disagrees_with_divar(property_data: dict) -> bool:
    """True when Divar called it personal and the words say otherwise.

    This is the case worth showing loudly: the listing arrived through a
    «شخصی» filter and should not have.
    """
    return bool(property_data.get("agency_suspected")) and \
        (property_data.get("advertiser_type") or "").lower() == "personal"
