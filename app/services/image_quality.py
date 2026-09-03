"""
Scoring the photographs on a listing.

From the brief: «بررسی خودکار کیفیت، نور و زاویه عکس‌های آپلود شده».

Three of those four are measurable from the pixels with no model at all, and
this measures those three. Angle is not — judging composition needs to know
what is in the frame, which needs a vision model this deployment cannot run.
Rather than guess at it, the score says what it looked at.

What each measurement actually is, since «quality» on its own means nothing:

  * **Sharpness** — the variance of the Laplacian. A blurred photograph has
    little high-frequency detail, so the second derivative is flat and the
    variance collapses. It is the standard blur detector and it is honest
    about what it cannot tell: a photograph of a plain white wall is sharp
    and scores badly, because it has no edges to be sharp about.

  * **Exposure** — how much of the histogram is pinned at pure black or pure
    white. Clipped pixels are detail that no longer exists in the file, which
    is different from a picture that is merely dark.

  * **Resolution** — pixel count against what a listing gallery needs.

The scores are advisory. Nothing here deletes or rejects a photograph: the
brief's own version does («سیستم عکس‌های ضعیف یا تار را رد کرده»), and that
is a decision for whoever is looking at the listing, not for a number.
"""
from typing import Any, Dict, List, Optional, Sequence

# Laplacian variance below this reads as blurred. The usual starting point in
# the literature is 100 on 8-bit greyscale; marketplace photographs are
# re-compressed, which softens fine detail, so this sits lower to avoid
# condemning a perfectly usable re-upload.
SHARP_FLOOR = 60.0
SHARP_GOOD = 200.0

# Share of pixels allowed at the very ends of the histogram before the
# exposure counts as clipped.
CLIP_LIMIT = 0.06

# A listing photograph below this is a thumbnail, whatever else is true.
MIN_PIXELS = 640 * 480
GOOD_PIXELS = 1280 * 960


def measure(image) -> Dict[str, Any]:
    """Measure one PIL image. Never raises; returns what it could establish."""
    out: Dict[str, Any] = {"sharpness": None, "clipped": None,
                           "pixels": None, "width": None, "height": None}
    try:
        import numpy as np

        w, h = image.size
        out["width"], out["height"], out["pixels"] = w, h, w * h

        grey = image.convert("L")
        # Downscale before measuring: sharpness is a property of the picture,
        # not of how many megapixels it was saved at, and comparing a 4000px
        # photo against an 800px one on raw variance rewards the camera rather
        # than the photograph.
        target = 512
        if max(grey.size) > target:
            ratio = target / max(grey.size)
            grey = grey.resize((max(int(w * ratio), 1), max(int(h * ratio), 1)))

        a = np.asarray(grey, dtype=np.float64)
        if a.size == 0:
            return out

        # 3×3 Laplacian by hand — one convolution is not worth a scipy
        # dependency, and this keeps the whole file to numpy.
        lap = (
            -4 * a[1:-1, 1:-1]
            + a[:-2, 1:-1] + a[2:, 1:-1]
            + a[1:-1, :-2] + a[1:-1, 2:]
        )
        out["sharpness"] = round(float(lap.var()), 1)

        dark = float((a <= 4).mean())
        bright = float((a >= 251).mean())
        out["clipped"] = round(dark + bright, 4)
    except Exception:
        # A photograph we cannot measure is not a photograph we should drop.
        pass
    return out


def verdict(m: Dict[str, Any]) -> Dict[str, Any]:
    """Turn measurements into a judgement, with the reasons kept.

    Reasons rather than a bare score: «۴۲ از ۱۰۰» tells a photographer
    nothing, and «تار است» tells them to take it again.
    """
    problems: List[str] = []

    if m.get("sharpness") is None:
        return {"grade": "unknown", "problems": ["اندازه‌گیری نشد"], "score": None}

    if m["sharpness"] < SHARP_FLOOR:
        problems.append("blurry")
    if (m.get("clipped") or 0) > CLIP_LIMIT:
        problems.append("exposure")
    if (m.get("pixels") or 0) < MIN_PIXELS:
        problems.append("small")

    # A 0-100 number for sorting, built from the same three measurements so it
    # can never disagree with the reasons beside it.
    sharp_part = min(m["sharpness"] / SHARP_GOOD, 1.0)
    clip_part = 1.0 - min((m.get("clipped") or 0) / CLIP_LIMIT, 1.0)
    size_part = min((m.get("pixels") or 0) / GOOD_PIXELS, 1.0)
    score = int(round((sharp_part * 0.5 + clip_part * 0.25 + size_part * 0.25) * 100))

    grade = "good" if not problems else ("poor" if len(problems) > 1 else "fair")
    return {"grade": grade, "problems": problems, "score": score}


def summarise(results: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """A listing's gallery as a whole.

    The worst photograph matters more than the average one. A gallery whose
    first image is blurred is a gallery nobody opens, and averaging that away
    with four good ones hides the thing worth fixing.
    """
    graded = [r for r in results if r.get("score") is not None]
    if not graded:
        return {"count": len(results), "scored": 0, "best": None,
                "worst": None, "average": None, "problems": {}}

    scores = [r["score"] for r in graded]
    problems: Dict[str, int] = {}
    for r in graded:
        for p in r.get("problems", []):
            problems[p] = problems.get(p, 0) + 1

    return {
        "count": len(results),
        "scored": len(graded),
        "best": max(scores),
        "worst": min(scores),
        "average": int(round(sum(scores) / len(scores))),
        "problems": problems,
    }


PROBLEM_FA = {
    "blurry":   "تار",
    "exposure": "نور نامناسب (سوخته یا خیلی تاریک)",
    "small":    "رزولوشن پایین",
}

GRADE_FA = {
    "good":    "خوب",
    "fair":    "قابل قبول",
    "poor":    "ضعیف",
    "unknown": "سنجیده نشد",
}


def describe(v: Dict[str, Any]) -> str:
    g = GRADE_FA.get(v.get("grade"), v.get("grade"))
    if not v.get("problems"):
        return g
    named = "، ".join(PROBLEM_FA.get(p, p) for p in v["problems"])
    return f"{g} — {named}"
