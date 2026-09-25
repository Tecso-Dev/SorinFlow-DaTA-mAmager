# Stream p4-r1-properties-portal — coordinator report

Branch: `p4-r1-properties-portal`, based on `origin/claude/phase-4-nextjs-frontend-ca3150`.
This stream split into two sub-streams, each in its own worktree/branch, merged back here.

**Correction**: an earlier version of this report and of commit `fe599ef` on the
properties/insights sub-branch described part A as an unverified, stopped-mid-flight
snapshot ("may not even compile"). That was a mid-flight commit taken to satisfy an urgent
"merge and push now" instruction while the sub-stream was still actually running; it kept
going in the same turn and finished everything that message said was missing. The corrected,
accurate status is below — see `docs/phase4/reports/p4-r1a-properties-insights.md` for the
sub-stream's own full report.

---

## A) `/panel/properties` + `/panel/insights` — sub-branch `p4-r1a-properties-insights`

**Status: complete and checked.**

What exists:
- `frontend-next/src/app/panel/(app)/properties/page.tsx` + `src/components/properties/*`:
  full table with every §1.1 filter (search, searchable city picker, category→buy/rent,
  rent-only deposit/rent band) **plus** UI for the previously-unwired filters (min/max
  price/area/rooms, has_phone, sort_by/order) behind a "فیلترهای بیشتر" toggle — a
  parity-extension decision, documented in the sub-stream's report. Detail view is a wide
  Sheet drawer (not `RingDialog` — too much content for a centred dialog, justified in the
  report) with a from-scratch image lightbox (zoom/pan/carousel), inline
  `building_direction`/`corner_type` PATCH-on-change selects, an AI facts block (any role)
  and AI photo-tags block (root/super_admin only, server-enforced), delete via `useConfirm`,
  and a «ملک‌های مشابه» match modal reusing the old page's 4s/3-try polling. Both exports:
  Excel (full filter set) and JSON (intentionally narrower `{city, listing_type}` body,
  matching the old panel's actual behavior rather than "fixing" it).
- `frontend-next/src/app/panel/(app)/insights/page.tsx` + `src/components/insights/*`:
  visual tab (4 stat cards, coverage sentence, all 3 tables with exact empty-states,
  root/super_admin AI photo-status card, the disclaimer preserved verbatim) and pipeline tab
  (window selector, funnel that keeps unrecognised `Lead.status` values — verified against
  live seed data, which has 5 of them — temperature donut, gap-filled trend line, city bar
  chart, agent scoreboard, stalled leads). The "null renders as em-dash, never a fake zero"
  rule (`format.ts`) is applied throughout.
- `app/schemas/__init__.py`: `PropertyResponse`/`PropertyBase` gained the fields the old
  detail modal needs that were previously silently stripped by the response model
  (`land_area`, `built_area`, `building_direction`, `frontage`, `unit_status`,
  `document_type`, `usage_type`, `building_age`, `extra_attrs`, `address`, `latitude`,
  `longitude`, `updated_at`) — purely additive, no migration (the columns already existed on
  the model). Noted side effect: this also fixes the CRM-leads stream's `PropertySheet`,
  which already read these same fields from the same endpoint.
- `tests/e2e/next/properties-list.spec.js` (6 tests), `tests/e2e/next/insights-a.spec.js`
  (5 tests).

**Checks — all pass**: `tsc --noEmit`/`lint`/`next build` clean; `lint_new_code.py` clean;
backend pytest on Postgres for every file touching `PropertyResponse`/`/properties/*` —
152 passed, 1 skipped (pre-existing/unrelated), 0 failed; e2e 33/33 across
desktop/android/iphone, repeated 3× on desktop to rule out flakiness; screenshots reviewed
at desktop 1440×900 dark and iPhone light for both pages — one real bug found and fixed this
way (a horizontal bar chart only rendering one bar correctly).

No shared frontend-next file was touched (`kit.tsx`, `lib/*`, `app-shell.tsx`, `nav.ts`,
`viz.tsx`, `globals.css` all untouched). Left undone, on purpose: the `over`-priced
valuation table (API returns it, the old panel never showed it — matches parity per the
inventory doc's own note); `PropertyUpdate` not extended beyond its existing two
inline-editable fields (nothing else needed it). Full per-item inventory checklist and
screenshot paths: `docs/phase4/reports/p4-r1a-properties-insights.md`.

---

## B) Public portal (`/portal`) + admin `/panel/portal` — sub-branch `p4-r1b-portal`

**Status: complete and checked.** Full detail in `docs/phase4/reports/p4-r1b-portal.md`
(kept as-is from the sub-stream, not duplicated here). Summary:

- Public portal at `/portal`, SSR with real SEO (`getSiteConfig()`), full parity with the old
  `frontend/portal.html`/`frontend/js/portal.js`: login/register/verify flow, password
  strength + caps-lock + show/hide password, the property-request form, «درخواست‌های من»
  with delete via `useConfirm` (not `window.confirm`), and the «دسترسی به پنل مدیریت»
  ticket in all three states. No Kavenegar script. `GET /api/public/auth/status` honored.
  3D + motion hero (`Skyline`/`Reveal`/`Tilt` from `viz.tsx`).
- New cookie-session login for visitors: `POST /api/public/auth/session/login`, mirroring
  the panel's own `app/api/routes/session.py`; refuses staff, reuses the existing
  `portal_login` password/rate-limit/verification logic via a new shared
  `_authenticate_visitor()` helper (no forked auth logic). No new logout route needed —
  `POST /api/session/logout` already clears any cookie regardless of role.
  `app/main.py`'s `api_key_middleware` public-path set got the one new path.
- Admin `/panel/portal` «درخواست‌های مشتریان»: status filter, table, inline status select
  that PATCHes, budget column branching on `deal_type`, and «ملک‌های مناسب» reusing the CRM
  stream's own `CustomerMatchesSheet` rather than a second matching UI.
- Checks: tsc/lint/build clean; `lint_new_code.py` clean; pytest 44/44 on the touched-file
  suites on Postgres, plus a full `pytest tests/ -q` run with no failures; e2e 15/15 across
  desktop/android/iphone; screenshots reviewed, one real bug (server/client boundary crash
  in `ConfirmProvider`) and one aria bug found and fixed via e2e before merge.

### Ingress — what the coordinator (Mac side) needs to do
`k8s/base/ingress.yaml`'s HTTPS rules have no entry for `/portal` yet — it still falls
through to the catch-all `path: /` → `backend`. Once this branch is reviewed, add a
`pathType: Prefix` rule for `/portal` → `web:3000`, inserted before the catch-all the same
way `/panel` and `/_next` already are. Not done in this stream on purpose — the old
`frontend/portal.html`/`frontend/js/portal.js` are untouched and keep serving `/portal` from
the `backend` pod until that ingress change supersedes them; nothing was deleted.

---

## C) Panel section «درخواست‌های مشتریان» at `/panel/portal`

Built as part of sub-branch B above (see the admin `/panel/portal` bullet in part B) —
this was folded into the portal sub-stream rather than run as a third parallel stream,
since it shares the same backend router (`app/api/routes/portal.py`) and domain knowledge.
Checked and passing along with the rest of B.

---

## Cross-stream notes

- No shared files were edited by either sub-stream beyond what's listed above
  (`app/schemas/__init__.py` by A, `app/main.py`'s middleware public-path set by B) —
  `kit.tsx`, `date-input.tsx`, `lib/*`, `app-shell.tsx`, `nav.ts`, `globals.css`, `viz.tsx`
  were all left untouched by both.
- Both branches merged into `p4-r1-properties-portal` with no conflicts (disjoint file sets:
  properties/insights vs. portal/admin-portal).
- `~/SorinFlow-backups` was not opened or read by either sub-stream.
- No secrets in any commit. Commit identity throughout: `sobhan azimzadeh` only, no
  `Co-Authored-By`/`Claude-Session` lines, no AI/Claude/assistant mention anywhere.
- Both sub-streams ran their own full check suites (tsc/lint/build/pytest/e2e) independently
  and both are green; this branch has not additionally run a combined coordinator-level pass
  with both merged together (e.g. one `next build` over the final merged tree) — the two
  touch disjoint files with a clean merge, so this is low risk, but it is not yet confirmed.
