"""
Forty-two buttons competing for one glance.

A call card carried seven actions, a match five, a price drop four — six cards
to a screen on the tab the office opens every morning, and the call card alone
was 362px on a phone. Six screens of scrolling for eleven things to do.

The two actions that finish almost every card stay out. The rest go behind a
disclosure, and it is a <details>: the browser already has that widget,
keyboard and screen-reader behaviour included.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = (ROOT / "frontend/js/app.js").read_text(encoding="utf-8")
CSS = (ROOT / "frontend/css/style.css").read_text(encoding="utf-8")
HTML = (ROOT / "frontend/index.html").read_text(encoding="utf-8")


CARDS = {"call": "_cqCard", "match": "_matchQueueCard", "drop": "_dropCard"}


def _actions(which):
    """The action block of one card renderer."""
    body = JS.split(f"function {CARDS[which]}(")[1].split("\n}")[0]
    return body.split('<div class="cq-actions">')[1].split("\n        </div>")[0]


class TestTheCardsLeadWithTwoActions:

    def test_the_call_card_keeps_the_two_outcomes_that_end_most_calls(self):
        out = _actions("call").split("_cqMore(")[0]
        assert out.count("<button") == 2
        assert "'answered'" in out and "'no_answer'" in out

    def test_the_match_card_keeps_calling_and_texting(self):
        out = _actions("match").split("_cqMore(")[0]
        assert out.count("<button") == 2
        assert "'contacted'" in out and "mqSms(" in out

    def test_the_price_drop_keeps_who_wanted_it_and_seen(self):
        """A price drop exists to be matched against somebody, then filed."""
        out = _actions("drop").split("_cqMore(")[0]
        assert out.count("<button") == 2
        assert "showCustomersForProperty(" in out and "'seen'" in out

    def test_nothing_was_dropped_on_the_way(self):
        for which, total in (("call", 7), ("match", 5), ("drop", 4)):
            assert _actions(which).count("<button") == total, f"{which} card lost an action"


class TestTheDisclosureIsTheBrowsersOwn:

    def test_it_is_a_details_element(self):
        helper = JS.split("function _cqMore(")[1].split("\n}")[0]
        assert "<details" in helper and "<summary>" in helper

    def test_all_three_cards_use_the_one_helper(self):
        for which in CARDS:
            assert "_cqMore(" in _actions(which)

    def test_open_it_takes_a_row_of_its_own(self):
        """Collapsed it sits beside the buttons; open it must not shove them
        out of line."""
        assert ".cq-more[open] { flex: 1 1 100%; }" in CSS

    def test_the_default_triangle_is_gone_in_both_engines(self):
        assert "list-style: none" in CSS.split(".cq-more > summary {")[1].split("}")[0]
        assert ".cq-more > summary::-webkit-details-marker { display: none; }" in CSS

    def test_all_three_controls_fit_one_row_on_a_phone(self):
        """At 46% the two buttons filled the line and the chip wrapped below,
        costing 42px on every card."""
        assert ".cq-actions > .btn { flex: 1 1 30%; }" in CSS


class TestTheExplanationIsClampedButSaysSo:

    def test_the_clamp_is_on_the_text_not_the_block(self):
        """A pseudo-element at the end of clamped text is clipped with it — the
        label offering to open it was itself invisible."""
        assert ".cq-intro .cq-intro-text {" in CSS
        rule = CSS.split(".cq-intro .cq-intro-text {")[1].split("}")[0]
        assert "-webkit-line-clamp: 2" in rule

    def test_all_three_explanations_carry_the_label(self):
        assert HTML.count('class="cq-intro-text"') == 3
        assert HTML.count('class="cq-intro-more"') == 3

    def test_the_label_flips_when_it_is_open(self):
        assert "content: 'بیشتر'" in CSS and ".cq-intro.open .cq-intro-more::after { content: 'کمتر'; }" in CSS

    def test_a_wide_screen_shows_all_of_it_and_offers_no_control(self):
        assert "@media (min-width: 769px) { .cq-intro-more { display: none; } }" in CSS

    def test_the_label_is_not_read_aloud_as_content(self):
        assert 'class="cq-intro-more" aria-hidden="true"' in HTML
