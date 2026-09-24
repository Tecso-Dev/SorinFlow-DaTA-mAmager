#!/bin/bash
# Bring up the local Docker stack and seed it in one shot.
#   scripts/local_up.sh
set -euo pipefail
cd "$(dirname "$0")/.."

[ -f .env.local ] || cp .env.local.example .env.local

docker compose --env-file .env.local -f docker-compose.local.yml up -d --build

echo "waiting for backend health..."
for _ in $(seq 1 30); do
    curl -sf http://localhost:8000/health > /dev/null 2>&1 && break
    sleep 2
done
curl -sf http://localhost:8000/health > /dev/null || {
    echo "backend never became healthy — check: docker compose -f docker-compose.local.yml logs backend" >&2
    exit 1
}

docker compose -f docker-compose.local.yml exec -T backend python scripts/seed_local.py

echo "up: http://localhost:8000/dashboard/  (stop: docker compose -f docker-compose.local.yml down)"
