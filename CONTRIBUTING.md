# Contributing — مشارکت در توسعه

## Before you start

`main` is the only branch, and **pushing to it deploys to production**
(`.github/workflows/deploy.yml` builds and rolls out to the live cluster). Treat
every commit as something real users will meet within minutes.

## Running it locally

```bash
./local/start.sh
```

That creates a local PostgreSQL cluster, starts Redis, and boots the API on
<http://127.0.0.1:8010>. It prints the login credentials and is safe to re-run —
it reuses whatever is already up. `./local/stop.sh` shuts everything down and
keeps your data.

Local configuration lives in `local/local.env`, which git ignores. It never
shares credentials with production, and real environment variables take
precedence over `.env`, so a local run cannot pick up a production value by
accident.

## Tests

```bash
pytest tests/ -q
```

The suite needs PostgreSQL: `scraping_jobs.job_id` is a `postgresql.UUID`
column that the pinned SQLAlchemy cannot render on SQLite, so schema-building
tests skip with a message rather than failing cryptically. Point `DATABASE_URL`
at a real database to run everything:

```bash
DATABASE_URL=postgresql+asyncpg://sf@127.0.0.1:55432/sorinflow_test pytest tests/ -q
```

To also exercise the migration DDL against a live Postgres — the half SQLite can
never cover, because `ALTER ... IF NOT EXISTS` is a no-op there:

```bash
PG_TEST_URL=postgresql+asyncpg://sf@127.0.0.1:55432/sorinflow_test \
  pytest tests/test_pg_migration.py -q
```

CI runs both against real PostgreSQL and Redis services, and **nothing reaches
the registry unless they pass**.

## Linting

```bash
pre-commit install                       # once per clone — see .pre-commit-config.yaml
python scripts/lint_new_code.py          # the same gate by hand, against origin/main
npx eslint frontend/js frontend/*.js     # the panel's JS on its own, whole tree
```

ruff, mypy and eslint each have a long list of pre-existing findings on this
codebase — turning any of them into a gate on the *whole* tree would either
block the next unrelated commit until someone works through that backlog, or
get disabled within a week. `scripts/lint_new_code.py` is the actual gate
(pre-commit hook and CI both call it): it runs all three but only reports a
finding on a line the change itself added or touched, so nothing already
there has to be fixed to get a commit in. `--staged` lints what is about to
be committed; with no flags it diffs against `origin/main`, same as CI.

CI also runs `pytest --cov=app --cov-report=xml --cov-report=term:skip-covered`
and uploads the report as a build artifact. There is no enforced minimum —
watch the trend, do not chase a number.

## What good work looks like here

- **Fix the cause, not the symptom.** If a guard is missing in one caller, check
  every sibling caller before patching the one in the ticket.
- **Leave a runnable check behind.** Non-trivial logic — a branch, a parser, a
  money path, anything touching auth — gets a test that fails if the logic
  breaks. A test that passes whether or not the fix is present proves nothing;
  revert your fix and watch it fail before you trust it.
- **Comment the *why*, not the *what*.** The code says what it does. Explain the
  constraint that made it look like this, especially where it looks odd.
- **Match the surrounding code.** Naming, structure, comment density.
- **The UI is Persian and RTL.** New strings are Persian; use logical CSS
  properties, and keep phone numbers, money and codes in `dir="ltr"` islands.

## Things that will break production

- Never write a credential into a tracked file. See [SECRETS.md](SECRETS.md).
- Never default a setting to a working credential — an unset value must fail
  loudly, not quietly connect.
- Anything user-supplied that reaches `innerHTML` must go through `esc()`. The
  token lives in `localStorage`, so stored XSS is account takeover.
- New dashboard routes belong behind a permission in
  `app/api/routes/__init__.py`. A route mounted without one is reachable by any
  signed-in account, including a public portal visitor.
- Schema changes are Alembic revisions (`alembic revision --autogenerate -m
  "..."` against a local Postgres, then read what it wrote). The app applies
  them at boot under a 5-second lock timeout, so keep each one small and
  additive, and guard it against the change already being there (inspect the
  catalog first — `migrations/versions/0002_*` is the pattern): a database
  whose tables `create_all` built from newer models is stamped at the baseline
  and replays every revision. `alembic check` runs in CI and fails on any
  model that drifts from the schema. The older hand-written steps in `app/database.py` only
  bring pre-baseline databases up — do not add to them.

## Commits

Conventional-commit prefixes (`feat:`, `fix:`, `perf:`, `docs:`, `ci:`), with a
body explaining *why*. Persian in the subject is fine and normal here.

Write the body for whoever is reading it at 3am during an incident: what was
broken, what changes, and what you checked. "Fixed bug" tells them nothing.
