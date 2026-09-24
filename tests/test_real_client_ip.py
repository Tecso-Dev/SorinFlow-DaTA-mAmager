"""
«IP واقعی کاربر»: k3s's ServiceLB rewrote every caller to an address of its
own, so each per-address limit (logins, SMS codes) was one limit for the
whole world — ten requests could stop every password reset. Three pieces
together give the app the caller's real, unforgeable address.
"""
import asyncio
import os
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_real_ip.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

ROOT = Path(__file__).resolve().parent.parent


def _trusted() -> str:
    cmd = re.search(r'^CMD (\[.*\])', (ROOT / "Dockerfile").read_text(), re.M).group(1)
    args = yaml.safe_load(cmd)
    return args[args.index("--forwarded-allow-ips") + 1]


def _seen(peer: str, xff: str) -> str:
    """What the app sees as the client, through uvicorn's own middleware with
    the trusted range the image really uses."""
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
    got = {}

    async def app(scope, receive, send):
        got["client"] = scope["client"][0]
    mw = ProxyHeadersMiddleware(app, trusted_hosts=_trusted())
    scope = {"type": "http", "client": (peer, 40000), "scheme": "http",
             "headers": [(b"x-forwarded-for", xff.encode())]}
    asyncio.run(mw(scope, None, None))
    return got["client"]


class TestTheAppSeesTheRealCaller:

    def test_through_traefik_it_is_the_address_traefik_wrote(self):
        # a caller's own made-up entry first, Traefik's (the real peer) last
        assert _seen("10.42.0.9", "6.6.6.6, 203.0.113.7") == "203.0.113.7"

    def test_straight_from_outside_a_forged_header_is_ignored(self):
        assert _seen("198.51.100.4", "10.42.0.1, 6.6.6.6") == "198.51.100.4"

    def test_the_trusted_range_is_the_cluster_not_everyone(self):
        assert _trusted() == "10.42.0.0/16"


class TestTheClusterHandsItOver:

    def test_traefik_keeps_the_callers_address(self):
        doc = yaml.safe_load((ROOT / "k8s/overlays/production/traefik-acme.yaml").read_text())
        values = yaml.safe_load(doc["spec"]["valuesContent"])
        assert values["service"]["spec"]["externalTrafficPolicy"] == "Local"

    def test_the_backend_is_reachable_only_through_traefik(self):
        docs = [d for d in yaml.safe_load_all((ROOT / "k8s/base/backend.yaml").read_text()) if d]
        svc = next(d for d in docs if d["kind"] == "Service")
        assert svc["spec"]["type"] == "ClusterIP"
        assert all("nodePort" not in p for p in svc["spec"]["ports"])


class TestAnyoneCanCheckIt:

    def test_the_profile_endpoint_answers_with_what_the_limits_use(self):
        from types import SimpleNamespace
        from app.api.routes import users
        req = SimpleNamespace(headers={"x-forwarded-for": "203.0.113.7"},
                              client=SimpleNamespace(host="10.42.0.9"))
        assert asyncio.run(users.get_my_ip(req, SimpleNamespace(id=1))) == {"ip": "203.0.113.7"}
