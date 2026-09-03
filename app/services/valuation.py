"""
Is this listing priced where the neighbourhood is priced?

The idea is Zillow's and Redfin's: value a property against comparable sales
nearby. What is worth copying from them is not the neural network — it is the
discipline that comes with it. Zillow publishes a median error rate (1.9% on
listed homes, 7.5% on unlisted) precisely because a valuation without a stated
confidence is a guess wearing a suit.

So this uses a median of comparable listings, and it refuses to answer when
the comparables are too few to mean anything. That refusal is the feature. A
number derived from three listings, presented the same way as one derived from
three hundred, is how a tool teaches its user to distrust it.

Median rather than mean, throughout: one villa priced at forty billion in a
street of apartments drags a mean far enough to make every neighbour look
cheap. The median does not move.
"""
from statistics import median
from typing import Any, Dict, Iterable, List, Optional, Sequence

# Below this many comparables there is no answer — not a cautious answer, no
# answer. Chosen because a median of four is decided by two listings, and two
# listings in a Persian city district is somebody's asking price, not a market.
MIN_COMPARABLES = 8

# Above this, the neighbourhood is well enough described to lean on.
STRONG_COMPARABLES = 25

# How far from the median counts as notable. Below this a listing is simply
# priced normally, and saying so about every property would drown the ones
# that are not.
NOTABLE_PCT = 12.0

# Sanity bounds on a price per square metre, in toman. A listing outside these
# is a parsing failure — a total price read as a per-metre figure, an area of
# zero — and letting it into the median corrupts the benchmark for the whole
# district, which is worse than dropping it.
PPM_FLOOR = 1_000_000
PPM_CEILING = 5_000_000_000


def price_per_meter(prop) -> Optional[int]:
    """The per-metre figure, derived when Divar did not supply one.

    Returns None rather than guessing. A listing with no area cannot have a
    per-metre price, and inventing one from the total would silently place a
    500-metre villa in the same bucket as a 50-metre flat.
    """
    stored = getattr(prop, "price_per_meter", None)
    if stored and PPM_FLOOR <= stored <= PPM_CEILING:
        return int(stored)

    total = getattr(prop, "total_price", None) or getattr(prop, "price", None)
    area = getattr(prop, "area", None) or getattr(prop, "built_area", None)
    if not total or not area or area <= 0:
        return None

    derived = int(total / area)
    if not (PPM_FLOOR <= derived <= PPM_CEILING):
        return None
    return derived


def bucket_key(prop) -> Optional[tuple]:
    """What counts as «comparable»: same city, same district, same kind of deal.

    District rather than city alone. Two apartments in the same city with a
    3× price gap between neighbourhoods are not comparables, and pooling them
    produces a median that describes nowhere.

    None when the listing is not placed well enough to compare — an unnamed
    district cannot be benchmarked, and quietly falling back to the city
    average would attach a confident number to the listings we know least
    about.
    """
    city = (getattr(prop, "city_name", None) or "").strip()
    district = (getattr(prop, "district", None) or "").strip()
    listing = (getattr(prop, "listing_type", None) or "").strip()
    if not city or not district:
        return None
    return (city, district, listing or "unknown")


def benchmark(values: Sequence[int]) -> Optional[Dict[str, Any]]:
    """The district's price per metre, or None when there is not enough to say."""
    clean = sorted(int(v) for v in values
                   if v and PPM_FLOOR <= int(v) <= PPM_CEILING)
    if len(clean) < MIN_COMPARABLES:
        return None

    mid = median(clean)
    return {
        "median_ppm": int(mid),
        "sample": len(clean),
        "low": clean[len(clean) // 10],          # p10
        "high": clean[-(len(clean) // 10 + 1)],  # p90
        "confidence": "good" if len(clean) >= STRONG_COMPARABLES else "thin",
    }


def assess(prop, bench: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Where this listing sits against its district.

    Always returns a dict, always with a `verdict`. «unknown» is a real
    verdict here and the most common one on a young database — the caller
    renders it as «—» rather than as «priced normally», because those are
    very different statements and only one of them is supported.
    """
    ppm = price_per_meter(prop)
    if ppm is None:
        return {"verdict": "unknown", "reason": "no_price_per_meter",
                "ppm": None, "delta_pct": None}
    if not bench:
        return {"verdict": "unknown", "reason": "not_enough_comparables",
                "ppm": ppm, "delta_pct": None}

    mid = bench["median_ppm"]
    delta = round((ppm - mid) / mid * 100, 1)

    if delta <= -NOTABLE_PCT:
        verdict = "under"
    elif delta >= NOTABLE_PCT:
        verdict = "over"
    else:
        verdict = "typical"

    return {
        "verdict": verdict,
        "reason": None,
        "ppm": ppm,
        "median_ppm": mid,
        "delta_pct": delta,
        "sample": bench["sample"],
        "confidence": bench["confidence"],
    }


VERDICT_FA = {
    "under":   "زیر قیمت منطقه",
    "over":    "بالای قیمت منطقه",
    "typical": "هم‌قیمت منطقه",
    "unknown": "قابل سنجش نیست",
}

REASON_FA = {
    "no_price_per_meter": "متراژ یا قیمت این آگهی کامل نیست",
    "not_enough_comparables": "آگهی مشابه کافی در این محله ثبت نشده",
}


def describe(a: Dict[str, Any]) -> str:
    """One line, in the words the panel shows."""
    if a["verdict"] == "unknown":
        return REASON_FA.get(a.get("reason"), VERDICT_FA["unknown"])
    if a["verdict"] == "typical":
        return f"{VERDICT_FA['typical']} (±{abs(a['delta_pct'])}٪)"
    word = "کمتر" if a["verdict"] == "under" else "بیشتر"
    line = f"{abs(a['delta_pct'])}٪ {word} از میانهٔ محله"
    if a.get("confidence") == "thin":
        # Say it on the same line as the claim. A caveat one card away from
        # the number it qualifies is a caveat nobody reads.
        line += f" — بر پایهٔ فقط {a['sample']} آگهی مشابه"
    return line


def price_moves(prop) -> List[Dict[str, Any]]:
    """The recorded price trail, newest first, with the direction worked out."""
    trail = getattr(prop, "price_history", None) or []
    out = []
    for entry in trail:
        if not isinstance(entry, dict):
            continue
        to = entry.get("total_price") or entry.get("price")
        frm = (entry.get("from") or {}).get("total_price") or \
              (entry.get("from") or {}).get("price")
        if to is None or frm is None:
            continue
        out.append({
            "at": entry.get("at"),
            "from": int(frm),
            "to": int(to),
            "delta_pct": round((to - frm) / frm * 100, 1) if frm else None,
            "direction": "down" if to < frm else "up",
        })
    out.reverse()
    return out
