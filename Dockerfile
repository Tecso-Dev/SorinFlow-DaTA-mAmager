# ── the new panel (frontend-next): built here, served by the `web` Deployment
# from this same image (node /app/web/server.js), so one image tag still
# describes one release and the deploy pipeline stays as it is.
FROM node:22-bookworm-slim AS web
WORKDIR /web
ENV NEXT_TELEMETRY_DISABLED=1
COPY frontend-next/package.json frontend-next/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend-next/ ./
# No BACKEND_INTERNAL_URL at build time: rewrites stay off and Traefik routes
# /api to the backend (next.config.ts).
RUN npx next build && \
    cp -r .next/static .next/standalone/.next/static && \
    if [ -d public ]; then cp -r public .next/standalone/public; fi

FROM mcr.microsoft.com/playwright/python:v1.41.0-jammy

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.lock .

# requirements.lock pins numpy<2 itself (see requirements.txt), so no separate
# numpy pre-install step is needed before it.
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --require-hashes -r requirements.lock

COPY . .

# The panel's standalone server and the one node binary it needs (the base
# image's own Node belongs to its Playwright driver and is older).
COPY --from=web /usr/local/bin/node /usr/local/bin/node
COPY --from=web /web/.next/standalone /app/web

# The commit this image was built from — deploy.yml passes it as a build-arg
# so /health can report it. Declared after COPY so a changed commit (every
# build) never invalidates the pip install layer above it.
ARG GIT_SHA=""
ENV GIT_SHA=$GIT_SHA

# pwuser (uid/gid 1000, already in this base image) instead of root: every
# role runs as runAsNonRoot in k8s now (k8s/base/backend.yaml etc.), and
# Chromium's own sandbox needs a real unprivileged process to drop into, not
# a root one pretending to be non-root. /app/data and /app/logs are the only
# paths anything writes to at runtime — PYTHONDONTWRITEBYTECODE above means
# no .pyc write either — so only those two change hands. The code stays
# root's: the app cannot rewrite itself, and a `chown -R /app` would copy
# every file into one more layer that each deploy pulls from ghcr to Iran.
RUN mkdir -p /app/data/images /app/data/cookies /app/logs && \
    chown -R pwuser:pwuser /app/data /app/logs
USER pwuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

# X-Forwarded-For is believed only from inside the cluster (k3s's pod network,
# where Traefik runs). With "*" uvicorn took the leftmost entry — whatever the
# caller typed — as the client; now it takes the rightmost address not in this
# range, which is the one Traefik wrote.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "10.42.0.0/16"]
