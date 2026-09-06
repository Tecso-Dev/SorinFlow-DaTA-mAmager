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

Negation is the whole difficulty. «بدون کمیسیون» and «بدون واسطه» are what a
private seller writes, and a matcher that only looks for «کمیسیون» reads them
as the opposite of what they say.
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

# Words that turn a phrase into its opposite when they come just before it.
# «بدون کمیسیون» is a private seller's boast, not an agency's disclosure.
NEGATORS = ("بدون", "بدونِ", "بی", "نه", "غیر")

# How far back to look for one. Long enough for «بدون هیچ کمیسیون», short
# enough that a negator two sentences earlier does not reach.
_NEGATION_WINDOW = 18

# Persian and Arabic-Indic digits, plus the zero-width non-joiner, all of
# which appear inside otherwise identical phrases.
_ZWNJ = "‌"


def _flatten(text: str) -> str:
    """One spelling, so one pattern can find it."""
    if not text:
        return ""
    out = text.replace(_ZWNJ, " ").replace("ي", "ی").replace("ك", "ک")
    return re.sub(r"\s+", " ", out).strip()


def _negated(haystack: str, at: int) -> bool:
    """Is the match at `at` preceded by something that reverses it?"""
    window = haystack[max(0, at - _NEGATION_WINDOW):at]
    return any(n in window for n in NEGATORS)


def detect(*texts: Optional[str]) -> Tuple[bool, Optional[str]]:
    """(looks like an agency, the phrase that said so).

    Every text is searched — a title can give it away where a description
    does not, and the other way round.
    """
    for phrase in AGENCY_PHRASES:
        for text in texts:
            flat = _flatten(text or "")
            if not flat:
                continue
            start = 0
            while True:
                at = flat.find(phrase, start)
                if at < 0:
                    break
                if not _negated(flat, at):
                    return True, phrase
                start = at + 1
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
