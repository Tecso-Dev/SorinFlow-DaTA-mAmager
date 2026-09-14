"""
«موقع لغو کردن اسکرپر این پنجره باز میشه ولی رنگش زرده و میخام اونو همرنگ
سایتمون یعنی glassmorphism کنی.»

The dialog already was glass — blur, translucent gradient, the OTP
modal's radius and glow. What read as foreign was the colour: the
«warning» tone was amber, the one hue nowhere else in this UI, so a
cancel dialog looked like a different product had opened it. The tone
stays — a caution should not look like a routine OK — but in the
violet→pink the panel already uses for its own primary actions.
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSS = open(os.path.join(ROOT, "frontend/css/style.css"), encoding="utf-8").read()
INDEX = open(os.path.join(ROOT, "frontend/index.html"), encoding="utf-8").read()


def rule(selector):
    i = CSS.index(selector)
    return CSS[i:CSS.index("}", i) + 1]


class TestTheWarningToneIsInTheBrand:
    def test_no_amber_is_left_on_the_warning_card(self):
        for amber in ("#fbbf24", "#f59e0b", "#d97706", "245,158,11"):
            assert amber not in rule(".ask-card.is-warning {"), amber
            assert amber not in rule(".ask-card.is-warning .ask-ok"), amber

    def test_it_uses_the_panels_violet_and_pink(self):
        assert "#a78bfa" in rule(".ask-card.is-warning {")
        assert "#f0a6ff" in rule(".ask-card.is-warning .ask-ok")

    def test_it_is_still_a_distinct_tone(self):
        """Not the default indigo, not the danger red — a caution should not
        look like a routine OK, and the three-tone system survives."""
        assert rule(".ask-card.is-warning {") != rule(".ask-card.is-danger  {")
        assert "#ef4444" in rule(".ask-card.is-danger  {")

    def test_white_text_on_the_new_button(self):
        """Amber needed dark text; violet does not."""
        assert "color: #fff" in rule(".ask-card.is-warning .ask-ok")

    def test_the_glass_itself_is_untouched(self):
        card = rule(".ask-card {")
        assert "backdrop-filter" in rule(".ask-overlay {")
        assert "border-radius: 26px" in card


class TestTheStylesheetWillBeReloaded:
    def test_the_cache_buster_moved(self):
        m = re.search(r"css/style\.css\?v=([0-9a-z]+)", INDEX)
        assert m and m.group(1) != "20260915a"
