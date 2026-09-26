"""
The scheduled-scrapes card stays on screen when it is empty, says how to
make a schedule, and shows its times in Tehran time. It used to hide itself
with the last schedule deleted, and «بعدی: ۰۶:۳۰» on a European laptop for
an 08:00 schedule read as the wrong hour (1405/07/04). The real
loadSchedules runs under Node, as test_panel_escaping.py does it.
"""
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tests" / "js" / "schedules_card.mjs"


def test_the_schedules_card_is_always_there_and_on_tehran_time():
    node = shutil.which("node")
    assert node, "node is not on PATH — this check must not be skipped"
    result = subprocess.run([node, str(SCRIPT)], cwd=ROOT, capture_output=True, text=True,
                            timeout=30, env={**os.environ, "TZ": "Europe/Berlin"})
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "3 checks passed" in result.stdout, result.stdout
