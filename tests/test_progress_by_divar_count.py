"""
«تعداد دقیق آگهی‌های موجود در دیوار استخراج و گزارش شود … و درصد پیشرفت بر
اساس این محاسبه شود.»

The pool was the more exact denominator — it is what the loop walks — but it
is a number nobody sees until the run is over. «۶۰ از ۱۲۰» reads against the
figure the panel showed before the button was pressed. So: Divar's count
when it answered, the pool when it did not.

Two things keep it from lying. The pool can run PAST the count — Divar's
result page injects promoted ads its own total leaves out — and progress
clamps at 100 rather than reading 123%. And the pool can fall SHORT of it,
in which case completion fills the bar, because a finished run is finished
whatever Divar said it held.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_pdc.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.models.scraping_job import ScrapingJob  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRAPER = open(os.path.join(ROOT, "app/scraper/divar_scraper.py"), encoding="utf-8-sig").read()
APP_JS = open(os.path.join(ROOT, "frontend/js/app.js"), encoding="utf-8").read()
DB = open(os.path.join(ROOT, "app/database.py"), encoding="utf-8").read()


def job(total, scraped):
    j = ScrapingJob()
    j.total_items, j.scraped_items = total, scraped
    return j


class TestTheDenominator:
    def test_divars_count_is_stored_on_the_run(self):
        assert "divar_count" in ScrapingJob.__table__.c
        assert "job.divar_count = int(_divar_total)" in SCRAPER

    def test_it_is_the_denominator_when_known(self):
        i = SCRAPER.index("job.total_items = (job.divar_count")
        assert "else len(all_listings)" in SCRAPER[i:i + 200]

    def test_the_pool_is_the_fallback_not_zero(self):
        """Divar not answering must not freeze the bar at 0/0."""
        i = SCRAPER.index("job.total_items = (job.divar_count")
        assert "> 0" in SCRAPER[i:i + 200]

    def test_the_migration_adds_it(self):
        assert "ADD COLUMN IF NOT EXISTS divar_count INTEGER" in DB


class TestItCannotLie:
    def test_a_pool_past_the_count_reads_100_not_123(self):
        """148 candidates, Divar said 120 — the real run that prompted the
        earlier argument against this denominator."""
        assert job(120, 148).progress == 100.0

    def test_the_last_candidate_inside_the_count_is_not_yet_full(self):
        assert job(120, 119).progress < 100

    def test_the_clamp_is_in_the_model_not_the_panel(self):
        """Every consumer — panel, API, export — reads the same number."""
        src = inspect.getsource(ScrapingJob.progress.fget)
        assert "min(100.0" in src

    def test_completion_still_fills_a_run_that_fell_short(self):
        """The rule from the earlier fix survives: a finished run is finished
        whatever Divar said it held."""
        i = SCRAPER.index('job.status = "completed"')
        assert "job.scraped_items = job.total_items" in SCRAPER[i:i + 700]

    def test_an_empty_total_does_not_divide_by_zero(self):
        assert job(0, 0).progress == 0


class TestThePanelShowsBothNumbers:
    def test_the_bar_says_n_of_N(self):
        assert "${job.scraped_items} / ${job.total_items}" in APP_JS

    def test_the_tooltip_says_which_denominator(self):
        assert "تعدادی که دیوار برای این فیلترها اعلام کرد" in APP_JS
        assert "نامزدهای جمع‌شده" in APP_JS


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
        assert "scraper-max-items" in APP_JS[i:i + 600]

    def test_an_automatic_refresh_does_not_nag(self):
        i = APP_JS.index("async function estimateScrape(quiet = false)")
        assert "if (!quiet) showToast" in APP_JS[i:i + 600]

    def test_it_is_wired_when_the_section_opens(self):
        i = APP_JS.index("case 'scraper':")
        assert "_wireEstimateRefresh()" in APP_JS[i:i + 250]
