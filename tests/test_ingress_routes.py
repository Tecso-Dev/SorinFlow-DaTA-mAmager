"""Every path the API serves at the origin root has to be named in the Ingress.

Before phase 4, `/` went to the backend and only /panel and /_next went to the
new web pod, so anything the API added at the root just worked. The switchover
turns that around: `/` is the new site now, and the backend keeps only the
paths the Ingress lists. A route added to app/main.py and not added there no
longer falls back — it 404s out of Next, in production, on a path that works
perfectly in every local run (where there is no Ingress at all).

So this reads the routes and mounts out of the running app and checks each one
against k8s/base/ingress.yaml, rather than against a list written out by hand
that would go stale the same way.
"""
import os
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_ingress_routes.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

INGRESS = Path(__file__).resolve().parent.parent / "k8s" / "base" / "ingress.yaml"

# Paths the new site owns even though the API also answers them: the whole
# point of the switchover is that these now come from Next.
HANDED_OVER = {"/", "/portal", "/robots.txt", "/sitemap.xml", "/og.png", "/favicon.ico"}


HTTPS_INGRESSES = ("sorinflow-ingress-https", "sorinflow-ingress-web")


def _https_ingresses():
    docs = {d["metadata"]["name"]: d for d in yaml.safe_load_all(INGRESS.read_text(encoding="utf-8"))
            if isinstance(d, dict) and d.get("kind") == "Ingress"}
    for name in HTTPS_INGRESSES:
        assert name in docs, f"no {name} in k8s/base/ingress.yaml"
    return [docs[n] for n in HTTPS_INGRESSES]


def _https_rules():
    """Every https path, whichever of the two Ingresses holds it."""
    return [p for ing in _https_ingresses() for p in ing["spec"]["rules"][0]["http"]["paths"]]


def _traefik_routers():
    """The routers Traefik builds from the https Ingresses, ranked the way
    Traefik ranks them — which is not the Ingress spec's way. Each path
    becomes the rule `Host(`h`) && Path(`p`)` (Exact) or
    `Host(`h`) && PathPrefix(`p`)` (Prefix), and a router's priority is its
    Ingress's router.priority annotation or, without one, the length of that
    rule text."""
    out = []
    for ing in _https_ingresses():
        pinned = (ing["metadata"].get("annotations") or {}).get(
            "traefik.ingress.kubernetes.io/router.priority")
        rule = ing["spec"]["rules"][0]
        for p in rule["http"]["paths"]:
            matcher = "Path" if p["pathType"] == "Exact" else "PathPrefix"
            text = f"Host(`{rule['host']}`) && {matcher}(`{p['path']}`)"
            out.append({"exact": p["pathType"] == "Exact", "path": p["path"],
                        "priority": int(pinned) if pinned else len(text),
                        "service": p["backend"]["service"]["name"], "rule": text})
    return out


def _traefik_service_for(path: str) -> str:
    """Which service Traefik sends `path` to. A tie between two different
    services is a coin toss in production, so it fails here."""
    matching = [r for r in _traefik_routers()
                if (path == r["path"] if r["exact"] else path.startswith(r["path"]))]
    assert matching, f"no router matches {path}"
    top = max(r["priority"] for r in matching)
    winners = {r["service"] for r in matching if r["priority"] == top}
    assert len(winners) == 1, (
        f"{path}: routers tie at priority {top} and go to different services: "
        + ", ".join(r["rule"] for r in matching if r["priority"] == top))
    return winners.pop()


def _service_for(path: str) -> str | None:
    """Which service a request for `path` reaches, by the Ingress rules:
    Exact wins outright, otherwise the longest matching Prefix does."""
    rules = _https_rules()
    for r in rules:
        if r["pathType"] == "Exact" and r["path"] == path:
            return r["backend"]["service"]["name"]
    best, name = -1, None
    for r in rules:
        if r["pathType"] != "Prefix":
            continue
        p = r["path"].rstrip("/")
        # Prefix matches whole segments: /dashboard matches /dashboard/x but
        # never /dashboardx
        if path == r["path"] or path == p or path.startswith(p + "/") or p == "":
            if len(p) > best:
                best, name = len(p), r["backend"]["service"]["name"]
    return name


