## What changes / چه چیزی تغییر می‌کند

<!-- One or two sentences. What is different after this is merged? -->

## Why / چرا

<!-- The problem this solves. If it fixes an issue, link it: Fixes #123 -->

## How it was verified / چطور بررسی شد

<!-- What you actually ran, and what it said. "Tests pass" is not evidence;
     "419 passed against Postgres, plus the portal sign-up by hand" is. -->

- [ ] `pytest tests/ -q` passes against PostgreSQL
- [ ] Checked by hand in a running app (`./local/start.sh`)
- [ ] For non-trivial logic: a test that **fails without this change**
      (revert the fix, watch it go red — a test that passes either way proves nothing)

## Checks that keep production safe

<!-- Delete any line that genuinely does not apply. -->

- [ ] No credential in a tracked file, and no setting defaulted to a working one
      ([SECRETS.md](SECRETS.md))
- [ ] User-supplied data reaching `innerHTML` goes through `esc()`
- [ ] New dashboard routes are behind a permission in `app/api/routes/__init__.py`
- [ ] Any migration is additive and idempotent — it re-runs on every pod restart
- [ ] Persian UI strings, RTL-safe layout, `dir="ltr"` for numbers and codes

## Anything reviewers should push back on

<!-- Shortcuts taken, assumptions made, parts you are unsure about.
     Say it here rather than hoping nobody notices. -->

---

⚠️ Merging to `main` deploys to production. CI gates on the test suite, so a red
build never reaches the registry — but a green one goes live.
