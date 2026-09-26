"""
«تماس‌های امروز من» says how much of the queue it shows («نمایش ۴۰ از
۴۴۵»), can show forty more, and keeps what was expanded when it reloads after
a recorded call (1405/07/04: 445 waiting, 40 shown, no way to more). The real
loadCalls runs under Node, as test_panel_escaping.py does it.
"""
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tests" / "js" / "calls_more.mjs"


def test_the_call_list_can_show_more_and_keeps_it():
    node = shutil.which("node")
    assert node, "node is not on PATH — this check must not be skipped"
    result = subprocess.run([node, str(SCRIPT)], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "3 checks passed" in result.stdout, result.stdout
