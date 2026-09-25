#!/usr/bin/env python3
"""
Render the isometric hero PNGs email_templates.py puts above every message.

One small isometric scene per template family (HERO_FILES in
app/services/email_templates.py): an SVG built in this script, screenshotted
by Playwright's Chromium (already a backend dependency — app/scraper uses it
the same way) rather than rasterised with a separate SVG library, so there is
one rendering engine for every image this project produces from SVG,
frontend icons included (frontend-next/scripts/render-icons.mjs).

Output: app/static/email_assets/hero-<family>.png, 1200x420 (2x a 600x210
display — see email_templates.HERO_FILES) with the card's own #0a0a10
background baked in, since a transparent PNG over a light client background
(some clients ignore "prefers dark") would show gaps around the art.

Usage: python scripts/render_email_heroes.py [family ...]
No family named renders all of them. Commit the PNGs — this script does not
run at request time, only when the art changes.
"""
import asyncio
import os
import sys
from pathlib import Path

from playwright.async_api import async_playwright

OUT_DIR = Path(__file__).resolve().parent.parent / "app" / "static" / "email_assets"
WIDTH, HEIGHT = 1200, 420

BG = "#0a0a10"
LINE = "#1c1c22"
VIOLET = "#a78bfa"
PINK = "#f0a6ff"
CYAN = "#67e8f9"
GOLD = "#fcd34d"
SUCCESS = "#10b981"
DIM = "#8f96a8"


def _iso_platform(cx: float, cy: float, rx: float, ry: float, top: str, left: str, right: str,
                  depth: float = 26) -> str:
    """A diamond-topped isometric block: top face + two shaded side faces."""
    top_pts = f"{cx},{cy - ry} {cx + rx},{cy} {cx},{cy + ry} {cx - rx},{cy}"
    left_pts = f"{cx - rx},{cy} {cx},{cy + ry} {cx},{cy + ry + depth} {cx - rx},{cy + depth}"
    right_pts = f"{cx},{cy + ry} {cx + rx},{cy} {cx + rx},{cy + depth} {cx},{cy + ry + depth}"
    return (f'<polygon points="{left_pts}" fill="{left}" />'
            f'<polygon points="{right_pts}" fill="{right}" />'
            f'<polygon points="{top_pts}" fill="{top}" />')


def _glow(cx: float, cy: float, r: float, colour: str, opacity: float = 0.35) -> str:
    return (f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{colour}" opacity="{opacity}" '
            f'filter="url(#blur)" />')


def _defs() -> str:
    return f"""
<defs>
  <linearGradient id="grad" x1="0%" y1="0%" x2="100%" y2="100%">
    <stop offset="0%" stop-color="{VIOLET}" />
    <stop offset="45%" stop-color="{PINK}" />
    <stop offset="100%" stop-color="{CYAN}" />
  </linearGradient>
  <filter id="blur" x="-50%" y="-50%" width="200%" height="200%">
    <feGaussianBlur stdDeviation="40" />
  </filter>
</defs>
<rect width="{WIDTH}" height="{HEIGHT}" fill="{BG}" />
{_glow(WIDTH * 0.22, HEIGHT * 0.3, 180, VIOLET, 0.22)}
{_glow(WIDTH * 0.8, HEIGHT * 0.75, 200, CYAN, 0.16)}
"""


def _floor_dots() -> str:
    """A faint isometric grid, the same «کف شیشه‌ای» floor viz.tsx uses under
    3D panel illustrations, scaled down for a hero banner."""
    dots = []
    for row in range(-3, 4):
        for col in range(-5, 6):
            x = WIDTH / 2 + (col - row) * 34
            y = HEIGHT * 0.86 + (col + row) * 17
            if 40 < x < WIDTH - 40 and HEIGHT * 0.55 < y < HEIGHT - 10:
                dots.append(f'<circle cx="{x:.0f}" cy="{y:.0f}" r="1.6" fill="{LINE}" />')
    return "".join(dots)


def scene_auth() -> str:
    cx, cy = WIDTH / 2, HEIGHT * 0.46
    body = _iso_platform(cx, cy, 150, 68, "#15151d", "#0d0d13", "#101017")
    lock = f"""
<g transform="translate({cx},{cy - 34})">
  <rect x="-46" y="-6" width="92" height="72" rx="16" fill="url(#grad)" />
  <rect x="-46" y="-6" width="92" height="72" rx="16" fill="#0a0a12" opacity="0.08" />
  <path d="M -26 -6 v -26 a 26 26 0 0 1 52 0 v 26" fill="none" stroke="url(#grad)"
        stroke-width="10" stroke-linecap="round" />
  <circle cx="0" cy="26" r="9" fill="{BG}" />
  <rect x="-4" y="26" width="8" height="20" rx="4" fill="{BG}" />
</g>"""
    return body + lock


