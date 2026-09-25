"""
kustomize merges a strategic-merge patch's containers BY NAME: a patch that
names a container which does not exist in the base does not update anything
— it adds a second, image-less container beside the real one. The staging
overlay's DOMAIN patch did exactly that after k8s/base/backend.yaml's
container was renamed from "api" to "backend" (see that file's comment on
why the rename was needed), and the raw YAML files never show it — only a
live render does, since kustomize is what resolves the patch.
"""
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
OVERLAYS = ["production", "staging"]


def _render(overlay):
    kubectl = shutil.which("kubectl")
    if not kubectl:
        pytest.skip("kubectl not on PATH — present locally and on GitHub runners")
    result = subprocess.run(
        [kubectl, "kustomize", f"k8s/overlays/{overlay}"],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return [d for d in yaml.safe_load_all(result.stdout) if d]


@pytest.mark.parametrize("overlay", OVERLAYS)
def test_every_deployment_and_job_has_exactly_one_container_with_an_image(overlay):
    docs = _render(overlay)
    checked = 0
    for doc in docs:
        if doc.get("kind") not in ("Deployment", "Job"):
            continue
        containers = doc["spec"]["template"]["spec"]["containers"]
        name = doc["metadata"]["name"]
        assert len(containers) == 1, (
            f"{overlay}: {doc['kind']}/{name} renders {len(containers)} containers "
            f"({[c.get('name') for c in containers]}) — a patch is naming one that "
            f"does not exist and adding a second beside the real one instead of "
            f"updating it"
        )
        assert containers[0].get("image"), f"{overlay}: {doc['kind']}/{name}'s container has no image"
        checked += 1
    assert checked >= 5, "no Deployment/Job found in the render — the test is looking in the wrong place"


def test_production_api_container_is_named_backend():
    """`kubectl apply` after a `rollout undo` merges containers BY NAME (see
    backend.yaml's comment): a container named anything else would not
    update the live one and would add a second beside it instead."""
    docs = _render("production")
    backend = next(d for d in docs if d.get("kind") == "Deployment" and d["metadata"]["name"] == "backend")
    names = [c["name"] for c in backend["spec"]["template"]["spec"]["containers"]]
    assert names == ["backend"]
