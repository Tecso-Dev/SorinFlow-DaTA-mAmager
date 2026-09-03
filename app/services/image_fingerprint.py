"""
Recognising the same property listed twice.

A marketplace scraper has a problem the big portals do not. Zillow and
Rightmove receive each listing once, from the agent who holds it. We read a
marketplace where one apartment is posted by three agencies at three prices,
plus once more by the owner — four rows, one flat, and the CRM treats them as
four opportunities.

The images give it away, because everyone reposts the same photos. So: a
perceptual hash per image, and two listings that share enough images are the
same property.

The naive version of this is worse than not having it, and most of this file
is the difference:

  * **Boilerplate.** An agency watermark, a location map, a floor plan, the
    exterior of a tower shared by forty flats — these match everything and
    would merge a whole building into one listing. Any hash that turns up on
    more than a handful of distinct properties is not evidence, and is
    dropped before matching rather than weighed.

  * **One image is not enough.** Two listings in the same tower legitimately
    share the exterior shot. A single match is a hint; it becomes a claim
    only with a second matching image, or with the numbers agreeing — the
    same area, the same room count, the same district.

Getting this wrong merges two different flats, which is worse than missing a
duplicate: a missed duplicate wastes a phone call, a wrong merge deletes a
property from somebody's pipeline.
"""
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

# Bits of a 64-bit hash that may differ and still be the same picture.
# Divar re-encodes to webp at several sizes, which moves a few bits; a genuine
# re-upload of the same photo lands well inside this. Set it wider and
# different rooms in the same style start matching.
HASH_DISTANCE = 6

# A hash on more than this many distinct properties is furniture — a logo, a
# map, a building exterior — not an identifying photograph.
BOILERPLATE_AFTER = 4

# Matching images needed before two listings are called the same property,
# absent any corroboration from the numbers.
MATCHES_FOR_CERTAINTY = 2

# How close the areas must be to corroborate a single image match. Divar
# rounds, and agents disagree by a metre or two on the same flat.
AREA_TOLERANCE_PCT = 4.0


def dhash(image, size: int = 8) -> int:
    """A 64-bit difference hash of a PIL image.

    Difference hashing rather than average hashing: it encodes whether each
    pixel is brighter than its right-hand neighbour, so it survives the
    re-encoding, rescaling and mild brightness shifts that a marketplace
    applies to every upload, while still separating two genuinely different
    rooms.
    """
    from PIL import Image

    small = image.convert("L").resize((size + 1, size), Image.LANCZOS)
    px = list(small.getdata())

    bits = 0
    for row in range(size):
        base = row * (size + 1)
        for col in range(size):
            bits <<= 1
            if px[base + col] > px[base + col + 1]:
                bits |= 1
    return bits


def hamming(a: int, b: int) -> int:
    """How many bits differ."""
    return bin(a ^ b).count("1")


def near(a: int, b: int, threshold: int = HASH_DISTANCE) -> bool:
    return hamming(a, b) <= threshold


def boilerplate(hash_lists: Iterable[Sequence[int]],
                *, limit: int = BOILERPLATE_AFTER) -> Set[int]:
    """Hashes that appear on too many distinct properties to identify one.

    Counted per property, not per image: a listing that repeats its own logo
    five times has still only used it once as evidence, and counting the
    repeats would condemn a hash that appears on a single property.
    """
    seen: Counter = Counter()
    for hashes in hash_lists:
        for h in set(hashes or []):
            seen[h] += 1
    return {h for h, n in seen.items() if n > limit}


def shared_images(a: Sequence[int], b: Sequence[int], *,
                  ignore: Optional[Set[int]] = None,
                  threshold: int = HASH_DISTANCE) -> int:
    """How many of a's images have a near match in b.

    Each image on either side is used at most once, so a listing that posts
    the same photograph four times cannot manufacture four matches.
    """
    ignore = ignore or set()
    left = [h for h in (a or []) if h not in ignore]
    right = [h for h in (b or []) if h not in ignore]

    used: Set[int] = set()
    matches = 0
    for h in left:
        for i, other in enumerate(right):
            if i in used:
                continue
            if near(h, other, threshold):
                used.add(i)
                matches += 1
                break
    return matches


def _numbers_agree(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """Do the listed facts corroborate one image match?

    Requires the numbers to be present on both sides. Two listings that are
    both missing an area do not "agree" about it — that is absence, and
    treating it as agreement is how a single shared building exterior becomes
    a merged pair.
    """
    area_a, area_b = a.get("area"), b.get("area")
    if not area_a or not area_b:
        return False
    if abs(area_a - area_b) / max(area_a, area_b) * 100 > AREA_TOLERANCE_PCT:
        return False

    rooms_a, rooms_b = a.get("rooms"), b.get("rooms")
    if rooms_a is not None and rooms_b is not None and rooms_a != rooms_b:
        return False

    d_a = (a.get("district") or "").strip()
    d_b = (b.get("district") or "").strip()
    if d_a and d_b and d_a != d_b:
        return False

    return True


def compare(a: Dict[str, Any], b: Dict[str, Any], *,
            ignore: Optional[Set[int]] = None) -> Dict[str, Any]:
    """Are these two listings the same property?

    Returns a verdict and the reason for it, always. «duplicate», «likely» and
    «different» are three answers, not two with a threshold — the middle one
    is a listing for a human to glance at, not one to merge.
    """
    matches = shared_images(a.get("hashes") or [], b.get("hashes") or [],
                            ignore=ignore)

    if matches == 0:
        return {"verdict": "different", "shared_images": 0, "reason": None}

    if matches >= MATCHES_FOR_CERTAINTY:
        return {"verdict": "duplicate", "shared_images": matches,
                "reason": "multiple_images"}

    if _numbers_agree(a, b):
        return {"verdict": "duplicate", "shared_images": matches,
                "reason": "image_and_attributes"}

    # One shared image and nothing to back it up. Two flats in the same tower
    # share the exterior shot, and that is the commonest false positive there
    # is — so it is reported as worth a look, not as a fact.
    return {"verdict": "likely", "shared_images": matches,
            "reason": "single_image_only"}


VERDICT_FA = {
    "duplicate": "همان ملک",
    "likely":    "احتمالاً همان ملک",
    "different": "ملک دیگری است",
}

REASON_FA = {
    "multiple_images":      "چند عکس مشترک",
    "image_and_attributes": "عکس مشترک، و متراژ و اتاق و محله هم می‌خواند",
    "single_image_only":    "فقط یک عکس مشترک — ممکن است نمای همان ساختمان باشد",
}


def describe(result: Dict[str, Any]) -> str:
    v = VERDICT_FA.get(result["verdict"], result["verdict"])
    r = REASON_FA.get(result.get("reason"))
    return f"{v} — {r}" if r else v
