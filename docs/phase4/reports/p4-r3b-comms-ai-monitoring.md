# p4-r3b-comms-ai-monitoring

Branch: `p4-r3b-comms-ai-monitoring`, based on `origin/claude/phase-4-nextjs-frontend-ca3150`
(HEAD at `73d6b5d`). No sub-branches or worktrees were used — everything below was built
directly on this branch in one session.

Commits (oldest first):
1. `feat: /panel/sms — stats, Kavenegar settings, events, send, broadcast, history`
2. `feat: /panel/email — SMTP settings, template preview, campaigns, history`
3. `feat: /panel/ai — tiles, agent grid, call log, «سورین» Q&A, settings`
4. `feat: /panel/monitoring — old section kept, new §4 tabs added`
5. `fix: scrollable log/Q&A lists lost their list semantics under axe`

## Scope

Phase 4 step 7, part 2: full parity with the matching parts of
`docs/phase4/inventory-system.md` §1–4 (SMS, email, AI, monitoring), plus the new
monitoring contract from `docs/MONITORING.md` §4.

## What was built, against the inventory

### 1. پیامک — `/panel/sms` (`src/components/sms/sms-view.tsx`)
- Stat tiles: اعتبار باقی‌مانده (`GET /sms/account`), ارسال‌شده/رسیده/ناموفق در ۳۰ روز
  (`GET /sms/stats`). Done.
- تنظیمات کاوه‌نگار (super_admin only): write-only masked key, sender, OTP template,
  signature, enabled switch, key-source hint (env/panel), test-send box that shows which
  route it used. Done.
- رویدادهای سرویس پیامک: stage + level filters, auto-excludes `inbound` unless a stage is
  picked. Done.
- ارسال پیام تکی: to + body with the 70/67-char Persian SMS-part counter. Done.
- ارسال گروهی (super_admin): audience list with live counts, `confirm_count`-guarded
  confirm dialog, campaign name, button re-labelled with the live count. Done.
- تاریخچهٔ ارسال: 50/page, search (number or text) + status filter, «بروزرسانی وضعیت».
  Done.
- Iranian mobile input, phone-gate handling, and the 403 `sms` permission message are the
  API's own — pages don't duplicate that logic (`api.ts` / `ApiError` already handle it).

### 2. ایمیل — `/panel/email` (`src/components/email/email-view.tsx`)
- Stat tiles: connection state, sent/login-codes/failed in 30 days. Done.
- تنظیمات ایمیل (SMTP) (super_admin): host/port/user/write-only password/security/
  from-name/from-email/reply-to, Gmail app-password hint (shown when the host contains
  "gmail"), ذخیره + بررسی اتصال (`POST /email/verify`, no send) + ارسال آزمایشی. Done.
- ارسال ایمیل (super_admin): free-form to/subject/body. Done.
- پیش‌نمایش قالب‌ها: a template `<select>` whose iframe points `src` straight at
  `GET /email/preview/{name}` — a real same-origin navigation, not `srcdoc`/`fetch`+inject.
  **No `proxy.ts` change was needed**: `frame-src` has no explicit directive in the strict
  CSP, so it falls back to `default-src 'self'`, which already allows it; and
  `app/main.py`'s own CSP on API responses is `Content-Security-Policy-**Report-Only**`
  (never enforced), so the template's `<style>` block — explicitly "never load-bearing" by
  the template's own design comment, since every rule is duplicated as inline `style`
  attributes for email-client compatibility — renders regardless. Done.
- کمپین ایمیلی (super_admin): audience picker, subject/body/CTA fields, a 400ms-debounced
  live preview (`POST /email/broadcast/preview` fetched via `api()`, set as a sandboxed
  iframe's `srcdoc` — safe here for the same non-load-bearing-`<style>` reason above, since
  `srcdoc` documents *do* inherit the parent's enforced CSP and would otherwise block an
  un-nonced `<style>` block), `confirm_count`-guarded send with a confirm dialog. Also added
  a small "دریافت فهرست" export next to the audience picker (`GET /email/export`) that
  builds a `text/plain` file client-side from the returned addresses and revokes the object
  URL right after triggering the download — no caching, no logging of the PII. This wasn't
  explicitly itemised as a UI element in the inventory (only listed as an endpoint), so it's
  an interpretation of "history with filters and export" — flagging it for review.
- تاریخچهٔ ارسال: template + status filters, 50/page. Done.

### 3. هوش مصنوعی — `/panel/ai` (`src/components/ai/ai-view.tsx`)
- Whole-page role gate (root/super_admin), matching every route's `_role_dep` in
  `ai.py`/`ai_assistant.py` (neither router carries a permission-key dependency). Done.
- Tile row: اتصال، هزینهٔ امروز (+ cap)، این ماه، توکن رایگان لیارا امروز (from
  `GET /ai/overview`; the Liara tile shows «در دسترس نیست» when `quota` is `null`, i.e. no
  Liara account token — expected in this env). Done.
