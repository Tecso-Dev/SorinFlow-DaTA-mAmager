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


def _ingress_paths():
    """Every rule of every https Ingress, with the router priority Traefik
    would give it.

    Traefik does not rank by specificity. Each path becomes a router whose
    priority is the LENGTH of its rule string unless the Ingress carries
    `router.priority`, and `PathPrefix(`/`)` is 15 characters — so an Exact
    rule shorter than that loses to the catch-all. That is not a hypothetical:
    /health (15, tied) and /ready (14) answered from Next with a 404 on the
    live site, and because scripts/deploy_k8s.sh verifies itself by probing
    /health, the deploy failed at its last step and deleted every
    NetworkPolicy. The first version of this test modelled longest-prefix,
    which is the intuitive rule and not the one in force, so it passed.
    """
    out = []
    for doc in yaml.safe_load_all(INGRESS.read_text(encoding="utf-8")):
        if not (isinstance(doc, dict) and doc.get("kind") == "Ingress"):
            continue
        ann = doc["metadata"].get("annotations", {})
        if ann.get("traefik.ingress.kubernetes.io/router.entrypoints") != "websecure":
            continue
        explicit = ann.get("traefik.ingress.kubernetes.io/router.priority")
        for r in doc["spec"]["rules"][0]["http"]["paths"]:
            rule = (f"Path(`{r['path']}`)" if r["pathType"] == "Exact"
                    else f"PathPrefix(`{r['path']}`)")
            out.append({
                "path": r["path"],
                "type": r["pathType"],
                "service": r["backend"]["service"]["name"],
                "priority": int(explicit) if explicit else len(rule),
            })
    assert out, "no websecure Ingress in k8s/base/ingress.yaml"
    return out


def _service_for(path: str) -> str | None:
    """Which service a request for `path` reaches, by Traefik's own ranking:
    every matching router competes, and the highest priority wins."""
    best, name = -1, None
    for r in _ingress_paths():
        p = r["path"].rstrip("/") or "/"
        if r["type"] == "Exact":
            hit = path == r["path"]
        else:
            hit = p == "/" or path == r["path"] or path == p or path.startswith(p + "/")
        if hit and r["priority"] > best:
            best, name = r["priority"], r["service"]
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
                                  "sorinflow-ingress-catchall"])
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


def test_the_catchall_is_outranked_by_every_named_path():
    """The bug that cost the cluster its NetworkPolicies, stated directly: no
    path the API serves may lose to `/` because its rule string is short."""
    rules = _ingress_paths()
    catchall = next(r for r in rules if r["path"] == "/")
    assert catchall["priority"] == 1, \
        "the catch-all must carry an explicit low router.priority, not a length"
    for r in rules:
        if r["path"] != "/":
            assert r["priority"] > catchall["priority"], f"{r['path']} does not outrank /"


def test_the_shortest_backend_paths_still_reach_the_api():
    """/ready is 14 characters as a Traefik rule and /health is 15 — both at
    or under `PathPrefix(`/`)`. They are the two this went wrong on, and they
    are what the deploy verifies itself with."""
    for p in ("/health", "/ready"):
        assert _service_for(p) == "backend", p
