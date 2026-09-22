"""
Secrets managed in GitHub reach the cluster on deploy — and a missing one
never blanks a key that was set by hand on the server.
"""
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WF = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")


def _step(name):
    at = WF.index(f"- name: {name}")
    nxt = WF.find("\n      - name: ", at + 1)
    return WF[at:nxt if nxt > 0 else None]


class TestTheSyncStep:

    def test_it_runs_before_the_manifests_create_the_new_pod(self):
        assert WF.index("- name: Sync secrets from GitHub") < WF.index("- name: Apply manifests")
        assert WF.index("- name: Pull image into k3s containerd") < WF.index("- name: Sync secrets from GitHub")

    def test_the_llm_trio_is_managed_there(self):
        step = _step("Sync secrets from GitHub")
        for name in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "LIARA_API_TOKEN"):
            assert f"{name}: ${{{{ secrets.{name} }}}}" in step
            assert name in re.search(r'LIST="([^"]+)"', step).group(1).split()

    def test_an_unset_github_secret_leaves_the_cluster_alone(self):
        step = _step("Sync secrets from GitHub")
        assert 'if [ -z "$val" ]' in step and "continue" in step
        assert 'if [ "$patch" = "{}" ]' in step, "no keys, no patch"

    def test_no_value_is_ever_printed(self):
        step = _step("Sync secrets from GitHub")
        run = step[step.index("run: |"):]
        for line in run.splitlines():
            if "echo" in line:
                assert "$val" not in line and "$patch" not in line, line
        assert "kubectl patch secret sorinflow-secrets" in run and ">/dev/null" in run

    def test_the_pod_actually_reads_them(self):
        manifest = (ROOT / "k8s/04-backend.yaml").read_text(encoding="utf-8")
        for name in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "LIARA_API_TOKEN"):
            assert f"key: {name}, optional: true" in manifest
        for name, default in (("LLM_MODEL_READ", "z-ai/glm-5.3-flash"), ("LLM_MODEL_VISION", "z-ai/glm-5.3-flash"),
                              ("LLM_MODEL_EMBED", "openai/text-embedding-3-small")):
            assert f'- name: {name}\n              value: "{default}"' in manifest
        cfg = (ROOT / "app/config.py").read_text(encoding="utf-8")
        for f in ("llm_model_read", "llm_model_vision", "llm_model_embed", "liara_api_token"):
            assert f"{f}: str = Field(" in cfg
        env = (ROOT / ".env.example").read_text(encoding="utf-8")
        assert "LLM_API_KEY=\n" in env and "LLM_BASE_URL=\n" in env and "LLM_MODEL=\n" in env
        docs = (ROOT / "SECRETS.md").read_text(encoding="utf-8")
        assert "## 2d. Managing a secret from GitHub" in docs
