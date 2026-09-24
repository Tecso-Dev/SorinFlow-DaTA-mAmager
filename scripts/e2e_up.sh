#!/bin/bash
# App startup for the Playwright e2e suite (tests/e2e). Invoked as
# playwright.config.js's webServer command — Playwright starts it, polls
# /health, runs the suite against it, then kills it.
#
# Runs schema creation and seeding to completion *before* uvicorn starts
# listening, so Playwright's /health poll can never observe a server that is
# up but not yet seeded (no sleeps, no race to paper over).
#
# Every switch below turns off something that would otherwise dial out
# (Divar, Telegram, an LLM, Google) — same reasoning as .env.local.example,
# plus MATCH_ENGINE, which that file leaves on.
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3}"
PORT="${E2E_PORT:-8111}"

export ENVIRONMENT=development
export DEBUG=true
export DATABASE_URL="${DATABASE_URL:-postgresql+asyncpg://macbook@localhost:5432/sorinflow_e2e_local}"
export REDIS_URL="${REDIS_URL:-redis://localhost:6379/11}"
export SECRET_KEY="${SECRET_KEY:-$("$PYTHON" -c 'import secrets; print(secrets.token_hex(32))')}"

# Seeded logins (scripts/seed_local.py adds manager1/agent1/agent2; root and
# owner are app/database.py:init_db's own boot-time seed, from these).
export SUPER_ADMIN_USERNAME="${SUPER_ADMIN_USERNAME:-owner}"
export SUPER_ADMIN_PASSWORD="${SUPER_ADMIN_PASSWORD:-local-pass-1234}"
export ROOT_USERNAME="${ROOT_USERNAME:-root}"
export ROOT_PASSWORD="${ROOT_PASSWORD:-local-pass-1234}"
export ROOT_EMAIL="${ROOT_EMAIL:-root@local.test}"

# Nothing here may reach the outside world.
export SCRAPE_SCHEDULER=false
export MATCH_ENGINE=false              # also gates the reader/embed/photo AI loops
export DIVAR_SESSION_CHECK_MINUTES=0   # the loop that pings divar.ir with the seeded fake cookies
export APK_MIRROR_HOURS=0              # mirrors the forwarder APK from GitHub
export FORWARDER_WATCH_MINUTES=0       # would email about forwarders that don't exist here
export PROXY_ENABLED=false
export GCP_ENABLED=false
export PUBLIC_AUTH_ENABLED=false
export DIGEST_HOUR=-1                  # the daily Telegram digest
export TELEGRAM_BOT_TOKEN=
export TELEGRAM_CHAT_ID=
export LLM_API_KEY=
export CORS_ORIGINS=
export SERVER_IP=127.0.0.1
export DOMAIN=localhost

# A scratch tree of its own — not the /tmp the pytest suite's LOGS_PATH/
# IMAGES_PATH point at, so a long-lived e2e server can't collide with another
# stream's short-lived test run also writing to /tmp.
DATA_DIR="${TMPDIR:-/tmp}/sorinflow-e2e-local"
mkdir -p "$DATA_DIR/cookies" "$DATA_DIR/images" "$DATA_DIR/downloads" "$DATA_DIR/logs"
export COOKIES_PATH="$DATA_DIR/cookies"
export IMAGES_PATH="$DATA_DIR/images"
export DOWNLOADS_PATH="$DATA_DIR/downloads"
export LOGS_PATH="$DATA_DIR/logs"

echo "[e2e] ensuring database exists..."
"$PYTHON" scripts/e2e_ensure_db.py

echo "[e2e] creating schema + seeding root/owner (app/database.py:init_db)..."
"$PYTHON" -c "import asyncio; from app.database import init_db; asyncio.run(init_db())"

echo "[e2e] seeding staff + sample listings/leads/customers (scripts/seed_local.py)..."
"$PYTHON" scripts/seed_local.py

echo "[e2e] starting uvicorn on :$PORT..."
exec "$PYTHON" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT"
