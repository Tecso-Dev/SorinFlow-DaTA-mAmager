"""
«تعداد آگهی برای اسکرپ رو فیلدش همیشه بای دیفالت خالی باشه.»

It opened as 50 twice over: a value="50" baked into the markup, and the
field on the list the form saves to localStorage and restores from. A 50
left over from last time silently capped the next scrape, and the box read
as if somebody had typed it. Empty means «all of them».
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_JS = open(os.path.join(ROOT, "frontend/js/app.js"), encoding="utf-8").read()
INDEX = open(os.path.join(ROOT, "frontend/index.html"), encoding="utf-8").read()


class TestItOpensEmpty:
    def test_no_value_is_baked_into_the_markup(self):
        tag = re.search(r'<input[^>]*id="scraper-pages"[^>]*>', INDEX).group(0)
        assert 'value=' not in tag

    def test_it_is_not_saved_between_runs(self):
        i = APP_JS.index("const _SCRAPER_TEXT_FIELDS = [")
        assert "'scraper-pages'" not in APP_JS[i:APP_JS.index("];", i)]

    def test_the_reason_is_written_beside_the_list(self):
        i = APP_JS.index("const _SCRAPER_TEXT_FIELDS = [")
        assert "not a preference to carry between runs" in APP_JS[i - 400:i]

    def test_the_other_fields_are_still_remembered(self):
        """Only the count is a per-run decision; the filters are preferences."""
        i = APP_JS.index("const _SCRAPER_TEXT_FIELDS = [")
        block = APP_JS[i:APP_JS.index("];", i)]
        for f in ("scraper-category", "scraper-min-deposit", "scraper-advertiser-type"):
            assert f"'{f}'" in block


class TestTheEstimateRefreshNamesTheRealField:
    def test_the_count_field_is_excluded_by_its_real_id(self):
        """It was excluded by an id that does not exist, so typing in the
        count box re-asked Divar for nothing."""
        i = APP_JS.index("function _wireEstimateRefresh()")
        assert "'scraper-pages'" in APP_JS[i:i + 600]
        assert "'scraper-max-items'" not in APP_JS[i:i + 600]
