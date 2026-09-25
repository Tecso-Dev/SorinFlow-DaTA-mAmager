# p4-r5-pwa-email

Branch: `p4-r5-pwa-email`, based on `origin/claude/phase-4-nextjs-frontend-ca3150`. No
sub-worktrees were used — all work landed directly on this branch.

## A. PWA — done for the panel, scaffolded for the portal

The panel (`frontend-next/src/app/panel/`) is fully live:

- `src/app/panel/manifest.webmanifest/route.ts` — built from `getSiteConfig()`
  (brand/tagline/domain, nothing hard-coded). **Not** a `manifest.ts` file: Next's
  manifest file convention only resolves at the app **root**
  (`node_modules/next/dist/lib/metadata/is-metadata-route.js` anchors that regex,
  unlike `icon.tsx`'s), so a per-scope manifest needs a plain route handler named
  for the literal URL, with `metadata.manifest` in the layout pointing at it
  explicitly (Next does not auto-link a route handler the way it auto-links the
  special file convention). Scope `/panel/`, start_url `/panel`, dir `rtl`, lang
  `fa`, dark theme colours (`#05050a`, the same token `.dark` uses), 3 icons
  (192/512/maskable-512), 3 shortcuts (dashboard/CRM/properties).
- `public/sw.js` — plain JS, one script registered twice (panel scope, portal
  scope). Precaches the two offline pages and the icon set; navigations are
  network-first with a cached offline fallback picked by URL prefix;
  `/_next/static/`, `/icons/`, `/fonts/` are cache-first in a versioned cache;
  `/api/`, `/images/`, `/downloads/`, `/dashboard/` are never touched — the old
  panel's own worker (`frontend/sw.js`) lives at `/dashboard/sw.js` and this one
  does not shadow it; `skipWaiting()` + `clients.claim()`; old `sf-pwa-*` caches
  deleted on activate.
- `src/components/pwa/register.tsx` — production-only registration component,
  takes a `scope` prop; mounted once in `src/app/panel/layout.tsx` (new file,
  wraps both `/panel/login` and the authenticated `(app)` group, so install and
  the offline fallback work before sign-in too).
- `src/components/pwa/install-menu-item.tsx` — listens for `beforeinstallprompt`
  and calls `.prompt()` directly on Chrome/Edge/Android; on iOS Safari (which
  never fires that event) it asks the parent to show a hint dialog instead,
  since a `DropdownMenuContent` unmounts on close and would take an
  internally-owned dialog's open state with it. Wired into `app-shell.tsx`'s
  `UserMenu` — the only shared-file change, four spots: one import, one
  `useState` for the iOS-hint dialog, one `<InstallMenuItem onIosHint=.../>`
  item, one `<RingDialog>` (the OTP-dialog look, per `docs/FRONTEND.md`) as a
  sibling of the `DropdownMenu`, not inside it. `Share` icon import.
- `src/app/panel/offline/page.tsx` (+ shared `src/components/pwa/offline-page.tsx`,
  `retry-button.tsx`) — static Persian page, `IsoBadge` (kit.tsx's shared 3D
  extruded-badge component) as the 3D touch, no session/API call of any kind.
- `frontend-next/scripts/render-icons.mjs` — Playwright Chromium, same engine
  the backend and the e2e specs already use. Takes an SVG path (default:
  `frontend/favicon.svg`, the site's current placeholder mark — the actual logo
  stream reruns this with the chosen logo, nothing else about the setup
  changes). Renders `icon-192`, `icon-512`, `maskable-512` (mark scaled to the
  center 80% over a solid backdrop), `apple-touch-icon` (180), `favicon-32`,
  committed under `public/icons/`.
- `src/proxy.ts` — two additive changes: `/panel/offline` (and `/portal/offline`)
  exempted from the login redirect; `sw.js`, `*/manifest.webmanifest` and
  `/icons/*` return early with no nonce/CSP header work (the negative-lookahead
  matcher can't exclude a nested path like `/panel/manifest.webmanifest` by
  prefix, so this is done inside the handler instead of the `config.matcher`).
- `appleWebApp` metadata (`mobile-web-app-capable`, apple title/status-bar) and
  the icon `<link>`s live in the new `panel/layout.tsx` and `portal/layout.tsx`.

**Portal is scaffolding only** — Phase 4 step 8 (پورتال) has not started;
`/portal` in production is still `frontend/portal.html`, served directly by the
backend. `src/app/portal/manifest.webmanifest/route.ts`,
`src/app/portal/offline/page.tsx` and `src/app/portal/layout.tsx` exist and are
tested (they build, serve correctly, register their own SW scope), but nothing
routes real traffic to them yet — no portal shell exists to add an install menu
item to. When step 8 builds the portal, the manifest/offline/registration
pieces are already there; only the install UI wiring (into whatever the
portal's own shell component turns out to be) is left.

## B. Email templates — brand/domain from site settings, one hero per family

- **New** `app/services/site_settings.py`: the read/defaults logic moved out of
  `app/api/routes/site.py` (which now imports it) so `email_templates.py` can
  read the same site config without a service importing a route module.
  `app/api/routes/site.py`'s behaviour is unchanged — `tests/test_site_settings.py`
  (HTTP, unchanged) still passes.
- `email_templates.py`: every public function (`login_code`, `verify_email_code`,
  `welcome`, `ticket_decision`, `request_received`, `notification`, `test_message`,
  and `shell()`) gained an **optional** `site: dict | None = None` keyword —
  existing call sites still work unchanged (falls back to
  `site_settings.defaults()`, the same env/default values the hard-coded
  constants used to be). Removed the module-level `BRAND`/`SITE_URL` constants
  and every hard-coded "سورین‌فلو" / "املاک سورین" / `sorinflow.com` in every
  subject line, body sentence, footer link and copyright line. All 10 call sites
  across the backend (`verification.py`, `users.py`, `forwarder_watch.py`,
  `divar_scraper.py`, `contact_extractor.py`, `portal.py`, `public_auth.py`,
  `email.py`'s `send`/`test`/`broadcast`/`broadcast preview`/`preview`) now fetch
  `await read_site(db)` and pass it through.
- One isometric PNG hero per template family (auth/welcome/decision/request/
  notification — 5 images), rendered by the **new**, committed
  `scripts/render_email_heroes.py` (Playwright Chromium again) into
  `app/static/email_assets/`, mounted at `/email-assets/` in `app/main.py` and
  added to `api_key_middleware`'s public prefix list (same pattern as
  `/images`/`/downloads`). Referenced by an absolute URL built from the site's
  own domain (`https://{domain}/email-assets/hero-<family>.png`), **not**
  embedded as a `data:` URI: Gmail strips `data:` URIs from HTML mail outright,
  so an inline image would silently vanish in the one client that matters most;
  a hosted PNG degrades the same way every marketing email already does
  (blocked-by-default remote images show a placeholder, not nothing). The
  `<img>`'s pixel `width="600"` is confined to an `<!--[if mso]>` branch — a bare
  one outside it would win over `max-width` on Gmail's Android app the same way
  it once did for the card itself.
- Animated GIF variant for campaign/digest mail: **not done** — ran out of scope
  for this stream; the hero mechanism (family → PNG file → hosted URL) is built
  so a GIF is a drop-in swap (`HERO_FILES` maps family → filename) whenever
  that's picked up.
- Plain-text alternatives, send paths (`email_service.send`, `resolve_config`)
  and every function's required positional/keyword arguments are unchanged.

## Tests

- New: 8 tests in `tests/test_email_panel.py` (`TestBrandComesFromSiteSettings`,
  `TestHeroImages`) — brand substitution with no leak of the default, no
  hard-coded brand/domain anywhere in the module source, every hero file
  referenced actually on disk, hero `<img>` never a bare `data:` URI, Outlook's
  `width="600"` confined to its own branch.
- New: `tests/test_security_headers.py::test_email_assets_pass_the_api_key_gate`.
- New: `tests/e2e/next/pwa.spec.js` (11 cases) + one shared helper added to
  `tests/e2e/next/helpers.js` (`waitForServiceWorkerCache` — `page.waitForFunction`
  with an async predicate resolves as soon as the predicate returns a *Promise*,
  not once it settles, so it never actually waited; this polls
  `cache.match()` directly instead). Manifests valid and served with the right
  content-type, every icon they list is a real PNG that exists, `sw.js` served
  as JavaScript from the root and never claims `/api/`, `/images/`,
  `/downloads/`, `/dashboard/`, registers and precaches on a production build
  (Chromium — installability is a Chromium concept, skipped elsewhere), both
  offline pages render (Persian, RTL, no horizontal scroll, axe-clean), reachable
  with no session cookie, and the cached copy is real.

### Results
- Backend, Postgres (`sorinflow_test`), full suite except `tests/test_dr_roundtrip.py`
  (needs an external backup/Telegram toolchain, fails identically on the base
  branch — unrelated): **3661 passed, 30 skipped, 0 failed.**
- `python scripts/lint_new_code.py --base origin/claude/phase-4-nextjs-frontend-ca3150`:
  clean (ruff + mypy on changed backend/script lines).
- Frontend: `npx tsc --noEmit`, `npm run lint`, `npx next build` — all clean;
  routes list includes `/panel/manifest.webmanifest`, `/panel/offline`,
  `/portal/manifest.webmanifest`, `/portal/offline`.
- `tests/e2e/next/pwa.spec.js`: 11/11 on desktop (Chromium); 22/22 more running
  the same file again on `android` and `iphone` (this box has no WebKit, so
  `iphone` ran on Chromium with the iPhone viewport — real WebKit is CI-only,
  same as the repo's existing fingerprint tests).
- Regression check against a clean production build
  (`scripts/e2e_next_up.sh`, real backend on `:8020`): `login.spec.js` (3),
  `dashboard.spec.js` (5), `crm-a.spec.js` (9), `crm-b.spec.js` (4),
  `crm-d.spec.js` (7 of 8 — one `crm-d` case hit a pre-existing strict-mode
  locator flake from cross-test data pollution, reproduced and passed clean in
  isolation, confirmed unrelated) all still pass — `app-shell.tsx` and
  `proxy.ts`'s changes cost nothing.
- Manual: screenshots of `/panel/offline` at desktop/dark and iPhone/light, the
  dashboard and the user menu after logging in as `owner`; a rendered
  `login_code` template with its hero image loading from a local
  `/email-assets/` mount.

## For the coordinator

- New public backend path: `/email-assets/` (static, `app/static/email_assets/`,
  committed PNGs) — needs the same ingress treatment as `/images`.
- New frontend routes needing the `web` pod once it exists in k8s:
  `/panel/manifest.webmanifest`, `/panel/offline`, `/sw.js`,
  `/icons/*`, and (inert until step 8) `/portal/manifest.webmanifest`,
  `/portal/offline`.
- No new npm dependency in `frontend-next/package.json` — `render-icons.mjs`
  uses the box's already-installed global Playwright
  (`/opt/node22/lib/node_modules/playwright`), same as the e2e specs'
  screenshot helper in `docs/phase4/routine-rules.md`. `render_email_heroes.py`
  uses the backend's existing `playwright==1.41.0`.
- No new backend dependency, no migration.
- Portal PWA wiring (install menu item) is the one open item once step 8 lands
  a portal shell component.
