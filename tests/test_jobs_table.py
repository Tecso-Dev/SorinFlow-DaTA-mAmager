"""
The scrape jobs table shows start times in Tehran time, like the schedules
card, and the progress bar says what its percent is measured against: a run
its 08:00 schedule started read «۰۶:۳۰» on a European laptop, and a run for
200 sat at 4% of Divar's 4253 with 176 saved (1405/07/05). The real
_renderJobsTable runs under Node, as test_schedules_card.py does it.
"""
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tests" / "js" / "jobs_table.mjs"


def test_the_jobs_table_is_on_tehran_time_and_names_its_target():
    node = shutil.which("node")
    assert node, "node is not on PATH — this check must not be skipped"
    result = subprocess.run([node, str(SCRIPT)], cwd=ROOT, capture_output=True, text=True,
                            timeout=30, env={**os.environ, "TZ": "Europe/Berlin"})
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "9 checks passed" in result.stdout, result.stdout
