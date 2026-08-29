"""
Google Cloud Observability integration.

The load-bearing property is not that it exports — it is that it does no harm
when it cannot. This server is in Iran, where Google's endpoints are blocked, so
"unreachable" is the normal state and every path has to survive it: the buffer
must not grow without bound, the exporter must not wedge the event loop, and no
request may ever wait on Google.
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_gcp.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")


@pytest.fixture(autouse=True)
def clean_pipeline():
    from app.services.gcp import pipeline
    pipeline._buffer.clear()
    for k in ("exported_logs", "dropped", "failures", "published", "exported_metrics"):
        pipeline._stats[k] = 0
    pipeline._stats["last_error"] = None
    yield
    pipeline._buffer.clear()


# ── it ships disabled ───────────────────────────────────────────────────────

def test_disabled_by_default():
    """Google is unreachable from this server; on by default would mean a
    background task failing forever on every deploy."""
    from app.config import Settings
    assert Settings().gcp_enabled is False


def test_the_sink_does_nothing_while_disabled():
    from app.services.gcp import pipeline

    class Msg:
        record = {"time": None, "level": type("L", (), {"name": "INFO"})(),
                  "message": "x", "name": "n", "function": "f", "line": 1, "extra": {}}
    pipeline.sink(Msg())
    assert len(pipeline._buffer) == 0


def test_client_reports_itself_unconfigured_rather_than_raising():
    from app.services.gcp import gcp_client
    assert gcp_client.enabled is False


# ── it fails soft ───────────────────────────────────────────────────────────

def test_request_raises_the_typed_error_when_disabled():
    from app.services.gcp import gcp_client
    from app.services.gcp.client import GCPUnavailable
    with pytest.raises(GCPUnavailable):
        asyncio.run(gcp_client.request("GET", "https://example.invalid"))


def test_a_failed_export_returns_records_to_the_buffer(monkeypatch):
    """A transient failure must not lose logs."""
    from app.services.gcp import pipeline, exporters
    from app.services.gcp.client import GCPUnavailable
    from app.config import get_settings

    cfg = get_settings()
    saved = cfg.gcp_enabled
    cfg.gcp_enabled = True
    try:
        for i in range(5):
            pipeline._buffer.append({"level": "INFO", "message": f"m{i}"})

        async def boom(*a, **k):
            raise GCPUnavailable("network unreachable")
        monkeypatch.setattr(exporters, "export_logs", boom)
        monkeypatch.setattr(type(pipeline.gcp_client), "enabled",
                            property(lambda self: True))

        asyncio.run(pipeline._flush_once())
        assert len(pipeline._buffer) == 5, "records were lost on a failed export"
        assert pipeline._stats["failures"] == 1
        assert "unreachable" in (pipeline._stats["last_error"] or "")
    finally:
        cfg.gcp_enabled = saved


def test_the_buffer_is_bounded_so_an_unreachable_google_cannot_kill_the_pod():
    """The failure mode that matters here. Without a cap, a permanently
    unreachable Google turns a disabled-in-practice integration into an OOM."""
    from app.services.gcp import pipeline
    cap = pipeline._buffer.maxlen
    assert cap and cap <= 10000

    for i in range(cap + 500):
        pipeline._buffer.append({"level": "INFO", "message": f"m{i}"})
    assert len(pipeline._buffer) == cap

    # and the oldest are the ones dropped, so recent context survives
    assert pipeline._buffer[-1]["message"] == f"m{cap + 499}"


def test_status_distinguishes_off_unconfigured_and_unreachable():
    """Three states that look identical from outside, and need different fixes."""
    from app.services.gcp import pipeline
    st = pipeline.stats()
    assert st["enabled"] is False
    assert st["configured"] is False
    assert "buffer_capacity" in st


# ── payload shape ───────────────────────────────────────────────────────────

def test_logs_are_sent_as_structured_json_not_flattened_text(monkeypatch):
    """jsonPayload keeps the fields queryable in Cloud Logging — a textPayload
    would collapse them into one string, losing the 'structured analysis' this
    exists for."""
    from app.services.gcp import exporters, gcp_client
    captured = {}

    async def fake_request(method, url, *, json_body=None, params=None):
        captured["body"] = json_body
        return {}
    monkeypatch.setattr(gcp_client, "request", fake_request)
    monkeypatch.setattr(type(gcp_client), "project_id", property(lambda self: "proj"))

    asyncio.run(exporters.export_logs([{
        "level": "ERROR", "message": "boom", "name": "app.x",
        "function": "f", "line": 12, "job_id": "j1",
    }]))
    entry = captured["body"]["entries"][0]
    assert entry["severity"] == "ERROR"
    assert entry["jsonPayload"]["message"] == "boom"
    assert entry["jsonPayload"]["job_id"] == "j1"
    assert "textPayload" not in entry
    # self-hosted k3s is not GKE, so the resource type must not claim to be
    assert captured["body"]["resource"]["type"] == "generic_node"


def test_metrics_are_sent_as_gauges_with_the_custom_prefix(monkeypatch):
    from app.services.gcp import exporters, gcp_client
    captured = {}

    async def fake_request(method, url, *, json_body=None, params=None):
        captured["body"] = json_body
        return {}
    monkeypatch.setattr(gcp_client, "request", fake_request)
    monkeypatch.setattr(type(gcp_client), "project_id", property(lambda self: "proj"))

    asyncio.run(exporters.export_metrics([("http_requests_total", 42.0, {"route": "/api"})]))
    series = captured["body"]["timeSeries"][0]
    assert series["metric"]["type"].startswith("custom.googleapis.com/sorinflow/")
    assert series["points"][0]["value"]["doubleValue"] == 42.0
    assert series["metric"]["labels"]["route"] == "/api"


def test_every_gcp_service_the_brief_named_is_covered():
    from app.services.gcp.exporters import GCP_SERVICES
    assert set(GCP_SERVICES) == {"compute", "cloudrun", "functions", "cloudsql"}
