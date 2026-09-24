"""
Stored XSS in the panel.

app.js builds most of the DOM with innerHTML/insertAdjacentHTML, and the data
going into it is Divar listings, customer notes, SMS logs and staff-typed
fields — none of it trusted. esc() is the one thing standing between that
data and a script tag running with whoever's admin token is in
localStorage.sf_token. html()/raw() are the escape-by-default helper for new
code, and safeUrl()/safeManualPhoto() stop a stored value from becoming a
javascript: link or breaking out of a src/href attribute.

Python never loads app.js (it is a classic script, not a module — see
test_panel_scripts_share_one_scope.py), so this runs a small Node script
against the real functions instead of re-implementing them here, which would
drift from app.js and stop catching anything.
"""
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tests" / "js" / "panel_escaping.mjs"


def test_esc_html_raw_and_url_guard_hold_against_real_payloads():
    node = shutil.which("node")
    assert node, (
        "node is not on PATH — this test needs it to exercise the real "
        "esc()/html()/raw()/safeUrl()/safeManualPhoto() functions in "
        "frontend/js/app.js. Install Node (CI's ubuntu runner has it); "
        "this check must not be skipped."
    )
    result = subprocess.run(
        [node, str(SCRIPT)], cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, (
        f"panel escaping check failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "16 checks passed" in result.stdout, result.stdout