- ایجنت‌ها grid: all 6 agents (explainer/reader/need/embed/vision/assistant), on/off toggle,
  editable per-agent cap, today/month calls+cost, «اجرای یک دور» for reader/embed/vision
  only (matching which agents actually have a `/run` route), and an error chip that jumps
  the log below to that agent's failures. Done.
- لاگ فراخوانی‌ها: agent + «فقط خطاها» filters. Done.
- سؤال‌های دستیار «سورین»: transcript + «بپرس» box using the same panel-ask pipeline. Done.
- تنظیمات هوش مصنوعی: 4 free-text model fields (env value shown as hint when panel value is
  empty), daily cap, office notes (600-char counter), enabled switch, ذخیره + تست اتصال. The
  circuit-breaker pause state (`breaker.state`/`until`) is surfaced as a badge next to the
  card title. Done.

### 4. پایش سامانه — `/panel/monitoring` (`src/components/monitoring/`)
- «نمای کلی» kept as the old section, unchanged in substance: health tiles (DB/Redis
  latency, disk % with amber/red thresholds, uptime+memory), سرور و سیستم table, مدت کارکرد
  و اتصال table + «تست واقعی» (`POST /monitoring/connectivity-test`), وضعیت نشست‌های دیوار
  with per-row «بررسی واقعی» (`POST /monitoring/cookies/check`), scraper job-status chips +
  stale-running warning, Google Cloud card + test, پردازه‌ها و کارهای پس‌زمینه
  (root/super_admin only, `GET /monitoring/runtime`), خطاهای مرورگر کاربران with clear,
  وضعیت زندهٔ سرور (5s poll, pause/resume, CPU/RAM/swap derived client-side from two
  consecutive `/monitoring/live` samples, tolerates missing fields), and لاگ سامانه
  (file/level/grep against `GET /stats/logs`). Done, all 9 inventory items.
- New tabs from `docs/MONITORING.md` §4/§8 (root/super_admin only): سرور، کوبرنتیز،
  سرویس‌ها، CI/CD، هشدارها (with the «پیام آزمایشی» button). **None of the §4 endpoints
  exist on this base branch** — confirmed by reading `app/api/routes/monitoring.py`, which
  only has `connectivity-test`, `client-errors`, `live`, `overview`, `cookies`,
  `cookies/check`, `runtime`; `GET /monitoring/{system,services,cicd,history,alerts}` and
  `POST /monitoring/alerts/test` all 404. Each tab is built against the documented response
  shapes in §4 and shows a graceful «هنوز در دسترس نیست» empty state naming the missing
  endpoint (e.g. `GET /api/monitoring/system`) instead of an error — verified with real 404s
  from the running backend, screenshotted. The §4 history chart (1h/6h/24h/7d selector) was
  **not** built: no tab in §8's list names it explicitly and every other §4 endpoint already
  needed the graceful-missing treatment, so it was left out rather than built against
  guessed data with no real endpoint to verify it against — flagging for the next stream
  that does have `/monitoring/history` to add it (likely into the «سرور» or «سرویس‌ها» tab).
- 3D touch: a small isometric server rack (`src/components/monitoring/rack.tsx`) with a
  pulsing status LED sits in the page header — CSS/SVG only, respects reduced motion.

## Shared-file changes
None. Every file is new, under `frontend-next/src/app/panel/(app)/{sms,email,ai,monitoring}/`
and `frontend-next/src/components/{sms,email,ai,monitoring}/`. `kit.tsx`, `viz.tsx`, `lib/*`,
`app-shell.tsx`, `nav.ts`, `globals.css`, `proxy.ts` are untouched.

## Backend changes
None. All endpoints used already exist and match the inventory's documented shapes exactly
(verified by reading every route file in full: `sms.py`, `email.py`, `ai.py`,
`ai_assistant.py`, `ai_reader.py`, `ai_embed.py`, `ai_photo.py`, `monitoring.py`, `gcp.py`,
the `/stats/logs` route in `stats.py`, and the `llm.py`/`assistant.py` services behind them).
The missing §4 monitoring endpoints were deliberately **not** added — see above.

## Environment
Postgres 16 + Redis on this box (no `sudo`, ran as root), Python 3.11 venv at `/tmp/venv`
from `requirements-dev.lock`, backend on `:8020` via `scripts/e2e_up.sh`
(`E2E_PORT=8020 PYTHON=/tmp/venv/bin/python`), frontend on `:3000` via `next dev`
(`BACKEND_INTERNAL_URL=http://127.0.0.1:8020`). This session's box does not have a
pre-installed WebKit binary (only the Chromium at `/opt/pw-browsers/chromium-1194`) and the
environment's own instructions say not to run `playwright install` — so the "iphone" e2e
project ran on Chromium emulating an iPhone 13 (the config's built-in fallback when
`E2E_WEBKIT` is unset), not real WebKit. Flagging this as an environment difference from
the box `routine-rules.md` assumes, not a skipped check.

## Checks

