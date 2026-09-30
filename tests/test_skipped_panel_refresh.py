"""
«آگهی‌های اسکرپ‌نشده» refreshes itself, and «تلاش دوباره» stays in the run (#58).

The window was read once, when it opened: a retry or a run still going
changed nothing on it until it was closed and opened again. Now it reads
the run's list every minute while it is open and stops when it closes,
without leaving a timer behind when it is opened again. Its buttons ask the
run itself to try its left-out listings again (POST /scraper/jobs/{id}/retry)
instead of /rescrape or the single-scrape box, which each opened a new row
in the jobs table. The real functions run under Node
(tests/js/skipped_panel.mjs), as test_schedules_card.py does it.
"""
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tests" / "js" / "skipped_panel.mjs"


def test_the_skipped_window_runs_under_node():
    node = shutil.which("node")
    assert node, "node is not on PATH — this check must not be skipped"
    result = subprocess.run([node, str(SCRIPT)], cwd=ROOT, capture_output=True, text=True,
                            timeout=30, env={**os.environ})
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "10 checks passed" in result.stdout, result.stdout
