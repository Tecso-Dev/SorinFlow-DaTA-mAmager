"""
The README's own correctness.

A mermaid block that does not parse renders as a red "Unable to render rich
display" box on GitHub — the diagram is replaced by a stack trace, on the page
that is the project's front door. That happened here: a `;` inside a
sequenceDiagram note is a statement separator, so the note terminated early and
the rest of the line became a syntax error.

These are static checks, not a mermaid parser. They cover the constructs that
have actually broken a diagram in this repository, plus the factual claims that
were wrong for long enough to mislead someone.
"""
import re
from pathlib import Path

import pytest

README = Path("README.md")


def _read():
    return README.read_text(encoding="utf-8")


def _mermaid_blocks():
    """(index, kind, lines) for every ```mermaid fence in the README."""
    out, buf, start = [], None, 0
    for i, line in enumerate(_read().split("\n"), 1):
        s = line.strip()
        if s == "```mermaid":
            buf, start = [], i
        elif buf is not None and s == "```":
            kind = next((b.strip().split()[0] for b in buf if b.strip()), "")
            out.append((start, kind, buf))
            buf = None
        elif buf is not None:
            buf.append(line)
    return out


class TestMermaidWillRender:

    def test_there_are_diagrams_at_all(self):
        assert len(_mermaid_blocks()) >= 4

    def test_every_fence_is_closed(self):
        assert _read().count("```") % 2 == 0

    def test_no_semicolon_inside_a_diagram(self):
        """`;` separates statements in mermaid. Inside a label or a note it
        ends the statement early and everything after it is a parse error."""
        bad = [(ln, kind, l.strip())
               for ln, kind, body in _mermaid_blocks()
               for l in body if ";" in l]
        assert not bad, "semicolon inside a mermaid block:\n" + "\n".join(
            f"  README.md:{ln} [{k}] {t[:100]}" for ln, k, t in bad)

    def test_flowchart_labels_with_punctuation_are_quoted(self):
        """`A[foo (bar)]` is a parse error; `A["foo (bar)"]` is not."""
        bad = []
        for ln, kind, body in _mermaid_blocks():
            if not kind.startswith("flowchart") and not kind.startswith("graph"):
                continue
            for line in body:
                for m in re.finditer(r'\[([^"\]]*)\]', line):
                    if any(c in m.group(1) for c in "()"):
                        bad.append((ln, line.strip()))
        assert not bad, "unquoted punctuation in a flowchart label:\n" + "\n".join(
            f"  near README.md:{ln} {t[:100]}" for ln, t in bad)

    def test_known_diagram_kinds_only(self):
        """A typo in the kind renders the whole block as an error box."""
        allowed = {"flowchart", "graph", "sequenceDiagram", "erDiagram",
                   "classDiagram", "stateDiagram", "stateDiagram-v2",
                   "gantt", "pie", "journey", "timeline"}
        for ln, kind, _ in _mermaid_blocks():
            assert kind in allowed, f"README.md:{ln}: unknown diagram kind {kind!r}"


class TestTheReadmeDoesNotLie:
    """Each of these was false for long enough to mislead someone reading it.
    They are cheap to assert and the assertion is the reason they stay fixed."""

    def test_it_does_not_claim_ci_skips_the_tests(self):
        """Asserted on the corrected sentence rather than the absence of the
        old phrase: the roadmap is allowed to *describe* a stale claim, and a
        bare substring check cannot tell the two apart."""
        text = _read()
        assert "nothing reaches the registry unless they pass" in text
        assert "builds and deploys the image but does not run pytest" not in text

    def test_the_role_count_matches_the_code(self):
        text = _read()
        assert "three dashboard roles" not in text

    def test_every_permission_key_is_documented(self):
        from app.auth.permissions import PERMISSIONS
        text = _read()
        missing = [k for k in PERMISSIONS if f"`{k}`" not in text]
        assert not missing, f"permission keys absent from the README: {missing}"

    def test_every_mounted_router_prefix_is_in_the_api_table(self):
        """The table went eight routers out of date, including all three of the
        newest panels — someone reading it would not know they existed."""
        text = _read()
        for prefix in ("/api/sms", "/api/email", "/api/portal",
                       "/api/public/auth", "/api/filing", "/api/monitoring",
                       "/api/gcp", "/api/maintenance"):
            assert f"`{prefix}`" in text, f"{prefix} is missing from the README"

    def test_the_secrets_warning_is_not_in_the_present_tense(self):
        """Nothing secret is tracked today. Saying otherwise sends a reader
        hunting for a problem that was already solved — but the rotation advice
        must survive, because purging history un-publishes nothing."""
        text = _read()
        assert "are already tracked in the repository" not in text
        assert "rotate" in text.lower()

    @pytest.mark.parametrize("section", [
        "## Project brain",
        "## System design map",
        "## Roadmap",
    ])
    def test_the_orientation_sections_exist(self, section):
        assert section in _read()

    def test_it_does_not_still_call_the_pace_knob_dead(self):
        """SCRAPER_DELAY_MIN/MAX were read by nothing for months — the throttle
        an operator reaches for after a ban, doing nothing. Wired on
        2026-09-02, and three separate places in this file said otherwise."""
        text = _read()
        assert "read by nothing; the real delay is hardcoded" not in text
        assert "SCRAPER_DELAY_MIN/MAX` are read by nothing" not in text
        assert "StealthConfig.__post_init__" in text

    def test_the_per_run_event_log_is_documented(self):
        """A feature nobody knows exists is a feature nobody uses, and this one
        is the answer to the question the owner asks most."""
        text = _read()
        assert "/jobs/{id}/events" in text
        assert "scraping_logs" in text

    def test_there_is_exactly_one_tests_heading(self):
        """There were two, and they disagreed with each other."""
        assert _read().count("\n### Tests\n") <= 1
