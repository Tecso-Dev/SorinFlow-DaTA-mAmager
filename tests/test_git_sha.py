"""
GIT_SHA: baked into the image at build time (Dockerfile ARG/ENV), passed by
deploy.yml from the commit actually being built, read by Settings, and shown
on /health (behavioural coverage for that part is in test_error_handling.py).
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_the_dockerfile_declares_and_exports_it():
    docker = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert 'ARG GIT_SHA=""' in docker
    assert "ENV GIT_SHA=$GIT_SHA" in docker
    # after COPY . ., so a changed commit does not bust the pip install cache
    assert docker.index("COPY . .") < docker.index("ARG GIT_SHA")


def test_deploy_workflow_passes_the_commit_being_built():
    wf = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")
    at = wf.index("- name: Build and push Docker image")
    nxt = wf.find("\n      - name: ", at + 1)
    step = wf[at:nxt if nxt > 0 else None]
    assert "build-args:" in step and "GIT_SHA=${{ github.sha }}" in step
    # only this step: triggers, guards and every other step are untouched
    assert wf.count("GIT_SHA") == 1


def test_settings_has_the_field():
    cfg = (ROOT / "app/config.py").read_text(encoding="utf-8")
    assert 'git_sha: str = Field(default="", validation_alias=AliasChoices("GIT_SHA", "git_sha"))' in cfg
