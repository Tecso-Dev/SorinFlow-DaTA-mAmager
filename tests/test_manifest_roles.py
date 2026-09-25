"""Every SORINFLOW_ROLE a manifest sets must be one the app accepts.

app/config.py refuses to start on an unknown role, so a manifest that names
one fails its pod at settings load, before anything is logged about why —
the migrate Job once said "migrate" and would have failed every deploy
before a single migration ran.
"""
import os
import sys
import typing
from pathlib import Path

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import Settings  # noqa: E402

K8S = Path(__file__).resolve().parent.parent / "k8s"


def _roles_in_manifests():
    for path in sorted(K8S.rglob("*.yaml")):
        for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")):
            if not isinstance(doc, dict):
                continue
            spec = (((doc.get("spec") or {}).get("template") or {}).get("spec") or {})
            for c in spec.get("containers") or []:
                for e in c.get("env") or []:
                    if e.get("name") == "SORINFLOW_ROLE":
                        yield path.relative_to(K8S), e.get("value")


def test_every_role_a_manifest_sets_is_accepted_by_the_app():
    allowed = set(typing.get_args(Settings.model_fields["sorinflow_role"].annotation))
    found = list(_roles_in_manifests())
    assert found, "no SORINFLOW_ROLE in any manifest — the test is looking in the wrong place"
    bad = [(str(p), v) for p, v in found if v not in allowed]
    assert not bad, f"roles the app refuses at startup: {bad} (allowed: {sorted(allowed)})"
