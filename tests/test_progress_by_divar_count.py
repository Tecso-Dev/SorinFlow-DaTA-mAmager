"""
«تعداد دقیق آگهی‌های موجود در دیوار استخراج و گزارش شود … و درصد پیشرفت بر
اساس این محاسبه شود.»

That made Divar's count the progress denominator, and #29 took it back out.
Divar's count for the filters ignores the day — Divar does not filter by it
— so a one-day run that collected 24 candidates read «251 / 251», and at the
end «بررسی» was bent to match it.

What stands now: Divar's count is still asked for at run time and stored on
the run (divar_count), and the panel shows it as «دیوار می‌گوید: N» beside
the run's own pair — «بررسی» (examined) over «کل» (this run's pool). The bar
walks the pool, never reads past 100, and a completed run is full.
"""
import inspect
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_pdc.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.models.scraping_job import ScrapingJob  # noqa: E402
import _scrape_harness as h  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_JS = open(os.path.join(ROOT, "frontend/js/app.js"), encoding="utf-8").read()
DB = open(os.path.join(ROOT, "app/database.py"), encoding="utf-8").read()


def job(total, scraped):
    j = ScrapingJob()
    j.total_items, j.scraped_items = total, scraped
    return j


def job_like(*, total, scraped, status):
    j = job(total, scraped)
    j.status = status
    return j


@pytest.fixture
async def db(monkeypatch):
    h.quiet(monkeypatch, divar_says=251)
    eng, maker = await h.open_db()
    yield maker
    await eng.dispose()


class TestTheDenominator:
    async def test_divars_count_is_stored_on_the_run(self, db):
        job_id = await h.new_job(db, max_items=10)
        feed = [h.token() for _ in range(3)]
        job, _ = await h.run(db, job_id, {"feed": feed, "pages": {
            t: h.page(t, phone=h.PHONE.format(800 + i)) for i, t in enumerate(feed)}})
        assert job.divar_count == 251

    async def test_it_is_kept_beside_the_pool_not_under_it(self, db):
        """«کل» is the pool the run walks; Divar's 251 is its own number."""
        job_id = await h.new_job(db, max_items=10)
        feed = [h.token() for _ in range(3)]
        job, _ = await h.run(db, job_id, {"feed": feed, "pages": {
            t: h.page(t, phone=h.PHONE.format(810 + i)) for i, t in enumerate(feed)}})
        assert (job.scraped_items, job.total_items, job.divar_count) == (3, 3, 251)
        running = job_like(total=24, scraped=12, status="running")
        running.divar_count = 251
        assert running.progress == 50.0

    async def test_divar_not_answering_changes_nothing_but_the_line(self, db, monkeypatch):
        """No count from Divar: no «دیوار می‌گوید», and the pair is the same."""
        async def _silent(_city, _form):
            return None, "stubbed"
        from app.services import divar_count
        monkeypatch.setattr(divar_count, "fetch_post_count", _silent)
        job_id = await h.new_job(db, max_items=10)
        feed = [h.token() for _ in range(2)]
        job, _ = await h.run(db, job_id, {"feed": feed, "pages": {
            t: h.page(t, phone=h.PHONE.format(820 + i)) for i, t in enumerate(feed)}})
        assert job.divar_count is None and (job.scraped_items, job.total_items) == (2, 2)

    def test_the_migration_adds_it(self):
        assert "ADD COLUMN IF NOT EXISTS divar_count INTEGER" in DB


class TestItCannotLie:
    def test_a_pool_past_the_count_reads_100_not_123(self):
        """148 examined of a total of 120 — the clamp still holds whatever
        the two numbers are."""
        assert job(120, 148).progress == 100.0

    def test_the_last_candidate_inside_the_count_is_not_yet_full(self):
        assert job(120, 119).progress < 100

    def test_the_clamp_is_in_the_model_not_the_panel(self):
        """Every consumer — panel, API, export — reads the same number."""
        src = inspect.getsource(ScrapingJob.progress.fget)
        assert "min(100.0" in src

    def test_a_completed_run_is_full_whatever_it_walked(self):
        """A run that met its target, or a day that held nothing at all, is
        over — the bar says so without «بررسی» being set to «کل»."""
        assert job_like(total=10, scraped=2, status="completed").progress == 100.0
        assert job_like(total=0, scraped=0, status="completed").progress == 100.0

    async def test_a_cut_short_run_is_partial_and_keeps_divars_count_apart(self, monkeypatch):
        """Run 47 read «105 / 105» and «تکمیل شده» over the 24 candidates
        Divar let it have (#28, #29): now it is «ناقص», «کل» is its own pool
        of 24, and Divar's 105 is kept apart."""
        from _scripted_run import page, refused, scripted_run, tokens
        run = scripted_run(monkeypatch)
        done, _, _ = await run([page(1, tokens("pa", 40), next_page=False, count=120)],
                               category="rent-apartment")
        assert done.status == "completed" and done.progress == 100.0
        assert (done.total_items, done.divar_count) == (40, 120)
        cut, _, _ = await run([page(1, tokens("pb", 24), count=105), refused(429)],
                              category="rent-apartment")
        assert cut.status == "partial" and (cut.total_items, cut.divar_count) == (24, 105)
        assert cut.scraped_items == 24

    def test_an_empty_total_does_not_divide_by_zero(self):
        assert job(0, 0).progress == 0


