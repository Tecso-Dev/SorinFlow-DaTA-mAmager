"""
Google Cloud Observability integration.

Cloud Logging, Cloud Monitoring, Pub/Sub and GCP service monitoring, spoken
over the REST APIs with httpx and a service-account token rather than the
official SDKs. Three reasons: google-cloud-logging + monitoring + pubsub pull
in well over a hundred megabytes of transitive dependencies into an image that
already carries Chromium, on a VPS whose bootstrap script adds swap because RAM
is tight; the REST surface we need is four endpoints; and httpx is already a
dependency, so nothing new arrives for a feature that ships disabled.

DISABLED BY DEFAULT (GCP_ENABLED=false). The server this runs on is in Iran,
where Google's endpoints are blocked, so every call here is written to fail
soft: a timeout or a 403 degrades the exporter and is reported on the status
endpoint, and never touches the request path or the scraper. Turning it on is
only useful where the host can actually reach Google — through a VPN, after a
migration, or when pointing at a client's project.
"""
from app.services.gcp.client import GCPClient, gcp_client, GCPUnavailable

__all__ = ["GCPClient", "gcp_client", "GCPUnavailable"]
