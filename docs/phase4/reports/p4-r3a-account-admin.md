# p4-r3a-account-admin — Phase 4 step 7, part 1

Branch: `p4-r3a-account-admin`, based on `origin/claude/phase-4-nextjs-frontend-ca3150`.

Commits (oldest first):
1. `992a0db` feat: /panel/profile — own profile page
2. `34c9606` fix: profile's Divar-numbers list 403s for staff without divar_auth
3. `f99aaa3` feat: /panel/users — access tickets, maintenance, backup, DR, team
4. `8a31463` fix: backup/DR cards crashed on real backend response shapes
5. `56fa33e` feat: /panel/audit — the audit trail table
6. `3394fd4` feat: /panel/settings — root-only brand and site identity
7. `4e608fc` test: e2e coverage for profile, users, audit and settings

## Scope

Four pages: `/panel/profile` (own profile, no nav item), `/panel/users`
(root/super_admin), `/panel/audit` (root/super_admin), `/panel/settings`
(root only, new page + one nav item). No backend work was needed — every
endpoint these pages call already existed (built ahead of this stream),
including `GET/PUT /api/settings/site`, which `docs/phase4/inventory-system.md`
§5f/§9 says did not exist yet when that document was written.

## Inventory coverage (docs/phase4/inventory-system.md)

### §7 Profile page — done, in full
- Hero card: avatar upload/remove (`POST`/`DELETE /users/me/avatar`),
  presence select, member-since/last-login, «IP شما از نگاه سرور»
  (`GET /users/me/ip`).
- مشخصات form: full name, username (rename reissues the session — the shell
  refetches via `SESSION_KEY` invalidation, not a manual token swap, since
  the new panel's session lives in an httpOnly cookie the backend re-issues
  itself), headline, bio, website/instagram/linkedin.
- اتصال به تلگرام: link-code card, deep link, bot command, 1s countdown/poll
  (re-checks the server every 5th tick), unlink.
- تماس و تأیید: email and phone change-then-verify (request code → confirm
  code, both channels use the same OTP-look `InputOTP` with the request
  nonce), «شماره‌های دیوار من» **read-only** (per the task's explicit scope —
  no پیش‌فرض/حذف actions here; those live in «احراز هویت دیوار», step 6, not
  built yet — a link points there). Gated behind the `divar_auth` permission
  client-side (see fix commit `34c9606`), matching the old panel.
- امنیت: change password (`POST /users/me/password`, refetches the session
  since the backend re-issues the cookie), TOTP status/setup/enable/disable
  (QR via npm `qrcode` → SVG data URI in an `<img>`, manual secret with
  copy, 6-digit `InputOTP` with `nonce={useNonce()}`, disable re-asks the
  password), email-2FA toggle (disabled with an explanatory note when no
  email is on file).

### §5 Users + Backup + DR + Maintenance — done, in full
- 5a Tickets: `GET /portal/admin/tickets?status=pending`, permission
  checklist (from `GET /users/permissions/catalog`), approve/reject
  (`POST /portal/admin/tickets/{id}/decide`).
- 5b Maintenance: close/reopen with a duration dropdown, contact
  phone/email, countdown (from `seconds_left`), bypass-link box after
  closing. «ذخیرهٔ تنظیمات» is a no-op with a toast while the site is open
  (matches the old panel's guard — settings only take effect at the next
  close).
