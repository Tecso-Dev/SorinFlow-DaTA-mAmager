"""
What the AI card shows while the gateway has no credit (#35).

When the account's credit, quota or balance is used up the agents pause until
the next Tehran midnight or until the card's test is answered. The card has to
show the gateway's own sentence, until when the agents wait — in Tehran time,
on a European laptop too — and drop the notice when the pause is over. The
server side is test_ai_quota_pause.py; this runs the real card functions of
app.js under Node, the way test_schedules_card.py does.
"""
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tests" / "js" / "ai_pause.mjs"


def test_the_card_shows_the_gateways_words_and_until_when_and_drops_them_after():
    node = shutil.which("node")
    assert node, "node is not on PATH — this check must not be skipped"
    result = subprocess.run([node, str(SCRIPT)], cwd=ROOT, capture_output=True, text=True,
                            timeout=60, env={**os.environ, "TZ": "Europe/Berlin"})
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "13 checks passed" in result.stdout, result.stdout
