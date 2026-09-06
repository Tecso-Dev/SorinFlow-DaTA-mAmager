"""
The unsaved listings, where they can be looked at.

Asked for as: «یه فیلدی تو اسکرپ باز کنی به اسم اسکرپ های ناموفق … لینک دیوار
اینارو بزاری تو اون فیلد که بشه بعدا اسکرپ تکی کرد» — so the two things that
matter are the link being present and a single re-scrape being one click, not
a copy-paste into another box.
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_JS = open(os.path.join(ROOT, "frontend/js/app.js"), encoding="utf-8").read()
INDEX = open(os.path.join(ROOT, "frontend/index.html"), encoding="utf-8").read()


class TestItIsReachableFromTheJobRow:
    def test_every_row_offers_it(self):
        assert "showSkipped('${job.job_id}')" in APP_JS

    def test_it_sits_beside_the_log_button(self):
        i = APP_JS.index("showJobLog('${job.job_id}')")
        assert "showSkipped(" in APP_JS[i:i + 700]

    def test_it_is_offered_for_finished_runs_too(self):
        """The question is asked after a run, not during it — gating this on
        `running` would hide it exactly when it is wanted."""
        i = APP_JS.index("showSkipped('${job.job_id}')")
        window = APP_JS[i - 400:i]
        assert "['running', 'paused', 'pending'].includes" not in window


class TestTheModal:
    def test_it_exists(self):
        assert 'id="skippedModal"' in INDEX

    def test_it_is_scrollable(self):
        """Some runs skip a hundred listings."""
        i = INDEX.index('id="skippedModal"')
        assert "modal-dialog-scrollable" in INDEX[i:i + 300]

    def test_it_has_somewhere_for_the_summary_and_the_rows(self):
        assert 'id="skipped-summary"' in INDEX and 'id="skipped-body"' in INDEX


class TestEachRowCanBeActedOn:
    def test_the_divar_link_is_shown(self):
        assert 'href="${esc(r.url)}"' in APP_JS

    def test_it_opens_in_a_new_tab_without_handing_over_the_opener(self):
        i = APP_JS.index('href="${esc(r.url)}"')
        assert 'target="_blank"' in APP_JS[i:i + 200]
        assert 'rel="noopener"' in APP_JS[i:i + 200]

    def test_a_single_rescrape_is_one_click(self):
        assert "rescrapeSkipped(" in APP_JS

    def test_it_fills_the_single_scrape_box_and_runs_it(self):
        i = APP_JS.index("function rescrapeSkipped(")
        block = APP_JS[i:i + 700]
        assert "getElementById('single-url')" in block
        assert "scrapeSingle()" in block

    def test_it_closes_the_modal_first(self):
        i = APP_JS.index("function rescrapeSkipped(")
        assert "modal.hide()" in APP_JS[i:i + 700]

    def test_the_reason_is_shown_in_persian(self):
        assert "r.reason_label" in APP_JS

    def test_the_title_falls_back_to_the_token(self):
        """A listing with no title still has to be identifiable."""
        assert "r.title || r.divar_id" in APP_JS


class TestItCanBeNarrowedAndCopied:
    def test_the_buckets_are_clickable(self):
        assert "filterSkipped(" in APP_JS

    def test_filtering_is_applied_to_what_is_rendered(self):
        i = APP_JS.index("function visibleSkipped()")
        assert "_skippedFilter" in APP_JS[i:i + 300]

    def test_all_the_links_can_be_copied_at_once(self):
        assert "copySkippedLinks" in APP_JS

    def test_copying_respects_the_filter(self):
        i = APP_JS.index("async function copySkippedLinks()")
        assert "visibleSkipped()" in APP_JS[i:i + 400]

    def test_a_browser_refusing_the_clipboard_is_not_a_silent_failure(self):
        i = APP_JS.index("async function copySkippedLinks()")
        assert "catch" in APP_JS[i:i + 500]


class TestTheEmptyCase:
    def test_a_clean_run_says_so_rather_than_showing_a_blank(self):
        i = APP_JS.index("async function showSkipped(")
        assert "چیزی کنار گذاشته نشد" in APP_JS[i:i + 2000]

    def test_it_mentions_that_older_runs_have_no_history(self):
        i = APP_JS.index("async function showSkipped(")
        assert "سابقه‌ای ندارد" in APP_JS[i:i + 2000]


class TestThePanelWillBeReloaded:
    def test_the_cache_buster_moved(self):
        m = re.search(r"js/app\.js\?v=([0-9a-z]+)", INDEX)
        assert m and m.group(1) != "20260906a"
