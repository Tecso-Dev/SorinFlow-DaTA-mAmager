# Stream p4-r1-properties-portal — coordinator report

Branch: `p4-r1-properties-portal`, based on `origin/claude/phase-4-nextjs-frontend-ca3150`.
This stream split into two sub-streams, each in its own worktree/branch, merged back here.
**This run was stopped before the properties/insights sub-stream finished its checks** — see
part A below for exactly what that means.

---

## A) `/panel/properties` + `/panel/insights` — sub-branch `p4-r1a-properties-insights`

**Status: unverified snapshot, stopped mid-stream.** The sub-stream was told to stop before
it ran any of the required checks. What's merged here is a single as-is commit of everything
it had built at that point — nothing below has been confirmed to actually build or run.

What exists (not yet checked):
- `frontend-next/src/app/panel/(app)/properties/page.tsx` + `src/components/properties/*`:
  a properties table (`properties-view.tsx`), city picker, a detail sheet
  (`detail-sheet.tsx`) with an image lightbox (`lightbox.tsx`), inline
  `building_direction`/`corner_type` selects, an AI photo-tags block
  (`ai-blocks.tsx`), and a match-modal (`match-dialog.tsx`).
- `frontend-next/src/app/panel/(app)/insights/page.tsx` + `src/components/insights/*`:
  a visual tab and a pipeline tab (`visual-tab.tsx`, `pipeline-tab.tsx`), plus formatting
  helpers (`format.ts`) mirroring the old page's "null renders as em-dash, never a fake
  zero" rule.
- `app/schemas/__init__.py`: `PropertyResponse`/`PropertyBase` gained the fields the old
  detail modal needs that were previously silently stripped by the response model
  (`land_area`, `built_area`, `building_direction`, `frontage`, `unit_status`,
  `document_type`, `usage_type`, `building_age`, `extra_attrs`, `address`, `latitude`,
  `longitude`, `updated_at`) — this was the schema-gap fix the inventory doc flagged as a
  judgment call; the sub-stream chose to fix it rather than leave those detail-card rows
  permanently dead.
- `tests/e2e/next/properties-list.spec.js`, `tests/e2e/next/insights-a.spec.js` — written,
  never run.

**Not done, and this is the important part**:
- `npx tsc --noEmit`, `npm run lint`, `npx next build` were never run against this code.
  It may not even compile.
- No backend test was added for the `PropertyResponse` schema change, and
  `scripts/lint_new_code.py` was never run against `app/schemas/__init__.py`.
- The e2e specs above were written but never executed, on any project.
- No screenshots were taken; nobody has looked at either page rendered.
- The inventory checklist in `docs/phase4/inventory-properties-insights.md` was not
  cross-checked item by item against what actually got built — the sub-stream's own
  commit message only self-describes what it believes it built.

**Before this branch is trusted or shipped further, someone (or a fresh stream) must**:
run the full check suite from `docs/phase4/routine-rules.md` against this code, fix whatever
tsc/lint/build turns up, add the missing schema-change test, actually run the two e2e specs
on desktop/android/webkit, take and review screenshots, and only then reconcile against the
inventory doc line by line.

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
- This branch was pushed without running the coordinator-level full check suite (tsc/lint/
  build/pytest/e2e across everything merged together) — only part B's own checks, run before
  the merge, are known-good. Part A is unverified as stated above. Whoever picks this branch
  up next should treat A as a draft, not a finished deliverable.
