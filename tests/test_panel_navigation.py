"""
Fifteen screens in one flat list.

Finding one meant reading all fifteen, and the two labels the menu had put
the scraper's own session page under «تنظیمات» — three screens away from the
scraper it belongs to. The menu is grouped by the work now, and there is a
palette so nobody has to read the menu at all.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
JS = (ROOT / "frontend/js/app.js").read_text(encoding="utf-8")
CSS = (ROOT / "frontend/css/style.css").read_text(encoding="utf-8")

# every screen the menu offers, in the order the markup lists them
NAV_IDS = re.findall(r'id="(?:nav-link-([a-z]+)|nav-(users))"', HTML)
NAV_SECTIONS = [a or b for a, b in NAV_IDS]


class TestTheMenuIsGrouped:

    def test_four_groups_and_every_screen_inside_one(self):
        groups = re.findall(r'data-group="([a-z]+)"', HTML)
        assert len(groups) == 4, f"expected four groups, found {groups}"
        js_groups = re.search(r"const NAV_GROUPS = \{(.*?)\n\};", JS, re.S).group(1)
        listed = set(re.findall(r"'([a-z]+)'", js_groups)) - set(groups)
        assert set(NAV_SECTIONS) <= listed, \
            f"these screens are in the menu but no group claims them: {set(NAV_SECTIONS) - listed}"

    def test_the_markup_and_the_script_agree_on_the_groups(self):
        for key in re.findall(r'data-group="([a-z]+)"', HTML):
            assert f"{key}:" in JS.split("const NAV_GROUPS")[1][:400], f"{key} has no list in NAV_GROUPS"

    def test_the_scrapers_session_page_sits_with_the_scraper(self):
        """It was under Settings, three screens from the thing it belongs to."""
        scrape = re.search(r"scrape:\s*\[(.*?)\]", JS).group(1)
        assert "'auth'" in scrape and "'scraper'" in scrape

    def test_the_group_holding_the_open_screen_is_never_collapsed(self):
        """Collapsing it leaves the panel with nothing highlighted."""
        body = JS.split("function _paintNavGroups()")[1].split("\n}")[0]
        assert "_currentSection" in body and "live" in body

    def test_showsection_repaints_the_groups(self):
        body = JS.split("function showSection(")[1].split("\n}")[0]
        assert "_paintNavGroups()" in body

    def test_the_choice_survives_a_reload(self):
        assert "_NAV_SHUT_KEY" in JS and "localStorage" in JS.split("function toggleNavGroup")[1][:400]

    def test_a_collapsed_group_actually_has_no_height(self):
        """grid-template-rows: 0fr zeroes the first row only, and a group is
        four links — it collapsed nothing."""
        rule = CSS.split(".nav-group.shut .nav-group-body")[1].split("}")[0]
        assert "max-height: 0" in rule
        bare = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)      # not the note explaining why
        assert "grid-template-rows: 0fr" not in bare


class TestThePalette:

    def test_it_opens_on_the_shortcut_and_on_a_click(self):
        assert "'k' || e.key === 'K'" in JS and "ctrlKey || e.metaKey" in JS
        assert HTML.count("openPalette()") >= 2, "a keyboard shortcut alone strands the mouse"

    def test_it_offers_screens_and_things_to_do(self):
        assert "PALETTE_ACTIONS" in JS
        kinds = JS.split("function _paletteBuild()")[1].split("\n}")[0]
        assert "'صفحه'" in kinds and "'کار'" in kinds

    def test_it_never_offers_a_screen_this_role_cannot_open(self):
        """Screens, actions and the CRM's own tabs — every group it builds."""
        body = JS.split("function _paletteBuild()")[1].split("\n}")[0]
        groups = [g for g in body.split("for (const") if "out.push" in g]
        assert groups, "nothing is being built"
        for g in groups:
            assert "_isSectionAllowed" in g or "_isSectionAllowed" in body.split(g)[0][-120:], \
                f"an unchecked group: {g.strip()[:70]}"

    def test_the_old_word_still_finds_the_renamed_screen(self):
        """«کوکی» is what the office calls the Divar session page."""
        words = JS.split("PALETTE_EXTRA_WORDS = {")[1].split("\n};")[0]
        assert "کوکی" in words and "ملک" in words

    def test_arabic_letters_and_persian_digits_are_folded(self):
        """A phone keyboard sends ي and ك; without folding, «کاربران» typed on
        a phone finds nothing."""
        fold = JS.split("function _fold(")[1].split("\n}")[0]
        for pair in ("يى", "ك"):
            assert pair in fold
        assert "۰-۹" in fold

    def test_the_results_are_escaped(self):
        row = JS.split("function _paletteRender(")[1].split("\n}")[0]
        assert "esc(it.label)" in row and "esc(it.hint)" in row

    def test_arrows_enter_and_escape_all_work(self):
        keys = JS.split("function _paletteKeys(")[1].split("\n}")[0]
        for k in ("ArrowDown", "ArrowUp", "Enter", "Escape"):
            assert k in keys

    def test_on_a_phone_it_takes_the_whole_screen(self):
        # the file has several 640px blocks; find the one the palette is in
        phone = next(b for b in CSS.split("@media (max-width: 640px)")[1:]
                     if ".palette" in b.split("\n}")[0])
        assert ".palette { width: 100%" in phone and "height: 100%" in phone
        assert ".palette-foot { display: none; }" in phone, \
            "a touch keyboard cannot press Ctrl+K, so the hints are noise"
