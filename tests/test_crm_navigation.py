"""
Two of the CRM's thirteen screens were reachable.

The tab strip scrolls sideways on a phone — a deliberate choice, thirteen tabs
wrapped is a wall — but the scrollbar is hidden, so eleven of them were behind
a drag with nothing on screen to say they existed. «معاملات» was a guess.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
JS = (ROOT / "frontend/js/app.js").read_text(encoding="utf-8")
CSS = (ROOT / "frontend/css/style.css").read_text(encoding="utf-8")

TAB_KEYS = sorted(set(re.findall(r'data-bs-target="#crm-tab-([a-z]+)"', HTML)))
PALETTE_TABS = JS.split("const CRM_TABS = [")[1].split("\n];")[0]


class TestEveryTabCanBeTyped:

    def test_the_palette_knows_all_thirteen(self):
        listed = set(re.findall(r"tab: '([a-z]+)'", PALETTE_TABS))
        assert set(TAB_KEYS) == listed, \
            f"the strip and the palette disagree: {set(TAB_KEYS) ^ listed}"

    def test_it_lands_on_the_tab_not_just_the_section(self):
        go = JS.split("function paletteGo(")[1].split("\n}")[0]
        assert "it.crmTab" in go and "goCrm(" in go

    def test_a_role_without_crm_is_not_offered_them(self):
        build = JS.split("function _paletteBuild()")[1].split("\n}")[0]
        assert "_isSectionAllowed('crm')" in build

    def test_each_one_carries_the_words_somebody_would_type(self):
        for line in PALETTE_TABS.strip().splitlines():
            if "tab:" not in line:
                continue
            words = re.search(r"words: '([^']*)'", line)
            assert words and len(words.group(1).split()) >= 2, f"thin keywords: {line.strip()[:60]}"


class TestPersianSuffixes:
    """«معامله» has to find «معاملات» — a substring match never will."""

    def test_the_stemmer_trims_the_endings_that_matter(self):
        stem = JS.split("function _stem(")[1].split("\n}")[0]
        for ending in ("های", "ها", "ات", "ان"):
            assert ending in stem
        assert "length > 4" in stem, "trimming a short word leaves nothing to match"

    def test_both_the_filter_and_the_ranking_use_it(self):
        flt = JS.split("function paletteFilter()")[1].split("\n}")[0]
        assert "_stemAll(hay)" in flt, "a stemmed query against an unstemmed haystack still misses"
        assert "headStem" in flt, "the tab must outrank the section that merely mentions it"


class TestTheStripSaysThereIsMore:

    def test_the_cut_off_edge_fades(self):
        assert "mask-image: linear-gradient(to left" in CSS
        strip = CSS.split("#crm-main-tabs::-webkit-scrollbar")[1]
        assert "mask-image" in strip.split("\n  }")[0] + strip[:400]

    def test_the_sideways_scroll_is_still_the_choice(self):
        """Thirteen tabs wrapped is a wall; this is an affordance, not a redesign."""
        assert "flex-wrap: nowrap !important" in CSS


class TestTheCallQueueStopsExplainingItself:

    def test_the_prose_is_two_lines_on_a_phone(self):
        rule = CSS.split(".cq-intro {")[1].split("}")[0]
        assert "-webkit-line-clamp: 2" in rule

    def test_the_rest_is_one_tap_away(self):
        assert ".cq-intro.open { -webkit-line-clamp: unset; }" in CSS
        assert "content: 'بیشتر'" in CSS and "content: 'کمتر'" in CSS
        assert "closest?.('.cq-intro')" in JS, "the panes render on demand, so the click is delegated"

    def test_the_desktop_keeps_the_whole_explanation(self):
        i = CSS.index(".cq-intro {")
        media = CSS.rfind("@media", 0, i)
        assert "max-width: 768px" in CSS[media:media + 40]