def _api_root_paths():
    """Every root-level path the app answers **in this process**.

    The four StaticFiles mounts register inside a try/except that only warns,
    so a mount whose directory is missing is simply absent here — /downloads
    went unchecked on CI for exactly that reason until DOWNLOADS_PATH was set
    there. Routes with path parameters are skipped: the Ingress matches by
    prefix and those all live under one."""
    import app.main as m
    out = set()
    for route in m.app.routes:
        path = getattr(route, "path", None)
        if path and path.startswith("/") and "{" not in path:
            out.add(path)
    return out


def test_every_root_path_the_api_serves_is_routed_to_it():
    served = {p for p in _api_root_paths() if p not in HANDED_OVER}
    missing = sorted(p for p in served if _service_for(p) != "backend")
    assert not missing, (
        "these reach the new site instead of the API and will 404 in production, "
        f"but work locally: {missing}. Add each to k8s/base/ingress.yaml, or to "
        "HANDED_OVER here if the new site is meant to own it."
    )


def test_traefik_sends_every_api_root_path_to_the_api():
    """What production actually does with the paths above. The phase 4 deploy
    stopped on exactly this: with the catch-all beside them, PathPrefix(`/`)
    tied with Path(`/health`) at 15 characters and beat Path(`/ready`), so
    the deploy's own check got Next's 404 for /health."""
    served = {p for p in _api_root_paths() if p not in HANDED_OVER} | {"/health", "/ready"}
    wrong = sorted(p for p in served if _traefik_service_for(p) != "backend")
    assert not wrong, f"Traefik sends these to the new site: {wrong}"


def test_traefik_sends_the_root_and_the_panel_to_the_new_site():
    for p in ("/", "/panel", "/panel/crm", "/portal", "/_next/static/x.js", "/sw.js"):
        assert _traefik_service_for(p) == "web", p


def test_traefik_sends_the_divar_login_routes_to_the_worker():
    for p in ("/api/auth/login", "/api/auth/verify", "/api/auth/refresh"):
        assert _traefik_service_for(p) == "worker", p


def test_the_divar_login_routes_still_go_to_the_worker():
    """They need the worker's Chromium and its cookie jar; the api pods have
    neither (k8s/base/worker.yaml)."""
    for p in ("/api/auth/login", "/api/auth/verify", "/api/auth/refresh"):
        assert _service_for(p) == "worker", p


def test_the_new_site_owns_the_root_and_the_panel():
    for p in ("/", "/panel", "/panel/crm", "/portal", "/_next/static/x.js",
              "/robots.txt", "/sitemap.xml", "/og.png", "/icons/icon-192.png", "/sw.js"):
        assert _service_for(p) == "web", p


def test_the_old_panel_and_the_file_mounts_stay_on_the_api():
    """/dashboard is the old panel, kept for two weeks after the switchover;
    the three mounts are files Next has never heard of."""
    for p in ("/dashboard/", "/dashboard/index.html", "/images/a.jpg",
              "/downloads/forwarder.apk", "/email-assets/hero-auth.png"):
        assert _service_for(p) == "backend", p


def test_a_public_path_still_reaches_the_api_with_a_trailing_slash():
    """A person typing sorinflow.com/health/ or a monitor configured with a
    trailing slash must not be handed the new site's 404. Prefix rules cover
    their own subtree; the Exact ones do not, which is the trap."""
    for p in ("/api/", "/dashboard", "/images", "/email-assets/"):
        assert _service_for(p) == "backend", p


def test_a_prefix_rule_does_not_capture_a_longer_word():
    """Prefix matches whole segments, so /dashboardx is not the old panel and
    must fall through to the new site rather than quietly reaching the API."""
    assert _service_for("/dashboardx") == "web"
    assert _service_for("/apiary") == "web"


@pytest.mark.parametrize("name", ["sorinflow-ingress-http", "sorinflow-ingress-https",
                                  "sorinflow-ingress-web"])
def test_the_staging_overlay_can_still_find_the_host_it_patches(name):
    """k8s/overlays/staging/kustomization.yaml replaces /spec/rules/0/host by
    index; a second rule added above would send staging's traffic to the
    production hostname without failing anything."""
    for doc in yaml.safe_load_all(INGRESS.read_text(encoding="utf-8")):
        if isinstance(doc, dict) and doc.get("metadata", {}).get("name") == name:
            rules = doc["spec"]["rules"]
            assert len(rules) == 1, f"{name} has {len(rules)} rules; staging patches rule 0 only"
            assert rules[0]["host"] == "sorinflow.com"
            return
    raise AssertionError(f"{name} is missing from k8s/base/ingress.yaml")
