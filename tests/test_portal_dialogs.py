"""
The customer portal uses the site's own dialog and inline error messages,
not the browser's confirm() and alert(), which the site forbids. Deleting a
request asked with confirm() and a failed delete or access request popped
alert(). The real portal.js functions run under Node with a DOM stand-in.
"""
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tests" / "js" / "portal_dialogs.mjs"


def test_the_portal_asks_and_reports_in_its_own_ui():
    node = shutil.which("node")
    assert node, "node is not on PATH — this check must not be skipped"
    result = subprocess.run([node, str(SCRIPT)], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "5 checks passed" in result.stdout, result.stdout


def test_no_browser_dialog_is_left_in_the_portal():
    import re
    js = (ROOT / "frontend" / "js" / "portal.js").read_text(encoding="utf-8")
    code = "\n".join(line for line in js.splitlines() if not line.lstrip().startswith("//"))
    assert not re.search(r"(?<![\w.])(confirm|alert|prompt)\(", code)
