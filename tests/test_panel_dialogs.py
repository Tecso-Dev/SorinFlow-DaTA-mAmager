"""
The panel's own dialog, and the design rules it exists to keep.

window.prompt/confirm/alert render as browser chrome: «sorinflow.com says»
above a bare sentence, no icon, no explanation, and light-on-light against a
black panel. Sobhan asked for this twice on 2026-09-14 — once for the dialog
and once for a guide card built from Bootstrap utilities that came out white.
"""
import re
from pathlib import Path

import pytest

JS = Path("frontend/js/app.js")
CSS = Path("frontend/css/style.css")
HTML = Path("frontend/index.html")


def _code(p: Path) -> str:
    """Source with comment lines removed — the comments here describe the very
    things being asserted absent."""
    src = p.read_text(encoding="utf-8")
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(l for l in src.splitlines()
                     if not l.strip().startswith(("//", "*")))


class TestNoBrowserDialogsSurvive:

    @pytest.mark.parametrize("fn", ["prompt", "confirm", "alert"])
    def test_the_native_dialog_is_not_called(self, fn):
        code = _code(JS)
        # `askConfirm(` and `_askOpen(` must not count as `confirm(`/`alert(`
        hits = [m for m in re.finditer(rf"(?<![\w.$]){fn}\s*\(", code)]
        assert not hits, f"{fn}() is still called {len(hits)}x"

    def test_the_replacement_exists(self):
        code = _code(JS)
        for fn in ("function askConfirm", "function askText", "function askInfo"):
            assert fn in code


class TestTheDialogUsesThePanelsOwnLook:
    """Not Bootstrap's modal and not the browser's: the panel has a dialog
    vocabulary already — the OTP prompt — and two dialogs that look unrelated
    read as two products."""

    def test_it_reuses_the_otp_dialogs_shape(self):
        css = CSS.read_text(encoding="utf-8")
        ask = css[css.index(".ask-card {"):css.index(".ask-card {") + 700]
        assert "26px" in ask, "a different radius from the OTP card"
        assert "ask-ring" in css and "62px" in css[css.index(".ask-ring {"):css.index(".ask-ring {") + 300]

    @pytest.mark.parametrize("token", ["--text-muted", "--input-bg", "--surface", "--border"])
    def test_it_is_built_from_panel_tokens(self, token):
        css = CSS.read_text(encoding="utf-8")
        assert token in css[css.index("ask() — the panel's own dialog"):]

    @pytest.mark.parametrize("bad", ["--bs-secondary-bg", "--bs-body-bg", "--bs-light"])
    def test_no_bootstrap_surface_tokens(self, bad):
        """They resolve LIGHT. That is how the setup guide shipped as white
        boxes on a black panel."""
        # Comments stripped: the comment that explains NOT to use these names
        # them, and a bare substring check cannot tell a rule from a warning.
        css = re.sub(r"/\*.*?\*/", "", CSS.read_text(encoding="utf-8"), flags=re.S)
        tail = css[css.index(".ask-overlay {"):]
        assert bad not in tail
        assert bad not in _code(JS), f"{bad} used in markup"

    def test_severity_changes_how_it_looks(self):
        css = CSS.read_text(encoding="utf-8")
        assert ".ask-card.is-danger" in css and ".ask-card.is-warning" in css


class TestItBehavesLikeADialogShould:

    def test_escape_and_backdrop_close_it(self):
        code = _code(JS)
        fn = code[code.index("function _askOpen"):code.index("function askConfirm")]
        assert "'Escape'" in fn
        assert "e.target === overlay" in fn, "clicking outside does nothing"

    def test_a_destructive_dialog_does_not_focus_its_destructive_button(self):
        """Enter on a delete confirm must not delete."""
        code = _code(JS)
        fn = code[code.index("function _askOpen"):code.index("function askConfirm")]
        assert "tone === 'danger' ? '#ask-cancel' : '#ask-ok'" in fn

    def test_cancelling_a_text_dialog_is_null_not_empty(self):
        """Empty string is a legitimate answer — «clear this field» — so it
        must be distinguishable from «I changed my mind»."""
        code = _code(JS)
        fn = code[code.index("function _askOpen"):code.index("function askConfirm")]
        assert "const cancelValue = field ? null : false;" in fn

    def test_it_is_labelled_for_a_screen_reader(self):
        code = _code(JS)
        assert 'role="dialog"' in code and 'aria-modal="true"' in code

    def test_motion_is_optional(self):
        css = CSS.read_text(encoding="utf-8")
        assert "prefers-reduced-motion" in css[css.index("ask() — the panel's own dialog"):]

    def test_input_is_validated_before_the_dialog_closes(self):
        code = _code(JS)
        fn = code[code.index("function _askOpen"):code.index("function askConfirm")]
        assert "field.validate" in fn and "err.textContent" in fn


class TestTheForwarderLogTellsTheTruth:
    """The latency column showed «۳۱٬۵۳۶٬۰۰۰s» and «-۲۹۹٫۸s» — it is measured
    across two clocks, so a phone with the wrong time produces a figure that
    is not a duration at all."""

    def test_impossible_latencies_are_named_not_printed(self):
        code = _code(JS)
        fn = code[code.index("function _fwLatency"):code.index("async function loadForwarderLog")]
        assert "ms < 0" in fn and "ms > 300000" in fn
        assert "ساعت گوشی" in fn

    def test_a_plausible_latency_is_still_shown(self):
        code = _code(JS)
        fn = code[code.index("function _fwLatency"):code.index("async function loadForwarderLog")]
        assert "formatNumber(s)" in fn

    def test_the_code_itself_is_shown(self):
        """«show the code that the app sent and we received» — it is already
        masked to its last two digits where it is stored."""
        code = _code(JS)
        assert "d.code" in code[code.index("async function loadForwarderLog"):]
        html = HTML.read_text(encoding="utf-8")
        assert "<th style=\"width:6rem\">کد</th>" in html

    def test_the_stored_code_is_masked(self):
        """Showing it must not mean storing it in the clear."""
        src = Path("app/api/routes/scraper.py").read_text(encoding="utf-8")
        assert "_mask_code(" in src