def scene_welcome() -> str:
    cx, cy = WIDTH / 2, HEIGHT * 0.46
    body = _iso_platform(cx, cy, 150, 68, "#15151d", "#0d0d13", "#101017")
    door = f"""
<g transform="translate({cx},{cy - 70})">
  <rect x="-56" y="-4" width="112" height="126" rx="10" fill="#101017" stroke="url(#grad)" stroke-width="4" />
  <path d="M -50 122 L -6 96 L -6 -34 L -50 -8 Z" fill="url(#grad)" opacity="0.35" />
  <g transform="translate(-6,-34) skewY(-24)">
    <rect x="0" y="0" width="44" height="130" fill="url(#grad)" />
    <circle cx="34" cy="66" r="5" fill="{BG}" />
  </g>
</g>"""
    return body + door


def scene_decision() -> str:
    cx, cy = WIDTH / 2, HEIGHT * 0.46
    body = _iso_platform(cx, cy, 150, 68, "#15151d", "#0d0d13", "#101017")
    card = f"""
<g transform="translate({cx},{cy - 42})">
  <rect x="-58" y="-30" width="116" height="132" rx="14" fill="{BG}" stroke="url(#grad)" stroke-width="4" />
  <rect x="-38" y="-8" width="76" height="10" rx="5" fill="{DIM}" opacity="0.5" />
  <rect x="-38" y="14" width="56" height="10" rx="5" fill="{DIM}" opacity="0.35" />
  <circle cx="0" cy="58" r="34" fill="url(#grad)" />
  <path d="M -14 58 l 10 12 l 22 -26" fill="none" stroke="{BG}" stroke-width="8"
        stroke-linecap="round" stroke-linejoin="round" />
</g>"""
    return body + card


def scene_request() -> str:
    cx, cy = WIDTH / 2, HEIGHT * 0.46
    body = _iso_platform(cx, cy, 150, 68, "#15151d", "#0d0d13", "#101017")
    folder = f"""
<g transform="translate({cx - 18},{cy - 30})">
  <path d="M -60 20 h 40 l 14 16 h 66 v 64 h -120 Z" fill="url(#grad)" />
  <path d="M -60 34 h 120 v 66 h -120 Z" fill="{BG}" opacity="0.18" />
</g>
<g transform="translate({cx + 58},{cy - 4}) rotate(35)">
  <circle cx="0" cy="0" r="26" fill="none" stroke="{CYAN}" stroke-width="9" />
  <line x1="18" y1="18" x2="42" y2="42" stroke="{CYAN}" stroke-width="10" stroke-linecap="round" />
</g>"""
    return body + folder


def scene_notification() -> str:
    cx, cy = WIDTH / 2, HEIGHT * 0.46
    body = _iso_platform(cx, cy, 150, 68, "#15151d", "#0d0d13", "#101017")
    bell = f"""
<g transform="translate({cx},{cy - 46})">
  <path d="M 0 -40 C 34 -40 44 -10 44 18 L 54 40 L -54 40 L -44 18 C -44 -10 -34 -40 0 -40 Z"
        fill="url(#grad)" />
  <circle cx="0" cy="54" r="12" fill="{GOLD}" />
</g>"""
    return body + bell


FAMILIES = {
    "auth": scene_auth,
    "welcome": scene_welcome,
    "decision": scene_decision,
    "request": scene_request,
    "notification": scene_notification,
}


def _svg(scene: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
            f'viewBox="0 0 {WIDTH} {HEIGHT}">{_defs()}{_floor_dots()}{scene}</svg>')


# This box's preinstalled Chromium is a newer revision than the Playwright
# 1.41 pin in requirements.txt expects; the fixed path is the one every
# script in this repo (and the frontend's own render-icons.mjs) uses instead
# of letting Playwright try to download its pinned build. Resolved at import
# time — a Path check is a blocking call an async function must not make.
_CANDIDATE_CHROMIUM = os.environ.get("PW_CHROMIUM", "/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
EXECUTABLE_PATH = _CANDIDATE_CHROMIUM if Path(_CANDIDATE_CHROMIUM).exists() else None


async def render(family: str) -> None:
    scene = FAMILIES[family]()
    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8" /><style>
      html,body {{ margin:0; padding:0; background:{BG}; }}
      svg {{ display:block; }}
    </style></head><body>{_svg(scene)}</body></html>"""
    await asyncio.to_thread(OUT_DIR.mkdir, parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=EXECUTABLE_PATH)
        page = await browser.new_page(viewport={"width": WIDTH, "height": HEIGHT})
        await page.set_content(html)
        out = OUT_DIR / f"hero-{family}.png"
        await page.screenshot(path=str(out))
        await browser.close()
    print(f"wrote {out}")


async def main() -> None:
    wanted = sys.argv[1:] or list(FAMILIES)
    unknown = [f for f in wanted if f not in FAMILIES]
    if unknown:
        raise SystemExit(f"unknown hero family: {', '.join(unknown)} (know: {', '.join(FAMILIES)})")
    for family in wanted:
        await render(family)


if __name__ == "__main__":
    asyncio.run(main())
