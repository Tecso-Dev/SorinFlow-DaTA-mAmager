"""
One panel, every laptop.

A 1366 laptop at Windows' 125% zoom hands the panel 1093 CSS pixels; a
27" monitor hands it 2560. The same sheet served both, so the small one
got a quarter of its width taken by the sidebar and a price column that
wrapped into four lines, and the big one got 13.5px text spread across
the whole screen. Every size in the sheet is rem, so the html font-size
is the one knob — these tests pin the knob and the two layout fixes.
"""
import re
from pathlib import Path

CSS = Path("frontend/css/style.css").read_text(encoding="utf-8")


def _block(query):
    i = CSS.index(query)
    depth, j = 0, i
    while True:
        c = CSS[j]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return CSS[i:j + 1]
        j += 1


class TestTheKnob:

    def test_body_text_is_rem_so_the_html_size_scales_it(self):
        body = _block("\nbody {")
        assert "font-size: .84375rem" in body, "13.5px would ignore the html font-size"
        assert not re.search(r"font-size:\s*13\.5px", body)

    def test_small_laptops_get_a_smaller_scale_and_a_narrower_sidebar(self):
        blk = _block("@media (min-width: 901px) and (max-width: 1199px)")
        assert "html { font-size: 15px }" in blk
        assert "--sidebar-w: 216px" in blk
        assert "#crm-main-tabs .nav-link" in blk, "eleven tabs must not wrap into a wall"

    def test_wide_screens_get_a_larger_scale_and_a_capped_content_width(self):
        blk = _block("@media (min-width: 1600px)")
        assert "html { font-size: 17px }" in blk
        assert "max-width: 1720px" in blk and "margin: 0 auto" in blk
        assert "html { font-size: 18px }" in _block("@media (min-width: 2200px)")

    def test_phones_are_untouched(self):
        # the laptop rules start where the sidebar overlay ends
        assert "@media (min-width: 901px) and (max-width: 1199px)" in CSS
        assert "@media (max-width: 900px)" in CSS
        for blk in ("@media (min-width: 1600px)", "@media (min-width: 2200px)"):
            assert "#sidebar" not in _block(blk)


class TestThePriceColumn:

    def test_a_price_never_wraps_but_the_title_still_may(self):
        blk = _block("@media (min-width: 901px) {\n  #section-properties")
        assert "#section-properties .table td { white-space: nowrap }" in blk
        assert "td:nth-child(2) { white-space: normal" in blk
        # the title is the second column
        html = Path("frontend/index.html").read_text(encoding="utf-8")
        head = html[html.index('id="properties-table"') - 900:html.index('id="properties-table"')]
        ths = re.findall(r"<th>([^<]*)</th>", head)
        assert ths[:2] == ["کد ملک", "عنوان"], ths
