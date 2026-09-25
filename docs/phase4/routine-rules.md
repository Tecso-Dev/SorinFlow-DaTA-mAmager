# Rules for every Phase 4 cloud stream (read fully before starting)

You build part of Phase 4 of SorinFlow: the old panel (`frontend/`, one page) rebuilt as the new
Next.js panel, portal and landing page in `frontend-next/`. Several streams run at the same time in
separate cloud sessions; a coordinator on the owner's Mac merges your branch, runs the full test
suites on Postgres, the end-to-end tests on desktop/Android/iPhone (WebKit) and a Kubernetes
rehearsal. The product is Persian (RTL): UI text Persian, code comments English.

## Git
- Base: `git fetch origin claude/phase-4-nextjs-frontend-ca3150 && git checkout -B <your-branch> origin/claude/phase-4-nextjs-frontend-ca3150`.
- Commit only as `git config user.name "sobhan azimzadeh"` / `git config user.email "sobhan.gjhav.azimzadeh@gmail.com"`.
  NO `Co-Authored-By`, NO `Claude-Session`, no mention of Claude, AI or an assistant in commits or files.
  Messages start with `feat:`/`fix:`/`test:`/`docs:` and say what was missing or broken, what changed and
  what was checked. Many small commits.
- Push ONLY your branch (`git push origin <your-branch>`), often (after each finished piece), so work
  survives if the session ends. Never push `main`, `sorinflow-v2` or the base branch; no PRs; never run
  workflows; never deploy; never connect to any server. No secrets in code, commits or logs.

## Read first
`CLAUDE.md` (Persian project rules, product rules: a Divar number's codes and prompts belong to its
owner only; root manages but never receives them; ownership changes only in the root-only registry
card), `docs/FRONTEND.md` (the rules every page follows — RTL logical utilities, responsive at 390px
with no sideways page scroll, strict CSP: no inline `<script>`/`<style>`, no `dangerouslySetInnerHTML`,
no CDN; motion and a 3D touch in every section; brand from settings; accessibility),
`docs/phase4/stream-rules.md` (the shared pieces you must reuse — `kit.tsx`, `date-input.tsx`,
`lib/*`, e2e helpers — ignore its `/tmp/...` paths), `frontend-next/AGENTS.md` (Next.js 16 has
breaking changes; its docs are in `frontend-next/node_modules/next/dist/docs/`), your inventory
file in `docs/phase4/` (the checklist: every item must exist in the new panel), and the old code for
your sections (`frontend/js/app.js`, `frontend/index.html`, the routes in `app/api/routes/`).

## Environment (a fresh Linux box)
```bash
# Postgres 16 and Redis (use sudo if you are not root)
apt-get update && apt-get install -y postgresql redis-server
service postgresql start || pg_ctlcluster 16 main start
su postgres -c "createuser -s $(whoami)" 2>/dev/null; createdb sorinflow_local
redis-server --daemonize yes --save "" --appendonly no
# Python 3.11 venv from the hashed lock
pip install uv && uv venv --python 3.11 /tmp/venv && uv pip install --python /tmp/venv/bin/python --require-hashes -r requirements-dev.lock
# The backend with rich sample data, exactly as the e2e suite starts it (read scripts/e2e_up.sh)
E2E_PORT=8020 PYTHON=/tmp/venv/bin/python DATABASE_URL=postgresql+asyncpg://$(whoami)@localhost:5432/sorinflow_local \
  REDIS_URL=redis://localhost:6379/0 setsid nohup bash scripts/e2e_up.sh > /tmp/backend.log 2>&1 &
# The new frontend
cd frontend-next && npm ci --no-audit --no-fund && npx next typegen
BACKEND_INTERNAL_URL=http://127.0.0.1:8020 setsid nohup npx next dev -p 3000 -H 127.0.0.1 > /tmp/next.log 2>&1 &
```
Logins (throwaway, local only): `owner`, `root`, `manager1`, `agent1`, `agent2`, password `local-pass-1234`.
Sign in from a script with `POST http://127.0.0.1:3000/api/session/login` `{"username":"owner","password":"local-pass-1234"}`.
If a step here fails, adapt (another Postgres package name, `sudo`, a different Python), but keep Postgres:
half of the auth and ownership behaviour only exists there.

## Checks (all must pass before you finish)
- `cd frontend-next && npx tsc --noEmit && npm run lint && npx next build`.
- Backend changes: `python scripts/lint_new_code.py --base origin/claude/phase-4-nextjs-frontend-ca3150` (ruff, mypy on
  changed lines), and pytest for the files you touched on Postgres:
  `DATABASE_URL=postgresql+asyncpg://$(whoami)@localhost:5432/sorinflow_test REDIS_URL=redis://localhost:6379/9 SECRET_KEY=$(python3 -c "import secrets;print(secrets.token_hex(32))") LOGS_PATH=/tmp IMAGES_PATH=/tmp /tmp/venv/bin/python -m pytest <files> -q`
  (create `sorinflow_test` first). A new or changed route gets a behavioural HTTP test; a route a browser
  calls without the API key must be listed in `api_key_middleware`'s public set and tested with
  `settings.api_key` set (see `tests/test_security_headers.py::...pass_the_api_key_gate`). Migrations
  only additive; do not add one unless unavoidable (next free revision: check `alembic heads`; `0017`
  belongs to another stream).
- E2E: add `tests/e2e/next/<your-stream>-*.spec.js` (helpers in `tests/e2e/next/helpers.js`: signIn,
  watchProblems, noHorizontalScroll, scrollThrough, a11y). For each page: it loads with data, the main
  create/edit/delete flows through the UI, filters, no horizontal scroll, no console errors or CSP
  violations, and `a11y(page)` returns no serious/critical finding. Run them on all three projects:
  `npm ci --prefix tests/e2e && npx --prefix tests/e2e playwright install --with-deps chromium webkit`, then
  `E2E_DEV=1 E2E_NEXT_URL=http://127.0.0.1:3000 E2E_API_URL=http://127.0.0.1:8020 E2E_WEBKIT=1 npx --prefix tests/e2e playwright test --config tests/e2e/playwright.next.config.js <your specs>`.
  Use unique names (`e2e-<stream>-${Date.now()}`) so parallel data does not collide.
- LOOK at your pages: screenshot desktop 1440×900 dark and iPhone light with Playwright, Read the PNGs,
  fix what looks wrong. Do not commit screenshots.

## Files
Put your pages under `frontend-next/src/app/...` for your routes and your components under
`frontend-next/src/components/<your-area>/`. Do not edit shared files (`kit.tsx`, `date-input.tsx`,
`lib/*`, `app-shell.tsx`, `nav.ts`, `globals.css`, `viz.tsx`) except for the smallest additive change you
truly need — list every such change in your report. New npm dependencies only if listed in your task.

## Finish
Write `docs/phase4/reports/<your-branch>.md` (English is fine): what you built against every inventory
item (done / not done and why), shared-file and backend changes (with their tests), test results
(tsc, lint, build, pytest counts, e2e counts per project), and what the coordinator must know
(new env vars, new routes the ingress must send to the web pod, anything left). Commit it and push.