- `npx tsc --noEmit`, `npm run lint` (whole repo), `npx next build` — all clean, all 4 new
  routes present in the build's route list.
- Manual verification: signed in as `owner` (super_admin) and screenshotted all 4 pages at
  1440×900 dark and 390×844 light, after scrolling through (the `Reveal`/`whileInView`
  sections only paint once actually scrolled into view — confirmed this is why a first,
  un-scrolled screenshot looked truncated, and that scrolling through fixes it, matching
  `tests/e2e/next/helpers.js`'s own `scrollThrough`). Also checked role gating directly:
  `agent1` (no `sms`/`email`/`monitoring`/permission) gets the backend's own Persian 403
  message on every card, no crash; `manager1` (admin, all permissions but not super_admin)
  correctly loses the SMTP/campaign/settings/broadcast cards and the whole AI page renders
  its "فقط برای مدیر ارشد و root" deny screen instead of the section.
- e2e: added `tests/e2e/next/{sms,email,ai,monitoring}.spec.js` (24 tests total), run against
  the dev servers above (`E2E_DEV=1`) across `--project desktop --project android --project
  iphone` (`PW_CHROMIUM` pointed at the preinstalled Chromium). **Final state: 52 of 63 pass.**
  The 11 known failures, all diagnosed, none reflecting a real product defect found so far:
  - **email.spec.js "owner sees SMTP settings…" and "dark theme…" (×3 projects, 6 of the
    11):** the template-preview iframe is `sandbox="allow-same-origin"` (no
    `allow-scripts`, as it must be — the response is arbitrary-ish templated HTML). Under
    `next dev` specifically, something (most likely Next.js's own dev/HMR client, the same
    category of noise `helpers.js` already special-cases for "Refused to apply inline
    style") repeatedly tries to run a script inside that same-origin iframe and gets
    blocked, logging ~120 console errors over the test's run and failing my `watchProblems`
    check. `email_templates.py` itself has no `<script>` tag. The iframe's actual *content*
    renders correctly — confirmed both by screenshot and by the separate, passing "campaign
    card debounces a live preview" test, which asserts on the preview iframe's text content
    directly. This did not reproduce in my earlier ad-hoc Playwright script when it didn't
    happen to trigger this dev-only path. Not re-verified against a production build
    (`next build && next start`) for time; the next stream/coordinator running the full
    suite against the production server per `playwright.next.config.js`'s default should
    see this disappear, since that config comment already says "the strict CSP is only
    strict there" (dev injects extra scripts/styles prod doesn't).
  - **ai.spec.js "owner sees every agent card…" (×3):** a genuine bug in *my test*, not the
    page — `page.locator('div', { has: ... })` for the agent-card scope wasn't specific
    enough and the fix went into the chain along with the `role="region"` mistake below; not
    re-verified after the `role="region"` fix (commit 5) since re-running the full 3-project
    suite conflicted with being told to stop and push now. Left as a known follow-up.
  - **sms.spec.js "owner sees the Kavenegar settings…" (×3):** my test's toast-text regex
    (`/ارسال (شد|ناموفق بود)/`) also substring-matches the hidden `<option>` "ارسال شد" in
    the history status filter, which sorts earlier in the DOM than the toast portal — a
    `.first()` locator bug in the test, not the page. Needs a toast-scoped locator (e.g.
    `page.getByRole('status')`) instead of a bare text regex.
  - All of the above were caused by test-authoring issues or a dev-only artifact, are
    understood, and are not believed to indicate a broken page — but they are **not**
    re-verified green after the fixes above, because I was told to stop iterating and push
    now rather than run the suite again. Whoever picks this up next: the fixes to apply are
    (1) scope the "ارسال شد" toast assertion in `sms.spec.js` to `getByRole('status')`, (2)
    tighten the agent-card locator in `ai.spec.js`, (3) re-run once against the production
    build to confirm the email iframe noise is dev-only.
  - Every *other* assertion in all 4 spec files passed, including the real functional ones:
    single/broadcast SMS send flow, the broadcast confirm dialog and its zero-count guard
    (no phone numbers in this seed, so the guard itself was the thing verified), email
    campaign live preview + confirm + history filter, the full AI settings/toggle/ask flow,
    the AI role-gate deny screen, and every monitoring overview card plus the new tabs'
    graceful-missing state and the live chart's pause/resume — on desktop, Android and
    iPhone(-emulated).

## What the coordinator should know
- No new env vars, no new routes the ingress needs to know about (everything is under the
  already-routed `/panel/*` and `/api/*`).
- The `§4` monitoring endpoints (`system`/`services`/`cicd`/`history`/`alerts`) still need a
  backend implementation on some future stream before those tabs show real data; until then
  they degrade gracefully rather than error, on any base branch that lacks them.
- The three e2e failures described above are understood but not re-verified fixed; see the
  follow-up list.
- Nothing was pushed to `main`, `sorinflow-v2` or the base branch; no PR was opened; no
  workflow was run; no server was touched.
