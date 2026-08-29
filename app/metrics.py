"""
Prometheus-format metrics for SorinFlow.

Self-hosted and self-contained: the server sits in Iran behind sanctions, so
nothing here talks to a cloud provider. It exposes the numbers over a text
endpoint any scraper can read, and the panel's own monitoring screen reads the
same registry through app/api/routes/monitoring.py.

A private registry rather than the global default: the default one carries
process and GC collectors that would be exported whether or not anything wants
them, and having our own means a stray prometheus_client import elsewhere in a
dependency cannot inject series into our output.
"""
import shutil
import time
from typing import Optional

from prometheus_client import (
    CollectorRegistry, Counter, Gauge, Histogram,
    ProcessCollector, generate_latest, CONTENT_TYPE_LATEST,
)

REGISTRY = CollectorRegistry()

# CPU, resident memory and open file descriptors, read from /proc inside the
# container. Free, no privileges, and it is the "resource utilisation" half of
# what monitoring is for.
#
# Silent on macOS: it reads /proc, which only Linux has, so a developer running
# this on a laptop sees no process_* series and nothing is wrong. The container
# is Ubuntu jammy, where it works.
ProcessCollector(registry=REGISTRY)

# ── HTTP ────────────────────────────────────────────────────────────────────
# Labelled with a route GROUP, never the raw path. FastAPI paths carry ids
# (/api/properties/1234), and one series per id is how a metrics endpoint turns
# into the thing that runs the server out of memory.
http_requests = Counter(
    "sorinflow_http_requests_total", "HTTP requests",
    ["route", "method", "status"], registry=REGISTRY,
)
http_latency = Histogram(
    "sorinflow_http_request_seconds", "HTTP request duration",
    ["route"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 10.0),
    registry=REGISTRY,
)

# ── scraper ─────────────────────────────────────────────────────────────────
scrape_listings = Counter(
    "sorinflow_scraper_listings_total", "Listings processed by outcome",
    ["outcome"], registry=REGISTRY,          # saved | skipped | failed | duplicate
)
scrape_reveals = Counter(
    "sorinflow_scraper_contact_reveals_total",
    "Contact-info reveals — the unit Divar counts before demanding a code",
    registry=REGISTRY,
)
scrape_rotations = Counter(
    "sorinflow_scraper_account_rotations_total", "Divar account switches",
    ["reason"], registry=REGISTRY,           # threshold | challenged
)
scrape_challenges = Counter(
    "sorinflow_scraper_otp_challenges_total",
    "Times Divar demanded an SMS code", registry=REGISTRY,
)
scrape_images = Counter(
    "sorinflow_scraper_images_total", "Image downloads by outcome",
    ["outcome"], registry=REGISTRY,          # saved | too_big | too_many | undecodable
)
scrape_jobs = Gauge(
    "sorinflow_scraper_jobs", "Scraping jobs by status",
    ["status"], registry=REGISTRY,
)

# ── business + storage ──────────────────────────────────────────────────────
properties_total = Gauge("sorinflow_properties_total", "Properties stored", registry=REGISTRY)
leads_total = Gauge("sorinflow_leads_total", "Leads stored", registry=REGISTRY)
disk_free = Gauge(
    "sorinflow_images_disk_free_bytes",
    "Free space on the volume holding scraped images", registry=REGISTRY,
)
dep_up = Gauge(
    "sorinflow_dependency_up", "1 when a dependency answered, 0 when it did not",
    ["dependency"], registry=REGISTRY,       # postgres | redis
)
dep_latency = Gauge(
    "sorinflow_dependency_latency_seconds", "Round trip to a dependency",
    ["dependency"], registry=REGISTRY,
)

_ROUTE_GROUPS = ("/api", "/dashboard", "/images", "/portal")


def route_label(path: str) -> str:
    """Collapse a path to one of a fixed set of buckets.

    Fixed, not derived: a request rejected by the API-key or maintenance
    middleware never reaches the router, so there is no route template to read —
    and without an explicit /api bucket every 401 would land in the same series
    as scanner traffic hitting /wp-admin, which is precisely the signal the
    metrics exist to show.
    """
    for group in _ROUTE_GROUPS:
        if path == group or path.startswith(group + "/"):
            return group
    return "__other__"


def render() -> tuple[bytes, str]:
    """The exposition payload and its content type."""
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST


def sample_disk(path: str) -> None:
    """Free space where images land. The volume filling stops the scraper
    saving anything, and it is the failure that arrives without warning."""
    try:
        disk_free.set(shutil.disk_usage(path).free)
    except Exception:
        pass


class Timer:
    """Context manager that records a dependency round trip and whether it
    answered at all."""

    def __init__(self, dependency: str):
        self.dependency = dependency
        self.start = 0.0

    def __enter__(self):
        self.start = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb):
        dep_latency.labels(self.dependency).set(time.perf_counter() - self.start)
        dep_up.labels(self.dependency).set(0 if exc_type else 1)
        return False
