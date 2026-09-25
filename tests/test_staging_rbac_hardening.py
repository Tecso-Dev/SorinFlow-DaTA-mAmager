"""
staging-deployer's Role granted "*" on "*" in every apiGroup — which,
because RBAC treats /namespaces/sorinflow-staging as itself a namespaced
object of that namespace, let the scoped token relabel or unlock its own
Namespace, including the Pod Security labels below. Pins the fix down so a
"just get this working" patch cannot quietly bring the wildcard back.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent


def _kustomize(overlay):
    kubectl = shutil.which("kubectl")
    if not kubectl:
        pytest.skip("kubectl not on PATH — present locally and on GitHub runners")
    result = subprocess.run(
        [kubectl, "kustomize", f"k8s/overlays/{overlay}"],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return [d for d in yaml.safe_load_all(result.stdout) if d]


def test_staging_namespace_enforces_pod_security_baseline():
    ns = next(d for d in _kustomize("staging") if d["kind"] == "Namespace")
    assert ns["metadata"].get("labels", {}).get("pod-security.kubernetes.io/enforce") == "baseline"


def test_staging_role_has_no_wildcards_and_cannot_touch_namespaces():
    role = next(d for d in _kustomize("staging") if d["kind"] == "Role")
    assert role["rules"], "staging-deployer's Role has no rules at all"
    for rule in role["rules"]:
        for field in ("apiGroups", "resources", "verbs"):
            assert "*" not in rule.get(field, []), \
                f"staging-deployer's Role still has a wildcard in {field}: {rule}"
        assert "namespaces" not in rule.get("resources", []), \
            "staging-deployer can get/patch its own Namespace object"


def test_ownership_job_uses_a_baseline_allowed_capability():
    """Pod Security "baseline" (enforced above) allows CHOWN/FOWNER/
    DAC_OVERRIDE but not DAC_READ_SEARCH — the Job would be refused outright
    with the old capability once staging enforces baseline."""
    job = next(d for d in _kustomize("staging") if d["kind"] == "Job" and d["metadata"]["name"] == "data-ownership")
    caps = job["spec"]["template"]["spec"]["containers"][0]["securityContext"]["capabilities"]["add"]
    assert "DAC_READ_SEARCH" not in caps
    assert "DAC_OVERRIDE" in caps


def test_deploy_script_runs_exclude_kinds_the_admin_step_already_applied():
    """Runs scripts/deploy_k8s.sh's own exclude_kinds() (extracted verbatim,
    not reimplemented, so this cannot drift from what the script actually
    does) the same way step 1 builds $CORE for OVERLAY=staging, against a
    real render — and checks none of the objects the admin kubeconfig step in
    staging.yml already applied (the Namespace, and rbac.yaml's own
    ServiceAccount/Role/RoleBinding) ride along in it."""
    script = (ROOT / "scripts/deploy_k8s.sh").read_text(encoding="utf-8")
    assert 'if [ "$OVERLAY" = staging ]' in script and \
        'CORE_EXCLUDE="$CORE_EXCLUDE Namespace ServiceAccount Role RoleBinding"' in script, \
        "deploy_k8s.sh no longer skips the staging Namespace/RBAC objects the way this test expects"
    fn = re.search(r"^exclude_kinds\(\) \{\n.*?\n\}\n", script, re.M | re.S)
    assert fn, "exclude_kinds() not found in scripts/deploy_k8s.sh"

    kubectl = shutil.which("kubectl")
    if not kubectl:
        pytest.skip("kubectl not on PATH — present locally and on GitHub runners")
    rendered = subprocess.run(
        [kubectl, "kustomize", "k8s/overlays/staging"],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    assert rendered.returncode == 0, rendered.stderr

    core = subprocess.run(
        ["bash", "-c", fn.group(0) +
         'exclude_kinds "Deployment Ingress NetworkPolicy Job Namespace ServiceAccount Role RoleBinding"'],
        input=rendered.stdout, capture_output=True, text=True, timeout=10,
    )
    assert core.returncode == 0, core.stderr
    kinds = set(re.findall(r"^kind:\s*(\S+)", core.stdout, re.M))
    assert kinds, "exclude_kinds dropped every document — check the extraction"
    assert kinds.isdisjoint({"Namespace", "ServiceAccount", "Role", "RoleBinding"}), kinds
