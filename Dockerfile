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

RUN mkdir -p /app/data/images /app/data/cookies /app/logs

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

# X-Forwarded-For is believed only from inside the cluster (k3s's pod network,
# where Traefik runs). With "*" uvicorn took the leftmost entry — whatever the
# caller typed — as the client; now it takes the rightmost address not in this
# range, which is the one Traefik wrote.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "10.42.0.0/16"]
