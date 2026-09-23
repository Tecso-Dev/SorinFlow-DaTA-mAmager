"""
The owner's phone number was off the right edge of the screen.

Eight columns in a 375px viewport meant a 471px table inside a horizontal
scroller, and the two columns past the fold were the phone number and the
buttons — the one thing a consultant standing outside a building has the panel
open for, reachable only by dragging the table sideways. Above it, five
full-width filter controls and two export buttons: a screen and a half before
a single listing.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / "frontend/css/style.css").read_text(encoding="utf-8")
JS = (ROOT / "frontend/js/app.js").read_text(encoding="utf-8")

PHONE = "".join(b for b in CSS.split("@media (max-width: 720px)")[1:]
                if "#section-properties" in b)
ROW = JS.split('<td data-l="کد"')[1].split("</tr>")[0] if '<td data-l="کد"' in JS else ""


class TestTheRowBecomesACard:

    def test_every_cell_says_what_it_is(self):
        """Without the header row, a bare «۱۴۰ متر» has to name itself."""
        for label in ("کد", "عنوان", "شهر", "متراژ", "اتاق", "قیمت", "شماره تماس"):
            assert f'data-l="{label}"' in JS, f"{label} carries no label"
        assert "content: attr(data-l)" in PHONE

    def test_the_header_row_goes_and_the_rows_stop_being_rows(self):
        assert "thead { display: none; }" in PHONE
        assert re.search(r"#section-properties \.table tbody tr \{[^}]*display: flex", PHONE, re.S)

    def test_the_phone_number_is_on_its_own_full_width_line(self):
        """It is the reason the screen is open; it does not share a line and it
        is not off the edge."""
        assert re.search(r"td\.pt-phone,?[^{]*\{[^}]*flex: 1 0 100%", PHONE, re.S) or \
               "td.pt-actions { flex: 1 0 100%; }" in PHONE.replace("\n", " ")
        assert "td.pt-phone a { font-size" in PHONE

    def test_the_title_heads_the_card(self):
        assert "td.pt-title { order: -1; }" in PHONE

    def test_the_empty_state_is_not_dressed_as_a_result(self):
        """«هیچ ملکی یافت نشد» is one cell spanning eight columns."""
        assert "td[colspan]" in PHONE


class TestTheFiltersStopEatingTheScreen:

    def test_no_spreadsheet_buttons_on_a_phone(self):
        assert '[onclick^="exportProperties"] { display: none; }' in PHONE

    def test_the_hidden_rent_filters_stay_hidden(self):
        """They are a .d-flex child of the same header, and an unqualified
        `display: grid !important` outranked Bootstrap's d-none."""
        assert ".card-header > .d-flex:not(.d-none)" in PHONE, \
            "this rule would force the hidden rent filters open"


class TestTwoThingsTheTableGotWrong:

    def test_the_room_count_is_written_in_persian_like_every_other_number(self):
        assert "formatNumber(property.rooms)" in ROW, "«۱۴۰ متر» beside a Latin 2"

    def test_a_title_short_enough_to_fit_gets_no_ellipsis(self):
        assert "property.title.length > 40" in ROW
        assert "property.title.substring(0, 40))}..." not in JS
