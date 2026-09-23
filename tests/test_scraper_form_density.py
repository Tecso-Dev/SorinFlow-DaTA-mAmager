"""
Two screens of empty money fields before the button that starts a run.

The new-scrape form was 1445px on a phone, and 663px of that was filters
almost nobody fills for a routine run: price, price per metre, area, rooms,
advertiser type, five feature checkboxes, publish date. They sat between
«دسته‌بندی» and «شروع اسکرپ».

Folding them is only safe while a filter can never be applied out of sight —
the panel remembers the form, so somebody could come back to a run narrowed by
a price they set last week and never see it.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
JS = (ROOT / "frontend/js/app.js").read_text(encoding="utf-8")
CSS = (ROOT / "frontend/css/style.css").read_text(encoding="utf-8")

FOLD = HTML.split('<details id="scraper-more"')[1].split("</details>")[0]
FORM = HTML.split('<form id="scraper-form">')[1].split("</form>")[0]


class TestWhatIsFoldedAndWhatIsNot:

    def test_the_optional_filters_are_inside(self):
        for block in ("scraper-buy-filters", "scraper-rent-filters",
                      "scraper-common-filters", "scraper-posted-date"):
            assert block in FOLD, f"{block} is still out in the open"

    def test_what_every_run_needs_stays_out(self):
        """City, category, how many, and the button that starts it."""
        outside = FORM.replace(FOLD, "")
        for keep in ("scraper-city", "scraper-category", "scraper-pages"):
            assert keep in outside, f"{keep} was folded away"

    def test_it_is_a_details_element(self):
        assert '<details id="scraper-more"' in HTML and "<summary>" in FOLD or "<summary" in HTML


class TestAFilterCanNeverBeAppliedOutOfSight:

    def test_a_set_filter_forces_it_open(self):
        sync = JS.split("function _scraperMoreSync()")[1].split("\n}")[0]
        assert "box.open = true" in sync

    def test_the_summary_counts_what_is_set(self):
        sync = JS.split("function _scraperMoreSync()")[1].split("\n}")[0]
        assert "scraper-more-count" in sync
        assert 'id="scraper-more-count"' in HTML

    def test_the_count_covers_every_filter_in_the_fold(self):
        listed = set(re.findall(r"'(scraper-[a-z-]+)'",
                                JS.split("_SCRAPER_FILTER_IDS = [")[1].split("];")[0]))
        in_fold = set(re.findall(r'id="(scraper-(?:min|max)-[a-z]+|scraper-has-[a-z]+|'
                                 r'scraper-advertiser-type|scraper-posted-date)"', FOLD))
        assert in_fold <= listed, f"counted by nothing: {in_fold - listed}"

    def test_a_filter_belonging_to_the_other_deal_type_is_not_counted(self):
        """Rent boxes keep their leftovers while a buy category is picked; they
        are not sent, so they must not be announced either."""
        fn = JS.split("function _scraperActiveFilters()")[1].split("\n}")[0]
        assert "closest('.d-none')" in fn

    def test_it_re_counts_when_the_form_changes(self):
        assert "if (e.target.closest?.('#scraper-more')) _scraperMoreSync();" in JS

    def test_it_re_counts_when_the_deal_type_changes(self):
        """Switching buy/rent swaps which price block is shown."""
        init = JS.split("function _initScraperDatePicker()")[1].split("\n}")[0]
        assert "_scraperMoreSync()" in init

    def test_a_remembered_form_is_counted_on_the_way_back(self):
        restore = JS.split("function restoreScraperForm()")[1].split("\n}\n")[0]
        assert "_scraperMoreSync()" in restore


class TestTheDateIsOnlyAFilterWhenSomebodyPickedOne:
    """persianDatepicker writes today into the field the moment it initialises,
    so a value alone does not mean anybody chose one — and the fold would sit
    open on every visit announcing a filter nobody set."""

    def test_the_count_asks_whether_it_was_chosen(self):
        fn = JS.split("function _scraperActiveFilters()")[1].split("\n}")[0]
        assert "dataset.userSet === '1'" in fn

    def test_choosing_one_marks_it(self):
        fn = JS.split("function _onScraperDateChange()")[1].split("\n}")[0]
        assert "dataset.userSet = '1'" in fn

    def test_clearing_it_unmarks_it(self):
        fn = JS.split("function _onScraperDateChange()")[1].split("\n}")[0]
        assert "delete el.dataset.userSet" in fn

    def test_a_remembered_date_counts(self):
        restore = JS.split("function restoreScraperForm()")[1].split("\n}\n")[0]
        assert "dateEl.dataset.userSet = '1'" in restore


class TestItLooksLikeTheRestOfThePanel:

    def test_the_default_triangle_is_gone_in_both_engines(self):
        rule = CSS.split(".scraper-more > summary {")[1].split("}")[0]
        assert "list-style: none" in rule
        assert ".scraper-more > summary::-webkit-details-marker { display: none; }" in CSS

    def test_a_fold_holding_something_says_so_while_still_closed(self):
        assert ".scraper-more.has-filters > summary" in CSS
