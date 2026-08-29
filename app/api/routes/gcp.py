"""
Google Cloud Observability — status, service metrics and a connectivity test.

Admin-only. The status endpoint is the one that matters on this host: the
integration is expected to be unreachable, so "is it working" has to be
answerable without reading pod logs.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger

from app.auth.dependencies import require_admin, require_super_admin
from app.config import get_settings
from app.models.user import User
from app.services.gcp import exporters, gcp_client
from app.services.gcp.client import GCPUnavailable
from app.services.gcp import pipeline

router = APIRouter()
settings = get_settings()


@router.get("/status")
async def gcp_status(_: User = require_admin):
    """Whether the integration is on, configured, and actually reaching Google."""
    st = pipeline.stats()
    return {
        **st,
        "project_id": settings.gcp_project_id or None,
        "log_name": settings.gcp_log_name,
        "pubsub_topic": settings.gcp_pubsub_topic or None,
        "export_interval": settings.gcp_export_interval,
        # Distinguishes the three states that look alike from outside: off,
        # on-but-unconfigured, and on-but-unreachable.
        "state": (
            "disabled" if not settings.gcp_enabled
            else "unconfigured" if not gcp_client.enabled
            else "unreachable" if st.get("last_error")
            else "connected" if st.get("last_export_at")
            else "starting"
        ),
        "last_error": st.get("last_error"),
    }


@router.post("/test")
async def gcp_test(_: User = require_super_admin):
    """Try one round trip now, and say exactly what happened.

    Exists because the usual failure here is a network one, and an operator
    needs to tell "wrong credentials" from "Google is blocked" without waiting
    for the next export interval.
    """
    if not settings.gcp_enabled:
        raise HTTPException(status_code=400, detail="GCP_ENABLED is false")
    if not gcp_client.enabled:
        raise HTTPException(
            status_code=400,
            detail="GCP_PROJECT_ID or GCP_SERVICE_ACCOUNT_JSON is not set")
    try:
        sent = await exporters.export_logs([{
            "level": "INFO",
            "message": "sorinflow connectivity test",
            "name": "app.services.gcp",
            "function": "gcp_test",
            "line": 0,
        }])
        return {"ok": True, "entries_written": sent,
                "detail": "Cloud Logging accepted the write"}
    except GCPUnavailable as e:
        # 200 with ok=false, not an error status: the caller asked whether it
        # works, and "no, because Google is unreachable" is a successful answer
        # to that question.
        return {"ok": False, "detail": str(e)}


@router.get("/services")
async def gcp_services(minutes: int = Query(30, ge=5, le=1440),
                       _: User = require_admin):
    """Platform metrics for Compute Engine, Cloud Run, Functions and Cloud SQL.

    One service failing does not fail the call — a project with no Cloud SQL
    instance should still show its Compute Engine numbers.
    """
    if not gcp_client.enabled:
        raise HTTPException(status_code=400, detail="GCP integration is not configured")

    out = {}
    for key in exporters.GCP_SERVICES:
        try:
            out[key] = await exporters.read_service_metric(key, minutes)
        except GCPUnavailable as e:
            out[key] = {"service": key, "label": exporters.GCP_SERVICES[key][0],
                        "error": str(e)[:160], "points": []}
        except Exception as e:
            logger.warning(f"[gcp] reading {key} failed: {e}")
            out[key] = {"service": key, "label": exporters.GCP_SERVICES[key][0],
                        "error": "unexpected error", "points": []}
    return {"window_minutes": minutes, "services": out}
