"""
The unified pipeline: buffer here, ship on a timer.

Logs are appended to a bounded in-memory ring by a loguru sink; a background
task drains it into Cloud Logging and Pub/Sub, and pushes a metric snapshot to
Cloud Monitoring, on an interval. Nothing in a request path ever waits on
Google.

Bounded on purpose. When the host cannot reach Google — the normal state here —
the buffer would otherwise grow until the pod is killed, turning a disabled
integration into an outage. It drops the oldest records instead and counts what
it dropped.
"""
import asyncio
import time
from collections import deque
from typing import Any, Optional

from loguru import logger

from app.config import get_settings
from app.services.gcp.client import GCPUnavailable, gcp_client
from app.services.gcp import exporters

settings = get_settings()

_buffer: deque = deque(maxlen=5000)
_stats = {
    "buffered": 0, "exported_logs": 0, "exported_metrics": 0,
    "published": 0, "dropped": 0, "failures": 0,
    "last_export_at": None, "last_error": None,
}


def sink(message) -> None:
    """loguru sink. Must be cheap and must never raise: it runs inside every
    log call, including the ones reporting that the exporter is broken."""
    try:
        if not settings.gcp_enabled:
            return
        rec = message.record
        if _buffer.maxlen and len(_buffer) == _buffer.maxlen:
            _stats["dropped"] += 1
        _buffer.append({
            "time": rec["time"].isoformat(),
            "level": rec["level"].name,
            "message": rec["message"],        # already redacted by the filter
            "name": rec["name"],
            "function": rec["function"],
            "line": rec["line"],
            "job_id": rec["extra"].get("job_id"),
            "req_id": rec["extra"].get("req_id"),
        })
        _stats["buffered"] = len(_buffer)
    except Exception:
        pass


def _metric_snapshot() -> list[tuple[str, float, dict]]:
    """Current values from the local registry, as Cloud Monitoring samples.

    Read from the same registry /metrics serves, so the two can never disagree
    about what the numbers are.
    """
    out: list[tuple[str, float, dict]] = []
    try:
        from app import metrics as mx
        for family in mx.REGISTRY.collect():
            # Histogram internals would multiply the series count for little
            # value in a cloud dashboard; counters and gauges carry the signal.
            if family.type not in ("counter", "gauge"):
                continue
            for sample in family.samples:
                if sample.name.endswith("_created"):
                    continue
                out.append((sample.name, sample.value, dict(sample.labels)))
    except Exception as e:
        logger.debug(f"[gcp] metric snapshot failed: {e}")
    return out[:200]


async def _flush_once() -> None:
    if not gcp_client.enabled or not _buffer:
        return

    batch = [_buffer.popleft() for _ in range(min(len(_buffer), settings.gcp_batch_size))]
    try:
        _stats["exported_logs"] += await exporters.export_logs(batch)
        if settings.gcp_pubsub_topic:
            _stats["published"] += await exporters.publish_to_pubsub(batch)
        _stats["exported_metrics"] += await exporters.export_metrics(_metric_snapshot())
        _stats["last_export_at"] = time.time()
        _stats["last_error"] = None
    except GCPUnavailable as e:
        # Put them back at the front so nothing is lost to a transient failure,
        # but only if there is room — a permanently unreachable Google must not
        # be able to fill the buffer through the retry path either.
        _stats["failures"] += 1
        _stats["last_error"] = str(e)[:200]
        for rec in reversed(batch):
            if _buffer.maxlen and len(_buffer) >= _buffer.maxlen:
                _stats["dropped"] += 1
                break
            _buffer.appendleft(rec)
    except Exception as e:
        _stats["failures"] += 1
        _stats["last_error"] = f"unexpected: {e}"[:200]
        logger.warning(f"[gcp] exporter error: {e}")


async def exporter_loop() -> None:
    """Background task started in the app lifespan."""
    if not settings.gcp_enabled:
        logger.info("[gcp] integration disabled — exporter not started")
        return

    logger.info(
        f"[gcp] exporter started — project={settings.gcp_project_id} "
        f"every {settings.gcp_export_interval}s")
    consecutive_failures = 0
    while True:
        try:
            await asyncio.sleep(settings.gcp_export_interval)
            before = _stats["failures"]
            await _flush_once()
            if _stats["failures"] > before:
                consecutive_failures += 1
                # Back off rather than hammering an unreachable endpoint every
                # interval; caps at ~10 minutes so it recovers on its own once
                # the network does.
                if consecutive_failures in (5, 20, 50):
                    logger.warning(
                        f"[gcp] {consecutive_failures} consecutive export failures: "
                        f"{_stats['last_error']}")
                await asyncio.sleep(min(consecutive_failures * 15, 600))
            else:
                consecutive_failures = 0
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"[gcp] exporter loop error: {e}")


def stats() -> dict:
    return {**_stats, "buffer_size": len(_buffer),
            "buffer_capacity": _buffer.maxlen,
            "enabled": settings.gcp_enabled,
            "configured": gcp_client.enabled}
