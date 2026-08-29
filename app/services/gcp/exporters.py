"""
The four things we send to, or read from, Google Cloud.

  entries:write   — application logs into Cloud Logging
  timeSeries      — custom metrics into Cloud Monitoring
  topics:publish  — the same log entries onto Pub/Sub, for a pipeline that
                    wants them somewhere other than Cloud Logging
  timeSeries.list — platform metrics back out, for Compute Engine, Cloud Run,
                    Cloud Functions and Cloud SQL

Everything batches, and everything fails soft. This runs on a host that usually
cannot reach Google, so a failed export must cost one log line and a status
flag — never a request, never the scraper.
"""
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from loguru import logger

from app.config import get_settings
from app.services.gcp.client import GCPUnavailable, gcp_client

settings = get_settings()

_LOGGING_WRITE = "https://logging.googleapis.com/v2/entries:write"
_MONITORING = "https://monitoring.googleapis.com/v3/projects/{project}/timeSeries"
_PUBSUB = "https://pubsub.googleapis.com/v1/projects/{project}/topics/{topic}:publish"

# Cloud Logging maps its severities onto these names; loguru's do not all match.
_SEVERITY = {
    "TRACE": "DEBUG", "DEBUG": "DEBUG", "INFO": "INFO", "SUCCESS": "NOTICE",
    "WARNING": "WARNING", "ERROR": "ERROR", "CRITICAL": "CRITICAL",
}


def _resource() -> dict:
    """How this service identifies itself in Cloud Logging and Monitoring.

    generic_node rather than k8s_container: the cluster is self-hosted k3s, not
    GKE, so the k8s_* resource types would require cluster/location labels that
    describe a GKE cluster this is not. generic_node is the type Google
    documents for exactly this case.
    """
    return {
        "type": "generic_node",
        "labels": {
            "project_id": gcp_client.project_id,
            "location": settings.gcp_location,
            "namespace": settings.gcp_namespace,
            "node_id": settings.gcp_node_id,
        },
    }


async def export_logs(records: list[dict]) -> int:
    """Ship structured log records to Cloud Logging. Returns how many landed.

    Records arrive already redacted — the sink filter runs before anything gets
    here, which matters more for this destination than for the local file.
    """
    if not records:
        return 0

    entries = []
    for rec in records[: settings.gcp_batch_size]:
        entries.append({
            "severity": _SEVERITY.get(str(rec.get("level", "INFO")).upper(), "DEFAULT"),
            "timestamp": rec.get("time") or datetime.now(timezone.utc).isoformat(),
            # jsonPayload, not textPayload: the fields stay queryable in Cloud
            # Logging instead of collapsing into one string, which is the whole
            # point of "structured analysis".
            "jsonPayload": {
                "message": rec.get("message", ""),
                "logger": rec.get("name"),
                "function": rec.get("function"),
                "line": rec.get("line"),
                "job_id": rec.get("job_id"),
                "req_id": rec.get("req_id"),
            },
            "labels": {"service": "sorinflow"},
        })

    await gcp_client.request("POST", _LOGGING_WRITE, json_body={
        "logName": f"projects/{gcp_client.project_id}/logs/{settings.gcp_log_name}",
        "resource": _resource(),
        "entries": entries,
    })
    return len(entries)


async def export_metrics(samples: list[tuple[str, float, dict]]) -> int:
    """Write custom metrics as Cloud Monitoring time series.

    Each sample is (metric_name, value, labels). Sent as GAUGE points at now:
    Cloud Monitoring rejects a CUMULATIVE series without a start time it agrees
    with, and gauges are what a dashboard reads anyway.
    """
    if not samples:
        return 0

    now = datetime.now(timezone.utc).isoformat()
    series = [{
        "metric": {
            "type": f"custom.googleapis.com/sorinflow/{name}",
            "labels": {k: str(v) for k, v in (labels or {}).items()},
        },
        "resource": _resource(),
        "points": [{
            "interval": {"endTime": now},
            "value": {"doubleValue": float(value)},
        }],
    } for name, value, labels in samples[: settings.gcp_batch_size]]

    await gcp_client.request(
        "POST", _MONITORING.format(project=gcp_client.project_id),
        json_body={"timeSeries": series})
    return len(series)


async def publish_to_pubsub(records: list[dict]) -> int:
    """Put the same log records on a Pub/Sub topic.

    For a pipeline that wants them somewhere other than Cloud Logging —
    BigQuery through a subscription, or another consumer entirely. Off unless a
    topic is configured, because publishing to a topic nobody reads is cost
    without a reader.
    """
    import base64
    import json as _json

    if not records or not settings.gcp_pubsub_topic:
        return 0

    messages = [{
        "data": base64.b64encode(_json.dumps(r, ensure_ascii=False).encode()).decode(),
        "attributes": {"service": "sorinflow",
                       "severity": str(r.get("level", "INFO"))},
    } for r in records[: settings.gcp_batch_size]]

    await gcp_client.request(
        "POST", _PUBSUB.format(project=gcp_client.project_id,
                               topic=settings.gcp_pubsub_topic),
        json_body={"messages": messages})
    return len(messages)


# ── reading platform metrics back out ───────────────────────────────────────

# The one metric worth showing per service, and what it means. Deliberately
# short: a monitoring screen that lists forty series is one nobody reads.
GCP_SERVICES = {
    "compute": ("Compute Engine", "compute.googleapis.com/instance/cpu/utilization"),
    "cloudrun": ("Cloud Run", "run.googleapis.com/container/cpu/utilizations"),
    "functions": ("Cloud Functions", "cloudfunctions.googleapis.com/function/execution_count"),
    "cloudsql": ("Cloud SQL", "cloudsql.googleapis.com/database/cpu/utilization"),
}


async def read_service_metric(service: str, minutes: int = 30) -> dict:
    """Latest value of one platform metric, over the last `minutes`."""
    if service not in GCP_SERVICES:
        raise ValueError(f"unknown service: {service}")

    label, metric_type = GCP_SERVICES[service]
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=minutes)

    body = await gcp_client.request(
        "GET", _MONITORING.format(project=gcp_client.project_id),
        params={
            "filter": f'metric.type="{metric_type}"',
            "interval.startTime": start.isoformat().replace("+00:00", "Z"),
            "interval.endTime": end.isoformat().replace("+00:00", "Z"),
            "aggregation.alignmentPeriod": "300s",
            "aggregation.perSeriesAligner": "ALIGN_MEAN",
        })

    points = []
    for s in (body.get("timeSeries") or []):
        for p in (s.get("points") or []):
            v = p.get("value", {})
            points.append({
                "t": p.get("interval", {}).get("endTime"),
                "v": v.get("doubleValue", v.get("int64Value")),
                "resource": (s.get("resource", {}).get("labels", {})
                             .get("instance_id")
                             or s.get("resource", {}).get("labels", {}).get("service_name")),
            })

    return {"service": service, "label": label, "metric": metric_type,
            "points": points, "series": len(body.get("timeSeries") or [])}
