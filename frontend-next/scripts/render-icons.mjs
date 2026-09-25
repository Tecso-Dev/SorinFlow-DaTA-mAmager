#!/usr/bin/env node
/**
 * Render the PWA icon set from one SVG mark, with Playwright's Chromium —
 * the same engine app/scraper already uses and the panel/portal e2e specs
 * screenshot with (see docs/phase4/routine-rules.md's preinstalled browser).
 *
 * Usage: node scripts/render-icons.mjs [svg-path] [--bg=#05050a]
 *
 * With no path, renders the site's current placeholder mark
 * (frontend/favicon.svg — the isometric house + infinity wordmark every page
 * already uses). The logo stream reruns this with the chosen final logo's
 * SVG once there is one; nothing else about the PWA setup needs to change.
 *
 * Output, committed to frontend-next/public/icons/:
 *   icon-192.png, icon-512.png       — full-bleed, purpose "any"
 *   maskable-512.png                 — the mark scaled to the center 80%
 *                                       ("safe zone") over a solid backdrop,
 *                                       so Android's round/squircle/etc. mask
 *                                       never crops the mark itself
 *   apple-touch-icon.png (180x180)   — iOS applies its own rounding; a
 *                                       full-bleed square is what Apple asks for
 *   favicon-32.png                   — browser tab / bookmark size
 */
import { createRequire } from "node:module";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const HERE = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(HERE, "..", "..");
const OUT_DIR = resolve(HERE, "..", "public", "icons");

const args = process.argv.slice(2).filter((a) => !a.startsWith("--"));
const bgArg = process.argv.find((a) => a.startsWith("--bg="));
const BG = bgArg ? bgArg.slice("--bg=".length) : "#05050a";
const svgPath = resolve(process.cwd(), args[0] ?? `${REPO_ROOT}/frontend/favicon.svg`);

if (!existsSync(svgPath)) {
  console.error(`no such SVG: ${svgPath}`);
  process.exit(1);
}
const svgMarkup = readFileSync(svgPath, "utf8");

// This box's node_modules has no local Playwright (frontend-next carries no
// browser-automation dependency of its own); the CLI's global install is the
// same one the e2e specs are told to use for screenshots.
function loadPlaywright() {
  for (const spec of ["playwright", "/opt/node22/lib/node_modules/playwright"]) {
    try {
      return require(spec);
    } catch {
      // try the next candidate
    }
  }
  throw new Error("Playwright not found — install it or set NODE_PATH to include it");
}

const TARGETS = [
  { file: "icon-192.png", size: 192, scale: 1, bg: null },
  { file: "icon-512.png", size: 512, scale: 1, bg: null },
  { file: "maskable-512.png", size: 512, scale: 0.8, bg: BG },
  { file: "apple-touch-icon.png", size: 180, scale: 1, bg: null },
  { file: "favicon-32.png", size: 32, scale: 1, bg: null },
];

function pageHtml({ size, scale, bg }) {
  const markSize = Math.round(size * scale);
  const offset = Math.round((size - markSize) / 2);
  return `<!DOCTYPE html><html><head><meta charset="utf-8" /><style>
    html,body { margin:0; padding:0; }
    #canvas { width:${size}px; height:${size}px; background:${bg ?? "transparent"}; position:relative; }
    #mark { position:absolute; inset-inline-start:${offset}px; top:${offset}px; width:${markSize}px; height:${markSize}px; }
    #mark svg { width:100%; height:100%; display:block; }
  </style></head><body>
    <div id="canvas"><div id="mark">${svgMarkup}</div></div>
  </body></html>`;
}

async function main() {
  const { chromium } = loadPlaywright();
  mkdirSync(OUT_DIR, { recursive: true });
  const browser = await chromium.launch();
  try {
    for (const target of TARGETS) {
      // deviceScaleFactor:1 — the canvas div is already sized to the exact
      // output pixels; a higher factor would render a correctly-antialiased
      // image at 2x those pixels, not a 2x-sharper image at the named size.
      const page = await browser.newPage({
        viewport: { width: target.size, height: target.size },
        deviceScaleFactor: 1,
      });
      await page.setContent(pageHtml(target));
      const el = await page.$("#canvas");
      const out = resolve(OUT_DIR, target.file);
      await el.screenshot({ path: out, omitBackground: target.bg === null });
      await page.close();
      console.log(`wrote ${out}`);
    }
  } finally {
    await browser.close();
  }

  // favicon.ico is served separately (app/main.py); this script only owns
  // the manifest/apple-touch icon set under public/icons/.
  writeFileSync(
    resolve(OUT_DIR, "SOURCE.txt"),
    `Rendered from ${svgPath.replace(REPO_ROOT + "/", "")} — rerun ` +
      `scripts/render-icons.mjs <path-to-final-logo.svg> once there is one.\n`,
  );
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
