"""
The Divar login form fills in a code the phone's forwarder already sent.

The server has parked forwarded login codes for three minutes all along
(/api/scraper/login-code), but nothing in the panel ever asked for them, so
the person typed a code their phone had already delivered — and reported
«فورواردر کار نمی‌کند». The real function is exercised under Node, the way
test_panel_escaping.py does it.
"""
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tests" / "js" / "login_code_autofill.mjs"


def test_the_forwarded_login_code_is_picked_up():
    node = shutil.which("node")
    assert node, "node is not on PATH — this check must not be skipped"
    result = subprocess.run([node, str(SCRIPT)], cwd=ROOT, capture_output=True,
                            text=True, timeout=30)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "4 checks passed" in result.stdout, result.stdout
