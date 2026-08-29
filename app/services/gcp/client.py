"""
Authenticated transport for the Google Cloud REST APIs.

One client for the process, holding one access token and refreshing it before
it expires. Everything above this module speaks in terms of a project id and a
payload; the OAuth dance lives here.
"""
import asyncio
import json
import time
from typing import Any, Optional

import httpx
from loguru import logger

from app.config import get_settings

settings = get_settings()

_TOKEN_URL = "https://oauth2.googleapis.com/token"
# Everything the exporter needs and nothing more. Cloud Platform's blanket
# scope would work too and is what most examples use — these are narrower, so a
# leaked token cannot read the project's storage.
_SCOPES = " ".join([
    "https://www.googleapis.com/auth/logging.write",
    "https://www.googleapis.com/auth/logging.read",
    "https://www.googleapis.com/auth/monitoring.write",
    "https://www.googleapis.com/auth/monitoring.read",
    "https://www.googleapis.com/auth/pubsub",
])


class GCPUnavailable(RuntimeError):
    """Google could not be reached, or refused us.

    Its own type because the caller's reaction differs from a bug: on this host
    it is the expected state, so it is logged once and reported on the status
    endpoint rather than raised into a request.
    """


class GCPClient:
    def __init__(self):
        self._token: Optional[str] = None
        self._expires_at: float = 0.0
        self._lock = asyncio.Lock()
        self._http: Optional[httpx.AsyncClient] = None
        self.last_error: Optional[str] = None
        self.last_success: Optional[float] = None

    # ── configuration ───────────────────────────────────────────────────────
    @property
    def enabled(self) -> bool:
        return bool(settings.gcp_enabled and settings.gcp_project_id
                    and settings.gcp_service_account_json)

    @property
    def project_id(self) -> str:
        return settings.gcp_project_id

    def _credentials(self) -> dict:
        """The service-account key, as a dict.

        Accepts either an inline JSON blob or a path, because a Kubernetes
        Secret can deliver it either way and operators reasonably expect both.
        """
        raw = settings.gcp_service_account_json.strip()
        if not raw:
            raise GCPUnavailable("GCP_SERVICE_ACCOUNT_JSON is empty")
        if not raw.startswith("{"):
            try:
                with open(raw, "r", encoding="utf-8") as fh:
                    raw = fh.read()
            except OSError as e:
                raise GCPUnavailable(f"service account file unreadable: {e}") from e
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            raise GCPUnavailable(f"service account JSON is malformed: {e}") from e

    def _client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            # Short timeouts on purpose. From a host that cannot reach Google
            # the connection hangs rather than refusing, and a background
            # exporter that blocks for 30s per batch is its own outage.
            self._http = httpx.AsyncClient(
                timeout=httpx.Timeout(settings.gcp_timeout_seconds, connect=5.0))
        return self._http

    # ── auth ────────────────────────────────────────────────────────────────
    async def _access_token(self) -> str:
        """A cached OAuth token, refreshed a minute before it expires."""
        async with self._lock:
            if self._token and time.time() < self._expires_at - 60:
                return self._token

            creds = self._credentials()
            try:
                # Signing is CPU work and blocks; it happens once per hour, but
                # the event loop should not be the thing doing it.
                assertion = await asyncio.to_thread(_signed_assertion, creds)
            except GCPUnavailable:
                raise
            except Exception as e:
                raise GCPUnavailable(f"could not sign the JWT assertion: {e}") from e

            try:
                resp = await self._client().post(_TOKEN_URL, data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": assertion,
                })
            except httpx.HTTPError as e:
                raise GCPUnavailable(f"cannot reach Google: {e}") from e

            if resp.status_code != 200:
                raise GCPUnavailable(
                    f"token exchange refused ({resp.status_code}): {resp.text[:200]}")

            body = resp.json()
            self._token = body["access_token"]
            self._expires_at = time.time() + int(body.get("expires_in", 3600))
            return self._token

    # ── requests ────────────────────────────────────────────────────────────
    async def request(self, method: str, url: str, *, json_body: Any = None,
                      params: dict = None) -> dict:
        """One authenticated call. Raises GCPUnavailable for anything that is
        not a clean 2xx, so callers have exactly one failure to handle."""
        if not self.enabled:
            raise GCPUnavailable("GCP integration is disabled")

        token = await self._access_token()
        try:
            resp = await self._client().request(
                method, url, json=json_body, params=params,
                headers={"Authorization": f"Bearer {token}"})
        except httpx.HTTPError as e:
            self.last_error = f"{type(e).__name__}: {e}"
            raise GCPUnavailable(f"cannot reach Google: {e}") from e

        if resp.status_code == 401:
            # token rejected — drop it so the next call re-authenticates
            self._token, self._expires_at = None, 0.0
        if resp.status_code >= 300:
            self.last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
            raise GCPUnavailable(self.last_error)

        self.last_error = None
        self.last_success = time.time()
        return resp.json() if resp.content else {}

    async def close(self) -> None:
        if self._http is not None and not self._http.is_closed:
            await self._http.aclose()


def _signed_assertion(creds: dict) -> str:
    """Build and sign the JWT the token endpoint trades for an access token.

    RS256 with the service account's private key. python-jose is already a
    dependency for the app's own tokens, so this needs nothing new.
    """
    from jose import jwt as jose_jwt

    email = creds.get("client_email")
    key = creds.get("private_key")
    if not email or not key:
        raise GCPUnavailable("service account JSON has no client_email/private_key")

    now = int(time.time())
    claims = {
        "iss": email,
        "scope": _SCOPES,
        "aud": _TOKEN_URL,
        "iat": now,
        "exp": now + 3600,
    }
    return jose_jwt.encode(claims, key, algorithm="RS256",
                           headers={"kid": creds.get("private_key_id")})


gcp_client = GCPClient()