- 5c Backup + offsite: bot token (write-only, masked hint) + chat id(s),
  «پیدا کن» (`POST /backup/probe`), the three-way route picker (manual
  proxy / dashboard pool / Cloudflare relay — one pane at a time, only the
  active mode's fields sent), «تست این راه» (`POST /backup/proxy-test`),
  ذخیره (`PUT /backup/settings`), «همین حالا» (`POST /backup/run`), the
  Worker-source viewer (`GET /backup/relay-worker`, copy button), morning
  digest preview + send now (`GET`/`POST /backup/digest*`).
- 5d DR: `GET /backup/dr`, «همین حالا» (`POST /backup/dr/run`, polled every
  4s up to 2 minutes since it only drops a request file for a host-side
  systemd unit), «تست همهٔ راه‌ها» (`POST /backup/diagnose`, shared with 5c
  per the inventory's open question — confirmed it's the same call).
- 5e Team table: search + role + active filters (client-side over one
  `GET /users`, same as the old panel — no server-side params exist),
  new-user dialog, permission editor (role + permission checklist for
  `admin`), **the root-demotion regression fix preserved**: the role
  `<select>` always starts on the account's actual role, root included,
  covered by an e2e regression test. Verification: root gets an inline
  switch per contact (`PATCH /users/{id}/verification`), everyone else a
  read-only tick + «درخواست تأیید» nudge (`POST /users/{id}/verification-request`).
  Reset password, disable TOTP, delete — all guard self-action and
  root-owned rows per the backend's own `_guard_root_target`.
- 5f Site/brand settings — **built as its own page**, see below.

Not built: the «شمارهٔ دیوار» (primary Divar number) edit action the
inventory's §5e old-panel description mentions in the kebab menu. The
task's own condensed scope for the team table names permission editor,
verification toggles, reset password, disable TOTP and delete explicitly
and doesn't include it; skipped to keep scope matched to the task text
rather than the fuller inventory prose. Easy to add later — `PATCH
/users/{id} {divar_phone}` already exists and is exercised by
`/me/divar-phone`'s sibling code path.

### §6 Audit — done, in full
Actor search, action `<select>` (from `GET /audit/actions`), Jalali
since/until (`JalaliDateInput`, sent as bare Gregorian dates so the
backend's "through the end of that day" rule on `until` applies), table
(زمان/کاربر/عملکرد/هدف/IP), 50/page pagination via the shared `Pagination`
component.

### §5f / §9 Site settings — done, new page
`app/api/routes/site.py` (`GET/PUT /api/settings/site`, super_admin+root
gated) already existed on the base branch — this stream only needed the
frontend. Built `/panel/settings`: every `SiteConfig` field (brand name
fa/latin, tagline, office name, domain, phone, email, telegram, instagram,
address, SEO title/description), with a live preview that mirrors the
login page's own brand corner and headline
(`src/app/panel/login/page.tsx`) so root sees the real result before
saving. The form sends only the fields actually edited (see the fix
below), not the whole object every time.

## Shared-file changes (smallest additive, listed per routine-rules.md)

- `frontend-next/src/components/panel/nav.ts`: one new entry in the
  «سیستم» group — `{ key: "settings", label: "برند و سایت", href:
  "/panel/settings", icon: Palette, roles: ["root"], step: 7, legacy:
  "users" }` — plus the `Palette` icon import. Nothing else in the file
  touched.
- `frontend-next/src/lib/session.ts`: added `divar_phone: string | null` to
  the `User` type. The backend's `UserResponse` already returns it (used by
  the team table's primary-Divar-number badge and the profile's own-numbers
  list); the frontend type just didn't carry it yet.
- `frontend-next/package.json` / `package-lock.json`: added `qrcode@1.5.4`
  and `@types/qrcode@1.5.6` (exact versions, as instructed) for the TOTP QR.

No other shared file touched.

## Bugs found and fixed during manual testing

1. **Profile's Divar-numbers list 403'd for any staff without `divar_auth`**
   (`app/api/routes/__init__.py` gates the whole `/auth` router on that
   permission). The old panel checked the permission client-side first and
   showed a note instead of calling the API; the rebuild hadn't. Fixed by
   gating the query the same way (`fix 34c9606`).
2. **Backup/DR cards crashed on a fresh backend** with `RangeError: Invalid
   time value`, found via a screenshot showing Next's dev error overlay.
   Three response-shape mismatches: `bk.last_offsite()` returns `{}` (not
   `null`) before the first shipment ever runs, so treating any object as
   truthy fed `new Date(undefined)`; `local_snapshots()` reports `size_kb`
   in kilobytes, not `size` in bytes; and `dr_backup.py`'s status file nests
   the outcome under `last_run.sent` (`at`/`ok`/`error`), not at the top
   level. Fixed in `8a31463`.
3. **Settings save 422'd on this dev box specifically**: this sandbox's
   `DOMAIN=localhost` (set by `scripts/e2e_up.sh`) fails `SiteIn`'s
   `domain\.tld` pattern. The form originally sent the whole `SiteConfig`
   back on every save, including the untouched `domain` field, so *any*
   save failed here. Fixed by sending only the fields the root actually
   edited (diffed against the last-saved baseline) — also the more correct
   semantics for a `PUT` whose handler applies `exclude_unset` fields.
4. **A real accessibility bug**: the hidden avatar-upload `<input
   type="file">` had no accessible name — axe flagged it as a critical
   `label` violation. Added `aria-label="بارگذاری عکس پروفایل"`.
5. Everything else axe first reported (`color-contrast` on three
   `text-muted-foreground` spots) turned out to be a test-timing race, not
   a real bug: `Reveal`'s fade-in animation (0.55s + a per-section delay)
   was still mid-transition when `a11y()` ran right after `scrollThrough`,
   so axe measured a partially-transparent (and therefore lower-contrast)
   text color. Confirmed by re-measuring with an extra settle wait — the
   violation disappears. All four spec files now wait ~700ms after
   `scrollThrough` before calling `a11y()`.

## Checks

- `cd frontend-next && npx tsc --noEmit` — clean.
- `npm run lint` (eslint) — clean, on every commit.
- `npx next build` — clean, all four new routes compile
  (`/panel/profile`, `/panel/users`, `/panel/audit`, `/panel/settings`).
- `python scripts/lint_new_code.py --base origin/claude/phase-4-nextjs-frontend-ca3150`
  — clean (no backend files touched, so nothing for ruff/mypy to check;
  the script ran clean on every commit anyway).
- No backend changes → no new/changed pytest files, nothing to run there.
- E2E: `tests/e2e/next/account-admin-{profile,users,audit,settings}.spec.js`,
  20 tests. Run against the dev server (`E2E_DEV=1`) on all three
  configured projects:
  - `desktop`: 20/20 passed.
  - `android`: 20/20 passed.
  - `iphone`: 20/20 passed — **but on Chromium with the iPhone 13's
    viewport/touch/UA, not real WebKit**. `npx playwright install
    --with-deps webkit` failed: this sandbox's network policy blocks the
    download hosts (`cdn.playwright.dev`,
    `playwright.download.prss.microsoft.com` both returned 403 from the
    egress proxy). `tests/e2e/playwright.next.config.js` already falls
    back to Chromium for the `iphone` project when `E2E_WEBKIT` is unset,
    which is what ran. **The coordinator should re-run the `iphone`
    project with `E2E_WEBKIT=1` on a machine that can reach those hosts**
    (the Mac's real e2e run) before trusting Safari-specific behaviour.
  - 60/60 total across the three projects that did run.
- Screenshots: desktop 1440×900 dark and iPhone 13 (390×844) light for all
  four pages, read back and checked — RTL correct, no horizontal scroll, no
  console errors, no failed `/api/*` calls. Not committed (scratch only, in
  this session's own scratchpad).

## For the coordinator

- No new env vars, no new backend routes, no ingress changes — everything
  this stream's pages call already existed on `origin/claude/phase-4-nextjs-frontend-ca3150`.
- New npm deps: `qrcode@1.5.4`, `@types/qrcode@1.5.6` (both pinned exact, as
  the task allowed).
- The `iphone` e2e project needs a real run against WebKit (see above) —
  this sandbox couldn't reach the download host.
- Manual testing left no residue in the shared local Postgres — TOTP was
  disabled again on `agent1`/`agent2`, the test visitor/ticket and test
  admin/headline edits were all cleaned up (checked with direct `psql`
  queries after each round), and the e2e specs revert what they change
  (headline, office name) at the end of each test.
- `/panel/settings`'s nav entry uses `legacy: "users"` (there is no
  standalone settings section in the old panel to link back to; site/brand
  editing didn't exist there at all — see inventory §5f/§9). If that reads
  oddly against the "open in the current panel" link on some other step's
  stub page, it's this stream's judgment call, easy to change.

## Where to look

Dev server on this session: `http://127.0.0.1:3000` (backend
`http://127.0.0.1:8020`), logins `owner`/`root`/`manager1`/`agent1`/`agent2`,
password `local-pass-1234` — `/panel/profile`, `/panel/users`,
`/panel/audit`, `/panel/settings`.
