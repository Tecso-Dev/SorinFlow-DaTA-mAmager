"""
The panel's log viewer reads the file that is picked. Since the roles became
separate pods each writes its own log, but the viewer only ever asked for
scraper.log, so scheduled scrapes, backups and the photo AI (scheduler.log)
were out of reach from the panel. The endpoint side is test_role_logs.py;
this runs the real loadScraperLog under Node.
"""
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tests" / "js" / "log_viewer.mjs"


def test_the_log_viewer_reads_the_picked_file():
    node = shutil.which("node")
    assert node, "node is not on PATH — this check must not be skipped"
    result = subprocess.run([node, str(SCRIPT)], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "4 checks passed" in result.stdout, result.stdout
