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
# no .pyc write either, so nothing else under /app needs to be writable.
RUN mkdir -p /app/data/images /app/data/cookies /app/logs && \
    chown -R pwuser:pwuser /app
USER pwuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

# X-Forwarded-For is believed only from inside the cluster (k3s's pod network,
# where Traefik runs). With "*" uvicorn took the leftmost entry — whatever the
# caller typed — as the client; now it takes the rightmost address not in this
# range, which is the one Traefik wrote.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "10.42.0.0/16"]
