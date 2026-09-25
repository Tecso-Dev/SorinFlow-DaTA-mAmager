# Stream p4-r1b-portal: public visitor portal + admin requests table

Branch: `p4-r1b-portal`, based on `origin/claude/phase-4-nextjs-frontend-ca3150`.

## Commits (in order)
1. `feat: cookie-session login for the portal, mirroring the panel's` — `POST /api/public/auth/session/login`.
2. `test: the portal's cookie login passes the API-key gate` — new test file + `api_key_middleware` addition.
3. `feat: the portal's cookie login honours «مرا به خاطر بسپار»` — `remember` field.
4. `feat: public visitor portal at /portal, on the cookie session` — the frontend.
5. `fix: the portal's login/register tabs had no matching content panel` — two real bugs an e2e run found (server/client boundary crash, aria-controls).
6. `test: e2e coverage for the visitor portal and its admin table`.

## Parity checklist against `frontend/portal.html` / `frontend/js/portal.js`
- Login tab + register tab, tab switch — done (Radix `Tabs`).
- Phone format validation (`09XXXXXXXXX`) — done, blur-validated.
- Password strength meter, 4 rules (len/lower/digit/upper-or-symbol) — done, `Progress` bar + rule list.
- Caps-lock warning — done, on both login and register password fields.
- Show/hide password, paste stays enabled — done (`type` toggle on a plain `Input`, no masked-only field).
- Field-blur validation with inline errors — done (name/phone/email).
- Email marketing opt-in checkbox — done.
- Verify screen: channel-aware hint text from the server's own message — done, never hardcoded.
- Debug code display (non-production only) — done, server-gated (`debug_code` only set off-production).
- Resend with a real cooldown countdown — done (`setInterval`, not a static disabled state).
- Request form: buy/rent toggle swapping deposit+rent vs budget min/max, plus city/districts/kind/area/rooms/year/elevator/parking/storage/description — done, all fields.
- «درخواست‌های من»: list with status badges, delete via `useConfirm` (not `window.confirm`) — done. This is the one deliberate behavior change from the old page, per `docs/FRONTEND.md`.
- «دسترسی به پنل مدیریت» ticket, three states (none→form, pending→status, approved→link to `/panel`, rejected→note + resubmit form) — done.
- 3D + motion hero on the landing screen — done (`Skyline` from `viz.tsx`, `Reveal`/`Tilt` on the dashboard's sections).
- No Kavenegar script anywhere — confirmed absent; nothing in the new portal loads any CDN script (strict CSP, no `<script src>` at all outside Next's own bundle).
- `GET /api/public/auth/status` honored: a disabled feature renders a plain "به‌زودی فعال می‌شود" page — done, checked server-side in `portal/page.tsx`.

Not done / out of scope, as directed: nothing from the old page was dropped. The bearer-token flow (`/api/public/auth/verify`, `/api/public/auth/login`) is still used internally as a step (verify), immediately followed by the new cookie login — the browser never stores the bearer token.

## Parity checklist against `docs/phase4/inventory-system.md` §8 (admin side)
- Status filter (new/in_review/matched/contacted/closed) — done.
- Table columns مشتری/خواسته/بودجه/وضعیت/تاریخ/عملیات — done.
- Inline status `<select>` that PATCHes on change — done, `PATCH /portal/admin/requests/{id}`.
- Budget column branches on `deal_type` (rent → ودیعه/اجاره ranges, buy → budget range) — done.
- «ملک‌های مناسب» for a request with `customer_id` set — done, reuses the CRM stream's own `CustomerMatchesSheet` (`src/components/crm/customers/matches-sheet.tsx`) rather than building a second matching UI; a request with no `customer_id` shows a neutral "بدون مشتری" badge instead of the button.

## Backend: new/changed endpoints and their tests
- `POST /api/public/auth/session/login` (`app/api/routes/public_auth.py`) — new. Body: `identifier, password, remember`. Success sets the httpOnly session cookie (`app/auth/session_cookie.set_session`) and returns `{user, csrf_token}`; an unverified account gets the same `PortalPendingResponse` the bearer `/login` already returns, with no cookie; a staff account is refused with 403 (mirrors `app/api/routes/session.py`'s own refusal of `visitor`).
- Refactor: `_authenticate_visitor()` factors out the password check, rate limiting and verification gate that both `/login` (bearer) and `/session/login` (cookie) now share — no forked logic.
- No new logout route: `POST /api/session/logout` already clears whichever cookie is present, regardless of role, and needed no change.
- `app/main.py`'s `api_key_middleware` public set gained `/api/public/auth/session/login`.
- Tests: `tests/test_portal_session_login.py` (new, 6 tests — success sets cookie+CSRF, email-identifier login, `remember` sets Max-Age, a staff account is refused with no cookie, an unverified visitor still gets the fresh-code response with no cookie, wrong password sets nothing) and one addition to `tests/test_security_headers.py`'s existing `...pass_the_api_key_gate` test for the new path. All pass on Postgres (44/44 across the touched-file suites: `test_portal_session_login`, `test_security_headers`, `test_public_auth_ip_budget`, `test_session_cookie`).
- No migration: no model changes.

## Frontend files
- `frontend-next/src/app/portal/layout.tsx` — mounts `ConfirmProvider` (must be a client component, since it hands a component prop to a client child — a server component crashed the page outright, fixed in commit 5).
- `frontend-next/src/app/portal/page.tsx` — landing/auth screen, SSR metadata, `GET /api/public/auth/status` check.
- `frontend-next/src/app/portal/me/page.tsx` — signed-in dashboard, `robots: noindex`.
- `frontend-next/src/components/portal/auth-view.tsx` — login/register/verify state machine.
- `frontend-next/src/components/portal/dashboard.tsx` — request form, my-requests list, ticket card.
- `frontend-next/src/app/panel/(app)/portal/page.tsx` — thin, renders the admin view.
- `frontend-next/src/components/admin-portal/requests-view.tsx` — the admin table.

No shared files edited (`kit.tsx`, `lib/*`, `app-shell.tsx`, `nav.ts`, `viz.tsx`, `globals.css` untouched). `useSession()` and `api()` from the panel's own `lib/` are reused as-is for the portal — both are role-agnostic already, so no `useVisitorSession` was needed. The admin table imports `CustomerMatchesSheet` from the CRM stream's `src/components/crm/customers/matches-sheet.tsx` (reused, not copied).

## Checks
- `cd frontend-next && npx tsc --noEmit && npm run lint && npx next build` — all clean.
- `python scripts/lint_new_code.py --base origin/claude/phase-4-nextjs-frontend-ca3150` — clean (root `npm install` was needed once for the e2e `.spec.js` files to be checked; that install has no effect on any config, `frontend/js/**` is still the only globbed path).
- pytest on Postgres: 44/44 for the touched-file suites; a full `pytest tests/ -q` run also completed with no failures.
- e2e: `tests/e2e/next/portal-visitor.spec.js` (4 tests) and `tests/e2e/next/admin-portal.spec.js` (1 test), run via `E2E_DEV=1` against this stream's own backend (`PUBLIC_AUTH_ENABLED=true`, port 8020) and `next dev` (port 3010) — **15/15** across desktop, android and iphone (iphone via Chromium's device emulation; no real WebKit binary on this box, same fallback `playwright.next.config.js` already provides). No horizontal scroll, no console/CSP errors beyond the dev-only inline-style ones `helpers.js` already ignores, `a11y()` returns `[]` everywhere.
- Screenshots (not committed): landing (desktop dark, iPhone light), register tab (desktop dark), signed-in dashboard (desktop dark, three sections), admin table (desktop dark, iPhone light) — all read and checked by eye; no layout issues found after the Tabs fix.

## What the coordinator needs to know
- **Ingress**: `k8s/base/ingress.yaml`'s HTTPS Ingress currently has no rule for `/portal` — it falls through to the catch-all `path: /, backend: backend:3000`→(port 8000), same as everything else not yet moved. `/portal` and `/portal/me` (and any future subpaths) need a `pathType: Prefix` rule to `web:3000`, inserted the same way `/panel` and `/_next` already are (before the catch-all `/`). Not done here, per the task's own instruction — this is the coordinator's call once this branch is reviewed.
- The old `frontend/portal.html` + `frontend/js/portal.js` were not touched or deleted — they keep serving `/portal` on the `backend` pod (via `app/main.py`'s `is_dashboard` check, which already lists `request.url.path == "/portal"` as public) until the ingress change above supersedes them.
- Nothing from `~/SorinFlow-backups` was opened or read.
- No secrets in code, commits or logs. Commit identity throughout: `sobhan azimzadeh` only, no `Co-Authored-By`/`Claude-Session` lines, no AI/Claude mention anywhere.
