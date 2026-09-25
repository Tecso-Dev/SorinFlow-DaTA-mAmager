# p4-r4-brand-landing — logo concepts and the landing page

Branch `p4-r4-brand-landing`, based on `origin/claude/phase-4-nextjs-frontend-ca3150`.

## A. Logo concepts

| Item | State |
|---|---|
| Three concepts in «شب نیلی» (indigo #6366f1 → violet #8b5cf6, cyan #22d3ee), isometric, one light (top left), white highlight edge | done — `src/components/brand/logos.tsx` |
| (الف) isometric gabled house whose lit long wall carries a raised S | done — `HouseMark` / `HouseLockup` |
| (ب) one ribbon folded four times: roof chevron, then the rest of an S; lit "/" runs over shaded "\" runs, with thickness | done — `RibbonMark` / `RibbonLockup` |
| (ج) three isometric towers of rising height inside a tilted orbit ring (back half behind, front half in front, a glowing satellite) | done — `TowersMark` / `TowersLockup` |
| `Mark` readable at 16px, `Lockup` with the wordmark from props (never hard-coded) | done (`name`, `tagline` props) |
| Pure SVG, presentation attributes, no `<style>`, gradient ids unique per instance (`useId`, sanitised) | done; the e2e checks no two gradients on the gallery share an id |
| `animated` prop: SMIL (a light running along the S / a sheen across the ribbon / the satellite orbiting), off under reduced motion | done (`useReducedMotion`) |
| `/brand-preview` (noindex): 16/32/64/128/256 on #05050a and #f5f6fb, maskable 80% safe zone (squircle + circle crop), lockup with the brand from `getSiteConfig()`, animated mark, mock browser tab (dark + light), mock phone home screen; titled «الف / ب / ج» with one Persian sentence each | done — `src/app/brand-preview/page.tsx`, `src/components/brand/gallery.tsx` |
| ONE switch point `src/components/brand/logo.tsx` (`Logo`, `LogoMark`, `CURRENT_CONCEPT = "house"`) | done — to change concept, edit that one line and re-run `scripts/brand-assets.mjs` |
| Letter-in-a-square marks replaced | panel shell (`app-shell.tsx` `BrandMark`), login page, landing nav/footer |

Extra (for the switch to be complete): the favicon, app icons and OG image are drawn from the same
logo. `/brand-preview/og` and `/brand-preview/icon?kind=favicon|apple|maskable` (noindex) render them;
`frontend-next/scripts/brand-assets.mjs` screenshots them with Playwright (from `tests/e2e`) into
`public/og.png` (1200×630), `src/components/brand/icons/icon.png` (512, transparent),
`src/components/brand/icons/apple-icon.png` (180) and `src/app/favicon.ico` (16+32, PNG-in-ICO,
replaces the framework default). The two PNGs are imported by `app/layout.tsx` (`metadata.icons`),
so they are served from `/_next/static/media/…`, which today's ingress already sends to the web pod:
the panel's icons work before the landing switch. `/brand-preview/icon?kind=maskable` exists for a
future web manifest; no maskable file is committed.
Run: `npm ci --prefix tests/e2e`, a running app on :3000, `node frontend-next/scripts/brand-assets.mjs`.
The OG card carries the brand, tagline and Latin name from settings at generation time, so re-run it
after a rename.

## B. Landing page (step 9)

| Item | State |
|---|---|
| `/` is the landing page (was a redirect to `/panel`), SSR | done — `src/app/page.tsx` (dynamic: nonce + live numbers) |
| Metadata, OpenGraph, Twitter card, canonical from `getSiteConfig()` (`seoTitle`/`seoDescription` override the copy) | done |
| JSON-LD (SoftwareApplication, Organization, WebSite, FAQPage) from settings | done; `<script type="application/ld+json">` with the request nonce, `<` escaped |
| `robots.txt`, `sitemap.xml` as Next metadata routes | done — `src/app/robots.ts`, `src/app/sitemap.ts` (`/portal` listed only while public sign-up is on) |
| OG image PNG from a committed Playwright script (not satori) | done — see above |
| Copy in `src/content/landing.ts`, adapted from `frontend/landing.html`, `{brand}` filled from settings | done |
| Sections: hero, features, how it works, AI, security, contact (phone/email/telegram from settings; an empty-state line when none is set) | done — `src/components/landing/landing.tsx` |
| CTAs to `/panel/login` and `/portal` (portal only while `/api/public/auth/status` says enabled, as the old page did) | done |
| Live numbers (`/api/public/stats`) with count-up, a tilting mock panel like the old page | done |
| WebGL «holographic particle nebula» ported to TypeScript + `three` from npm: ~14k points, custom shader, additive blending, formations morph with scroll, mouse parallax | done — `src/components/landing/nebula-scene.ts` |
| Formations in «شب نیلی»: nebula (hero) → isometric city skyline (features) → the 3D house (how it works, AI) → a key (security) → infinity (contact) | done (house now drawn mostly along its edges so it reads as a house) |
| Dynamic import when idle, paused off-screen (sticky canvas + IntersectionObserver) and in a hidden tab, DPR ≤ 1.75, 6k points on phones / ≤4 cores, static gradient without WebGL or under reduced motion | done — `src/components/landing/nebula.tsx` |
| Light theme | additive light vanishes on a light page, so the light theme switches the scene to normal blending with a deeper palette (follows the `dark` class live) |
| No eval, no `<style>` | none; three's ShaderMaterial compiles GLSL, which CSP does not govern |
| Motion on every section (reveal, count-up, tilt, looping bars, floating shield, typing dots), a 3D piece in each (iso badges, stair of extruded slabs, extruded shield, tilting cards) | done; all respect reduced motion |
| Responsive to 390px, no sideways scroll | done (checked iPhone 13 and Pixel 7; e2e asserts it) |
| Old `frontend/` and `k8s/` untouched | yes |

## Shared-file changes
- `src/app/layout.tsx`: `metadata.icons` points at the imported logo PNGs (see above).
- `src/lib/site.ts`: `getSiteConfig()` keeps the default for a missing, non-string or blank field,
  and never lets `brandName`, `brandNameLatin` or `domain` be blank. Before, an empty `domain` saved
  in the panel (the backend allows it) made `new URL("https://")` throw and took `/` down, and a
  `null` overwrote a default.
- `src/components/panel/app-shell.tsx`: `BrandMark` renders `<LogoMark size={36}>` instead of the
  first Latin letter in a gradient square (one import + one element).
- `src/app/panel/login/page.tsx`: the brand block is `<Logo name tagline>`.
- `src/app/favicon.ico`: regenerated from the logo.
- `package.json` / lock: `three@0.186.1`, `@types/three@0.186.0` (exact), as allowed.
- No backend changes, no migrations.

## Tests
- `npx tsc --noEmit`, `npm run lint`, `npx next build`: clean.
- E2E `tests/e2e/next/p4-r4-brand-landing.spec.js` (8 tests): against the production build
  (`next start`, strict CSP) — desktop 8/8, android 8/8, iphone 8/8. Also 24/24 against `next dev`.
- Whole new-panel suite on the production build, three projects: 102 passed, 6 failed. The 6 are
  `dashboard.spec.js` «owner sees the whole office» and «the dark theme passes the same
  accessibility check» (axe: `aria-progressbar-name`, and tab-button contrast in dark) on each
  project. They fail the same way on an untouched build of the base branch against the same
  backend, so they were already failing before this branch.
- An independent adversarial review of the whole diff found: (1) icons on paths the ingress sends
  to the backend, (2) an empty `domain` setting causing a 500 on `/`, (3) hydration errors for
  reduced-motion visitors (hero `initial` and the animated logo branched on `useReducedMotion`),
  (4) a WebGL context leaked per client navigation, (5) the OG image having the brand baked in,
  (6) an unused maskable PNG, (7) `/portal` linked with `next/link` when it is not a Next route.
  Fixed: 1 (icons now under `/_next/static`), 2 (`getSiteConfig`), 3 (markup is the same on the
  server and on the first client render, the global `MotionConfig reducedMotion="user"` skips the
  transforms, the logo animates only after hydration; the e2e now watches the reduced-motion page
  for errors), 4 (`forceContextLoss()` on dispose), 6 (dropped), 7 (plain `<a>`). 5 is by design
  (the task asked for a committed Playwright script, not satori): re-run
  `scripts/brand-assets.mjs` after a rename.
- **WebKit not run here**: `playwright install webkit` is blocked by this environment's network
  policy (cdn.playwright.dev 403), so the iPhone project ran on Chromium with the iPhone profile.
  Please run `E2E_WEBKIT=1 ... p4-r4` on the Mac. The WebGL test tolerates a WebKit without WebGL
  (it then only checks the gradient fallback).
- Looked at: /brand-preview desktop dark; / desktop dark + light, iPhone light, every formation
  after it settles; login (iPhone light) and the shell (desktop dark).

## For the coordinator
New paths the ingress must send to the **web pod** (Next.js) instead of the backend / old frontend:
- `/` (the landing page; today the old `frontend/landing.html` serves it — switch together)
- `/robots.txt`, `/sitemap.xml`
- `/og.png`
- `/favicon.ico` (Next adds a `?<hash>` query; the path is what matters). The PNG icons need nothing:
  they live under `/_next/static`.
- `/brand-preview`, `/brand-preview/og`, `/brand-preview/icon` (noindex; may stay web-pod-only or be
  blocked at the edge after the owner picks a logo)
- `/_next/*` as for the panel.
The landing page calls `/api/public/stats`, `/api/public/auth/status` and `/api/public/site`
server-side through `BACKEND_INTERNAL_URL` (no new env vars). The old landing page's links to
`/dashboard/` became `/panel/login`. The old page loaded the Kavenegar web-push SDK from a CDN; the
strict CSP forbids that, so the new page does not (flag it to the owner if web push is still wanted).
