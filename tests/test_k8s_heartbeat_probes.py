"""
The worker's and the scheduler's heartbeat probes, read from the manifests
the deploy applies.

1405/07/04: after the server restarted, the startup probe accepted the
previous container's heartbeat file (/tmp is an emptyDir and outlives a
container restart), liveness judged the still-booting process by that old
file, and the SIGTERM it asked for was lost on PID 1 mid-boot — so the kill
came when the worker's two-hour grace period ran out, in the middle of two
scrapes. The same deployment is also where «no time limit on a scrape» lives.
"""
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent


def pod(name):
    docs = yaml.safe_load_all((ROOT / "k8s" / "base" / f"{name}.yaml").read_text())
    dep = next(d for d in docs if d and d.get("kind") == "Deployment")
    spec = dep["spec"]["template"]["spec"]
    return spec, spec["containers"][0]


@pytest.mark.parametrize("name", ["worker", "scheduler"])
def test_startup_needs_a_fresh_heartbeat_not_just_a_file(name):
    _, c = pod(name)
    cmd = " ".join(c["startupProbe"]["exec"]["command"])
    assert "test -f" not in cmd
    assert "stat -c %Y /tmp/sorinflow-heartbeat" in cmd and "-lt 90" in cmd


@pytest.mark.parametrize("name", ["worker", "scheduler"])
def test_the_boot_window_covers_a_whole_server_restarting(name):
    _, c = pod(name)
    p = c["startupProbe"]
    assert p["periodSeconds"] * p["failureThreshold"] >= 600   # the boot took 4.5 min


def test_a_probe_restart_never_waits_out_the_drain_window():
    _, c = pod("worker")
    for probe in ("startupProbe", "livenessProbe"):
        assert c[probe].get("terminationGracePeriodSeconds", 10**9) <= 60, probe


def test_a_running_scrape_is_not_cut_off_by_a_deploy():
    spec, _ = pod("worker")
    assert spec["terminationGracePeriodSeconds"] >= 7 * 24 * 3600