class TestTheRequestedNumber:
    """A run for 200 stops when 200 new listings are saved. Against Divar's
    4253 alone, job 37 sat at 4% with 176 saved and then jumped to 100%."""

    @staticmethod
    def run(total, scraped, new, max_items):
        j = job(total, scraped)
        j.new_items, j.config = new, {"max_items": max_items}
        return j

    def test_job_37_reads_88_not_4(self):
        assert self.run(4253, 182, 176, 200).progress == 88.0

    def test_walking_the_pool_still_moves_the_bar_when_nothing_is_new(self):
        """A rescrape of listings we already have saves nothing new; the
        bar still follows the listings it has been through."""
        assert self.run(10, 5, 0, 10).progress == 50.0

    def test_a_whole_day_run_has_no_cap_and_follows_the_count(self):
        assert self.run(120, 30, 25, None).progress == 25.0

    def test_a_search_run_sent_without_a_number_measures_against_100(self):
        """The scraper falls back to 100 (max_items or 100); so does the bar."""
        j = job(4253, 40)
        j.new_items, j.config = 50, {"city": "urmia"}
        assert j.max_items == 100 and j.progress == 50.0

    def test_a_whole_day_run_without_a_number_has_no_target(self):
        j = job(120, 30)
        j.new_items, j.config = 25, {"posted_date": "2026-09-26"}
        assert j.max_items is None and j.progress == 25.0

    def test_a_run_from_before_the_config_column_has_no_target(self):
        assert job(120, 30).max_items is None

    def test_saving_past_the_target_clamps(self):
        assert self.run(4253, 260, 201, 200).progress == 100.0

    def test_the_target_reaches_the_panel(self):
        from app.schemas import ScrapingJobResponse
        assert "max_items" in ScrapingJobResponse.model_fields
        assert self.run(4253, 182, 176, 200).to_dict()["max_items"] == 200
        assert "آگهی تازهٔ درخواستی" in APP_JS


class TestThePanelShowsBothNumbers:
    """The behaviour is checked by rendering the real _renderJobsTable
    (tests/js/jobs_table.mjs, run by test_jobs_table.py): the pair is the
    run's own and Divar's count has a line of its own, «دیوار می‌گوید»."""

    def test_the_counts_are_shown_beside_the_bar(self):
        """Moved out of the bar's cell into their own column — the pair was
        unreadable crammed under the percent."""
        assert '<bdi title="بررسی‌شده">${job.scraped_items}</bdi>' in APP_JS
        assert '<bdi title="کل">${job.total_items}</bdi>' in APP_JS

    def test_the_tooltip_says_whose_numbers_they_are(self):
        start = APP_JS.index("function _renderJobsTable(")
        fn = APP_JS[start:APP_JS.index("\n}\n", start)]
        assert "همین اجرا" in fn, "the pair's tooltip does not say it is the run's own"
        assert "دیوار می‌گوید: ${esc(job.divar_count)}" in fn


class TestTheCountIsOnScreenBeforeTheRun:
    def test_the_estimate_refreshes_as_filters_change(self):
        assert "function scheduleEstimate()" in APP_JS
        assert "form.addEventListener('input'" in APP_JS

    def test_it_is_debounced(self):
        """Every keystroke in a price box is not a request to Divar."""
        i = APP_JS.index("function scheduleEstimate()")
        assert "setTimeout(() => estimateScrape(true), 900)" in APP_JS[i:i + 400]

    def test_it_needs_a_city_and_a_category_first(self):
        i = APP_JS.index("function scheduleEstimate()")
        assert "if (!city || !cat) return;" in APP_JS[i:i + 400]

    def test_the_count_field_does_not_retrigger_it(self):
        """max_items is ours, not a Divar filter — changing it changes
        nothing Divar would count."""
        i = APP_JS.index("function _wireEstimateRefresh()")
        # the count box's real id — the earlier name did not exist
        assert "scraper-pages" in APP_JS[i:i + 600]

    def test_an_automatic_refresh_does_not_nag(self):
        i = APP_JS.index("async function estimateScrape(quiet = false)")
        assert "if (!quiet) showToast" in APP_JS[i:i + 600]

    def test_it_is_wired_when_the_section_opens(self):
        i = APP_JS.index("case 'scraper':")
        assert "_wireEstimateRefresh()" in APP_JS[i:i + 250]
