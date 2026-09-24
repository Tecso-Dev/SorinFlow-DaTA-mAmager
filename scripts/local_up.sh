#!/bin/bash
# Bring up the local Docker stack and seed it in one shot.
#   scripts/local_up.sh
set -euo pipefail
cd "$(dirname "$0")/.."

[ -f .env.local ] || cp .env.local.example .env.local
# The port this stack answers on — not whatever else holds 8000, whose
# /health would pass for ours.
# An exported BACKEND_PORT beats .env.local for compose, so it does here too.
PORT=${BACKEND_PORT:-$(sed -n 's/^BACKEND_PORT=//p' .env.local)}
PORT=${PORT:-8000}

docker compose --env-file .env.local -f docker-compose.local.yml up -d --build

echo "waiting for backend health on :$PORT..."
for _ in $(seq 1 60); do
    curl -sf "http://localhost:$PORT/health" > /dev/null 2>&1 && break
    sleep 2
done
curl -sf "http://localhost:$PORT/health" > /dev/null || {
    echo "backend never became healthy — check: docker compose -f docker-compose.local.yml logs backend" >&2
    exit 1
}

docker compose -f docker-compose.local.yml exec -T backend python scripts/seed_local.py

echo "up: http://localhost:$PORT/dashboard/  (stop: docker compose -f docker-compose.local.yml down)"
