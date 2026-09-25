#!/usr/bin/env node
// Renders the brand files from the running app with Playwright, so they are
// always drawn by the same SVG logo as the pages (components/brand/logo.tsx):
//
//   public/og.png            1200×630 link preview (from /brand-preview/og)
//   src/app/icon.png         512×512 favicon, transparent
//   src/app/apple-icon.png   180×180 iOS home-screen icon, opaque
//   public/icon-maskable.png 512×512 Android maskable icon (80% safe zone)
//   src/app/favicon.ico      16 + 32 px, PNG-in-ICO
//
// Run it after switching the logo concept or renaming the brand:
//   npm ci --prefix ../tests/e2e            (once: it brings Playwright)
//   npx next dev -p 3000 &                  (or any running build)
//   node scripts/brand-assets.mjs [http://127.0.0.1:3000]
// Playwright comes from the e2e project (tests/e2e); PW_CHROMIUM may point at
// a Chromium binary when Playwright's own download is not installed.

import { writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const app = join(here, "..");
const base = (process.argv[2] ?? "http://127.0.0.1:3000").replace(/\/$/, "");
const require = createRequire(join(app, "../tests/e2e/package.json"));
const { chromium } = require("@playwright/test");

const browser = await chromium.launch(process.env.PW_CHROMIUM ? { executablePath: process.env.PW_CHROMIUM } : {});

async function shoot(path, { width, height, scale = 1, transparent = false, dark = true }) {
  const page = await browser.newPage({
    viewport: { width, height },
    deviceScaleFactor: scale,
    colorScheme: dark ? "dark" : "light",
    reducedMotion: "reduce",
  });
  await page.goto(base + path, { waitUntil: "networkidle" });
  await page.evaluate(() => document.fonts.ready);
  if (transparent) {
    await page.evaluate(() => {
      document.documentElement.style.background = "transparent";
      document.body.style.background = "transparent";
    });
  }
  const png = await page.locator("[data-asset]").screenshot({ omitBackground: transparent });
  await page.close();
  return png;
}

/** An .ico holding PNG images (supported by every current browser). */
function ico(pngs) {
  const header = Buffer.alloc(6 + pngs.length * 16);
  header.writeUInt16LE(0, 0);
  header.writeUInt16LE(1, 2);
  header.writeUInt16LE(pngs.length, 4);
  let offset = header.length;
  pngs.forEach(({ size, data }, i) => {
    const e = 6 + i * 16;
    header.writeUInt8(size >= 256 ? 0 : size, e);
    header.writeUInt8(size >= 256 ? 0 : size, e + 1);
    header.writeUInt16LE(1, e + 4); // colour planes
    header.writeUInt16LE(32, e + 6); // bits per pixel
    header.writeUInt32LE(data.length, e + 8);
    header.writeUInt32LE(offset, e + 12);
    offset += data.length;
  });
  return Buffer.concat([header, ...pngs.map((p) => p.data)]);
}

const out = {
  og: join(app, "public/og.png"),
  icon: join(app, "src/app/icon.png"),
  apple: join(app, "src/app/apple-icon.png"),
  maskable: join(app, "public/icon-maskable.png"),
  favicon: join(app, "src/app/favicon.ico"),
};

await writeFile(out.og, await shoot("/brand-preview/og", { width: 1200, height: 630 }));
await writeFile(out.icon, await shoot("/brand-preview/icon?kind=favicon", { width: 512, height: 512, transparent: true }));
await writeFile(out.apple, await shoot("/brand-preview/icon?kind=apple", { width: 512, height: 512, scale: 180 / 512 }));
await writeFile(out.maskable, await shoot("/brand-preview/icon?kind=maskable", { width: 512, height: 512 }));
const small = await Promise.all(
  [16, 32].map(async (size) => ({
    size,
    data: await shoot("/brand-preview/icon?kind=favicon", { width: 512, height: 512, scale: size / 512, transparent: true }),
  })),
);
await writeFile(out.favicon, ico(small));

await browser.close();
for (const p of Object.values(out)) console.log("wrote", p.replace(app + "/", ""));
