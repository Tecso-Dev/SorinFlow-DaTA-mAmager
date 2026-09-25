# p4-r2-scraper-divar — scraper, Divar auth, proxies, forwarder

Branch: `p4-r2-scraper-divar`, based on `origin/claude/phase-4-nextjs-frontend-ca3150`. Built as four
parallel streams in isolated git worktrees, each merged back with `--no-ff` after its own checks passed,
then re-verified together on this branch. No backend files were touched — this is a pure frontend port;
every endpoint the old panel used already exists.

## What's built

### `/panel/scraper` (permission `scraper`)
New-scrape card: Divar-link autofill (`POST /scraper/parse-link`, read-only, never auto-starts), city/category
pickers, account selector (`mine=1`, auto/manual, per-number enable/disable with `moved_jobs` surfaced),
collapsible "more filters" that auto-expands when any filter inside is already active, Jalali posted-date
picker, page count, rotate-every-N hint (direction spelled out: lower = more rotation = fewer OTP SMS),
download-images checkbox, live estimate (900ms debounce, `applied_after_scrape` warning). Start flow is
phone-gated and shows a cookie-not-valid warning dialog first when needed. Single-listing scrape creates a
real job. "Run daily" saves a schedule; schedules card has full CRUD + run-now, root/super_admin see
everyone's. Jobs table polls every 5s only while mounted, with log/skipped(+bulk re-scrape)/switch-account
(job-owner only)/cancel/resume/delete actions gated exactly as the backend gates them. The scraper-log
button is hidden for users who have `scraper` but not `stats` (an intentional fix over the old panel, which
didn't check this). No OTP UI here — that's global now (see below).

### `/panel/divar` (permission `divar_auth`) + global OTP popup
Login-a-number (phone → `POST /auth/login` → 6-box OTP, `POST /auth/verify?phone_number=` as a query param
→ auto-submit), refresh (handles the `in_use` case without erroring), logout, saved-sessions list (valid/
expired/disabled/identity-required badges, enable/disable, delete), manual cookie import (client JSON
validation, shows the real `alive: true/false/null`), and the root-only numbers registry
(`GET /auth/registry`, not just CSS-hidden — the component and its query never mount for non-root). Saving
an owner change (`PATCH /auth/registry/{id}/owner`) is the only ownership-transfer call in the whole panel;
confirmed no other stream added a second path (grepped for `owner_user_id` writes across all four areas).
The confirm dialog names both sides and warns about live-job movement.

The old per-section OTP modal is now `frontend-next/src/components/panel/divar-otp-popup.tsx`, mounted once
in the shell for any user with `scraper` or `divar_auth`: polls `GET /scraper/otp-pending` every 4s while the
panel is open, shows the identity-required wall first when present, 6-box paste/auto-submit OTP, server-driven
countdown, 15s resend lock, switch-number, job-scoped dismiss. It never displays more than the API's own
`phone_hint` — no unmasking, no extra filtering logic added on the client (trusts the endpoint's own
ownership scoping).

**Shared-file change (the only one across all four streams):** two lines in `app-shell.tsx` — the import and
mount of `<DivarOtpPopup />` next to the existing `<PhoneGate />`.

### `/panel/proxies` (permission `proxies`)
Add proxy (server's Persian rejection messages shown as-is for private/duplicate addresses), bulk import
(3 line formats), table with exit-country badge (red if not IR, "untested" when the API hasn't returned
`exit_country`/`exit_ip`/`is_hosting` yet rather than guessing), test / toggle / delete per row, test-all,
delete-all via the `confirm_count` pattern (409 on a stale count shown clearly).

### `/panel/forwarder` (permission `forwarder`)
Devices table with health-state badges, add device (one form dialog replacing the old three sequential
prompts, shows the one-time secret exactly once), the full 6-step install guide (APK mirror + GitHub
fallback, SMS-permission/Android-13 note, battery-optimization warning, QR hidden behind a 30s-reveal
shield with manual-field fallback, test-connection labeled as "last check-in" not a live ping, SMS parsing
rules incl. SIM2), rotate key, edit SIM 1/2 (re-scan warning), delete. Codes log filtered by outcome, with
defensive latency ("phone's clock" instead of a misleading number outside a 5-minute window); the whole
card is replaced by an explanation (not just disabled) for a user who has `forwarder` but not `sms`, matching
the same permission-mismatch pattern as the scraper log. Added two npm dependencies, nothing else:
`qrcode` and `@types/qrcode` (latest stable at merge time, see `frontend-next/package.json`).

## Not done
- The global topbar "cookie status" badge (inventory §1.6) — not in this task's literal checklist and no
  stream had it in scope (it needs another `app-shell.tsx`/header touch). Left for a later pass.
- `max_age_hours` scrape-form field — present in the backend schema but never exposed in the old panel's UI
  either; left out here too as a judgment call, noted by the scraper stream.

## Checks
- `cd frontend-next && npx tsc --noEmit && npm run lint && npx next build` — all clean on the fully merged
  tree (24 routes generated, including all four new ones).
- `python scripts/lint_new_code.py --base origin/claude/phase-4-nextjs-frontend-ca3150` — clean.
- E2E (Postgres-backed backend on :8020, Next dev server on :3000, Chromium at `/opt/pw-browsers/chromium`
  standing in for WebKit's "iphone" project — no WebKit binary reachable in this sandbox, so per the config's
  own fallback that project ran on Chromium with the iPhone 13 viewport instead):
  - `scraper.spec.js` — 33/33 (11 × 3 projects)
  - `divar.spec.js` — 30/30 (10 × 3 projects)
  - `proxies.spec.js` — 12/12 (4 × 3 projects)
  - `forwarder.spec.js` — 6/6 (2 × 3 projects)
  - All four run together (81 tests) with no cross-stream data collisions.
- Manually reviewed: no `window.confirm/alert/prompt` anywhere in the new code, no secrets committed, no
  second ownership-transfer path, the root registry card's data never fetches for a non-root session.
- Screenshots (desktop 1440×900 dark, iPhone 390px light) taken and read by each stream and by the
  coordinator; not committed.

## For the coordinator
- No new env vars, no new routes for the ingress to learn (`/panel/scraper`, `/panel/divar`, `/panel/proxies`,
  `/panel/forwarder` all go through the same Next.js app as everything else already routed).
- `frontend-next/package.json`/`package-lock.json` changed (only the two forwarder deps) — a plain
  `npm ci` picks them up.
- A local Postgres/Redis sandbox and a real WebKit binary weren't both available in this environment; the
  Mac coordinator run (per `docs/phase4/stream-rules.md`) should re-run the four new specs with
  `E2E_WEBKIT=1` for a true WebKit pass on `iphone`, on top of the desktop/android runs already green here.
- k3d/Kubernetes rehearsal and the cross-phase adversarial review are still outstanding per
  `docs/phase4/README.md`'s "پیش از دیپلوی" list — unchanged by this stream.
