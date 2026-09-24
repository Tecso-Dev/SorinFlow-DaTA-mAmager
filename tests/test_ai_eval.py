"""
scripts/ai_eval.py's offline sections, run as a fresh subprocess.

Not in-process: app.database.engine and get_settings() are cached at first
import, and by the time this test file runs inside the full suite some
other test has already imported app.database with its own DATABASE_URL —
an env var set after that has no effect on the already-built engine. A
subprocess is the only way ai_eval.py's own DATABASE_URL actually takes
hold, which is also exactly how it is really used (`python scripts/ai_eval.py`).

Values are not asserted — they will move as other streams change the AI
code this harness measures. Only that each offline section completes and
returns the metric keys the report depends on, in range.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "ai_eval.py"
OFFLINE = ("masking", "embed_text", "matching", "assistant_privacy")


def _run(tmp_path, *extra_args, env=None):
    out = tmp_path / "ai_eval.json"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json", str(out), *extra_args],
        cwd=ROOT, capture_output=True, text=True, timeout=90, env=env)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    return json.loads(out.read_text(encoding="utf-8"))


def test_offline_sections_complete_with_expected_metric_keys(tmp_path):
    data = _run(tmp_path, "--only", ",".join(OFFLINE))
    sections = data["sections"]
    assert set(sections) == set(OFFLINE)
    for name, res in sections.items():
        assert "metrics" in res, f"{name}: {res}"
        assert "failures" in res

    masking = sections["masking"]["metrics"]
    assert set(masking) == {"recall", "false_positives", "precision_ok"}
    assert 0.0 <= masking["recall"] <= 1.0
    assert masking["false_positives"] >= 0
    assert 0.0 <= masking["precision_ok"] <= 1.0

    embed_text = sections["embed_text"]["metrics"]
    assert set(embed_text) == {"coverage"}
    assert 0.0 <= embed_text["coverage"] <= 1.0

    matching = sections["matching"]["metrics"]
    assert set(matching) == {"recall", "precision", "old_customer_found"}
    assert 0.0 <= matching["recall"] <= 1.0
    assert 0.0 <= matching["precision"] <= 1.0
    assert matching["old_customer_found"] in (0, 1)

    priv = sections["assistant_privacy"]["metrics"]
    assert set(priv) == {
        "customer_names_sent", "other_consultant_customers", "private_listing_leaked",
        "draft_listing_leaked", "phones_sent", "own_customers_visible", "names_in_reply",
    }
    for key, value in priv.items():
        assert value >= 0, f"{key}: {value}"
    assert priv["private_listing_leaked"] in (0, 1)
    assert priv["draft_listing_leaked"] in (0, 1)
    assert priv["customer_names_sent"] <= 8
    assert priv["other_consultant_customers"] <= 3
    assert priv["own_customers_visible"] <= 3


def test_live_sections_are_skipped_without_a_key(tmp_path):
    """--live with no LLM_API_KEY/LLM_BASE_URL in the environment must not
    attempt a network call — the section is reported as skipped, not run."""
    env = {k: v for k, v in os.environ.items() if k not in ("LLM_API_KEY", "LLM_BASE_URL")}
    data = _run(tmp_path, "--live", "--only", "reader,need,retrieval", env=env)
    for name in ("reader", "need", "retrieval"):
        assert data["sections"][name] == {"skipped": "no key"}


def test_only_filters_to_the_requested_sections(tmp_path):
    data = _run(tmp_path, "--only", "masking")
    assert set(data["sections"]) == {"masking"}
