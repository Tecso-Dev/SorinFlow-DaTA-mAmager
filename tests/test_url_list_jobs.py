"""
A run can be handed an explicit list of listings.

«نوشت با موفقیت اسکرپ شد ولی … نه تو لیست املاک شمارش افتاده و نه از این
قسمت بدون شماره پاک شده.» The single scrape ran inside the HTTP request and
answered «success» whenever a row was saved — number or not. Inside a
request there was also nowhere for a code prompt to go: the panel's OTP
dialog polls jobs, and a request cannot wait on it.

«یه علامت رفرش کلی دقیقاً همین فیلد بذار وقتی اونو بزنم همه رو اسکرپ کنه.»

Both are the same thing now: a job, of one or of many, pointed at a list.
The same reveal, OTP dialog, pacing, rotation, log, skipped-list
bookkeeping and finish line as any run — because it IS any run.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_ulj.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.api.routes import scraper as sr  # noqa: E402
from app.scraper.divar_scraper import DivarScraper  # noqa: E402
from app.schemas import ScrapingJobCreate  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_JS = open(os.path.join(ROOT, "frontend/js/app.js"), encoding="utf-8").read()
RUN = inspect.getsource(DivarScraper.start_scraping_job)


class TestTokenFromUrl:
    @staticmethod
    def tok(u):
        return DivarScraper._token_from_url(u)

    def test_a_bare_token_url(self):
        assert self.tok("https://divar.ir/v/gamyVmL9") == "gamyVmL9"

    def test_a_slugged_url(self):
        assert self.tok("https://divar.ir/v/آپارتمان-تک-واحدی-در-مافی/gamyVmL9") == "gamyVmL9"

    def test_a_query_string_does_not_join_the_token(self):
        assert self.tok("https://divar.ir/v/gamyVmL9?utm=x") == "gamyVmL9"

    def test_a_token_with_dashes(self):
        assert self.tok("https://divar.ir/v/gag-D-FT") == "gag-D-FT"

    def test_not_a_listing(self):
        assert self.tok("https://divar.ir/s/urmia/rent-apartment") is None
        assert self.tok("") is None and self.tok(None) is None


class TestTheRunTakesAList:
    def test_the_parameter(self):
        assert "urls" in inspect.signature(DivarScraper.start_scraping_job).parameters

    def test_a_list_skips_collection(self):
        # the docstring mentions `urls` too; anchor on the code line
        i = RUN.index("if urls:\n                # An explicit list")
        block = RUN[i:RUN.index("seen_ids: set =", i)]
        assert "_collect_listings_robust(" in block[block.index("else:"):]
        assert "_collect_listings_robust(" not in block[:block.index("else:")]

    def test_it_is_deduplicated_and_ordered(self):
        i = RUN.index("if urls:")
        block = RUN[i:RUN.index("seen_ids: set =", i)]
        assert "_seen" in block and "all_listings.append(" in block

    def test_the_log_says_it_was_a_list(self):
        assert "بدون جست‌وجو" in RUN

    def test_a_named_listing_is_always_opened(self):
        """Not skipped as a duplicate — a listing named by hand is one
        somebody wants opened whatever the table already holds."""
        assert "if not urls and await self.property_exists(listing['divar_id']):" in RUN

    def test_no_count_is_asked_for_a_list(self):
        i = RUN.index("if urls:\n                    raise StopAsyncIteration")
        assert i > 0

    def test_labels_do_not_overwrite_the_page(self):
        assert "elif not urls:" in RUN
        assert "the page's own breadcrumb is the truth" in RUN


class TestTheApi:
    def test_the_schema_carries_urls(self):
        assert "urls" in ScrapingJobCreate.model_fields

    def test_the_launcher_accepts_labels_for_a_list(self):
        src = inspect.getsource(sr._launch_job)
        assert "if job_config.urls:" in src
        assert 'elif job_config.city not in CITIES:' in src
        assert "if not job_config.urls and job_config.category not in CATEGORIES:" in src

    def test_the_list_is_cleaned_and_capped(self):
        src = inspect.getsource(sr._launch_job)
        assert '"divar.ir/v/" in u' in src and "[:500]" in src

    def test_single_scrape_is_a_job_of_one(self):
        src = inspect.getsource(sr.scrape_single_property)
        assert "_launch_job(cfg, db, current_user)" in src
        assert "urls=[url]" in src
        assert "DivarScraper(" not in src, "no browser inside the request any more"

    def test_rescrape_is_a_job_of_many(self):
        assert "/rescrape" in [r.path for r in sr.router.routes]
        src = inspect.getsource(sr.rescrape_listings)
        assert "_launch_job(cfg, db, current_user)" in src

    def test_the_jobs_list_labels_a_list_run(self):
        src = inspect.getsource(sr)
        assert '(j.config or {}).get("category") if (j.config or {}).get("urls")' in src

    def test_the_urls_reach_the_background_task(self):
        src = inspect.getsource(sr.run_scraping_job)
        assert "urls=urls," in src


class TestThePanel:
    def test_single_scrape_reports_a_job_not_a_success(self):
        i = APP_JS.index("async function executeSingleScraping(")
        block = APP_JS[i:i + 1500]
        assert "result.job_id" in block and "loadJobs()" in block
        assert "ملک با موفقیت اسکرپ شد" not in block

    def test_the_bulk_button_exists(self):
        assert "rescrapeAllSkipped()" in APP_JS
        assert "بازاسکرپ همه" in APP_JS

    def test_it_sends_what_is_shown(self):
        i = APP_JS.index("async function rescrapeAllSkipped()")
        block = APP_JS[i:i + 1800]
        assert "visibleSkipped()" in block and "'/scraper/rescrape'" in block

    def test_the_button_counts_exactly_what_is_on_screen(self):
        """«همه» over eight rows says (8): the number is the list, filter or
        not. Chat-only rows are not quietly dropped — that verdict has been
        wrong before."""
        i = APP_JS.index("function rescrapeCandidates()")
        body = APP_JS[i:APP_JS.index("}", i)]
        assert "return visibleSkipped();" in body
        assert "chat_only" not in body

    def test_the_button_s_number_and_the_action_read_the_same_set(self):
        """«(8)» over a list of four was the two being computed separately."""
        assert "بازاسکرپ همه (${rescrapeCandidates().length})" in APP_JS
        i = APP_JS.index("async function rescrapeAllSkipped()")
        assert "rescrapeCandidates().map(r => r.url)" in APP_JS[i:i + 400]
        assert "_skippedRows.length" not in APP_JS[APP_JS.index("بازاسکرپ همه ("):][:200]

    def test_an_empty_set_disables_the_button(self):
        i = APP_JS.index("بازاسکرپ همه (")
        assert "rescrapeCandidates().length ? '' : 'disabled'" in APP_JS[i - 400:i]

    def test_it_asks_first_and_says_the_cost(self):
        i = APP_JS.index("async function rescrapeAllSkipped()")
        block = APP_JS[i:i + 1800]
        assert "askConfirm(" in block and "افشا خرج می‌شود" in block
