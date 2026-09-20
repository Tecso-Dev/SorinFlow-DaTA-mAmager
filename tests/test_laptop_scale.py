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


class TestTheUsersList:
    """Ten columns in a two-thirds card scrolled sideways and cut the username
    off one end and the actions off the other (screenshot, 2026-09-20). One
    row per account now, three zones that wrap — keyed on the card's own
    width, because the list lives in a column, not in the window."""

    def test_it_is_rows_not_a_scrolling_table(self):
        html = Path("frontend/index.html").read_text(encoding="utf-8")
        blk = html[html.index("لیست کاربران"):html.index('id="users-table"') + 40]
        assert "table-responsive" not in blk and "<table" not in blk
        assert 'class="user-list" id="users-table"' in html
        js = Path("frontend/js/app.js").read_text(encoding="utf-8")
        fn = js[js.index("async function loadUsers()"):js.index("async function openPermsEditor")]
        assert "row.className = 'user-row'" in fn and "<td>" not in fn
        for zone in ("ur-who", "ur-details", "ur-actions"):
            assert f'class="{zone}"' in fn

    def test_the_zones_wrap_by_the_cards_width(self):
        assert "container-type: inline-size" in _block(".user-list {")
        assert "@container (max-width: 720px)" in CSS and "@container (max-width: 460px)" in CSS
        assert "grid-column: 1 / -1" in _block("@container (max-width: 720px)")
        assert "overflow-wrap: anywhere" in CSS[CSS.index(".ur-name,"):CSS.index(".ur-details")]
