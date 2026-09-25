#!/bin/bash
# The new panel (frontend-next) for tests/e2e/playwright.next.config.js:
# a production build, served by `next start`, whose /api, /images and
# /downloads go to the e2e backend (scripts/e2e_up.sh on :8111).
#
# BACKEND_INTERNAL_URL is read at build time (next.config.ts rewrites), so
# the build here is the e2e's own; the Docker image is built without it and
# Traefik routes those paths instead.
set -euo pipefail
cd "$(dirname "$0")/../frontend-next"

PORT="${E2E_NEXT_PORT:-3111}"
export BACKEND_INTERNAL_URL="${BACKEND_INTERNAL_URL:-http://127.0.0.1:${E2E_PORT:-8111}}"
export NEXT_TELEMETRY_DISABLED=1

[ -d node_modules ] || npm ci --no-audit --no-fund
npx next build
exec npx next start -p "$PORT" -H 127.0.0.1
