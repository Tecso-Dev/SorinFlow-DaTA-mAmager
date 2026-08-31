"""
The two ways a scrape sat at «در حال اجرا» making no progress.

Reported as «اسکرپر گیر کرد و کار نمیکنه» — a job showing 6% after 34
minutes, three listings saved.

**Unanswered code prompts.** Divar challenges a contact reveal, the scraper
waits `otp_wait_timeout` (300s) for someone to type the code, times out, and
then does the same thing on the next listing. The suppression window that
exists for exactly this was only armed when the user *dismissed* a prompt;
a timeout armed nothing. Fifty listings meant four hours of waiting for
codes nobody was there to enter.

**Jobs orphaned by a restart.** A scrape is an asyncio task in the web
process. A deploy, restart or reboot kills it, and the row stays «running»
at whatever percent it reached, forever — with a stop button that stops
nothing. Today's four deploys each created one.
"""
import inspect
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_stall.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")


class TestAnUnansweredPromptStopsTheRestAsking:
    @pytest.fixture
    def otp_src(self):
        from app.scraper.contact_extractor import ContactExtractor
        return inspect.getsource(ContactExtractor._handle_sms_otp_if_present)

    def test_a_timeout_arms_the_suppression_window(self, otp_src):
        i = otp_src.index("SMS-OTP timeout")
        assert "cancel_all" in otp_src[i:i + 1500], (
            "a timeout must suppress like a dismissal, or every later listing "
            "waits the full timeout again"
        )

    def test_it_suppresses_by_job_not_globally(self, otp_src):
        """Three scrapes run at once; one job's silence is not the others'."""
        i = otp_src.index("SMS-OTP timeout")
        assert "job_of" in otp_src[i:i + 1500]

    def test_an_explicit_dismissal_still_works(self, otp_src):
        assert "is_cancelled" in otp_src

    def test_suppression_expires(self):
        """It has to be a window, not a permanent off switch."""
        from app.scraper import otp_store
        assert otp_store._CANCEL_WINDOW > 0

    def test_the_store_accepts_a_bare_job_id(self):
        from app.scraper import otp_store
        assert otp_store.is_cancelled("some-job-id") is False
        assert otp_store.job_of("job-1:divar-abc") == "job-1"


class TestTheRunSaysWhenNumbersAreMissing:
    def test_a_suppressed_run_records_it(self):
        from app.scraper.divar_scraper import DivarScraper
        src = inspect.getsource(DivarScraper.start_scraping_job)
        assert "is_cancelled(job.job_id)" in src

    def test_it_does_not_overwrite_an_existing_reason(self):
        from app.scraper.divar_scraper import DivarScraper
        src = inspect.getsource(DivarScraper.start_scraping_job)
        i = src.index("is_cancelled(job.job_id)")
        assert "if finish_reason else" in src[i:i + 500]


class TestRestartsDoNotLeaveGhostJobs:
    def test_startup_releases_them(self):
        from app import main
        assert hasattr(main, "_release_orphaned_jobs")

    def test_it_runs_at_startup(self):
        from app import main
        src = inspect.getsource(main.lifespan)
        assert "_release_orphaned_jobs" in src

    def test_it_runs_after_init_db(self):
        """The tables have to exist before it can update them."""
        from app import main
        src = inspect.getsource(main.lifespan)
        assert src.index("init_db()") < src.index("_release_orphaned_jobs()")

    def test_it_covers_paused_too(self):
        """A job paused waiting for a code is just as dead after a restart."""
        from app import main
        src = inspect.getsource(main._release_orphaned_jobs)
        assert '"paused"' in src and '"running"' in src

    def test_it_explains_itself_rather_than_vanishing(self):
        from app import main
        src = inspect.getsource(main._release_orphaned_jobs)
        assert "finish_reason" in src

    def test_it_cannot_stop_the_app_booting(self):
        """A cosmetic row is not worth a pod that will not start."""
        from app import main
        src = inspect.getsource(main._release_orphaned_jobs)
        assert "except Exception" in src

    def test_it_is_a_single_bulk_update(self):
        """Row-by-row at boot is how a rollout times out."""
        from app import main
        src = inspect.getsource(main._release_orphaned_jobs)
        assert "update(ScrapingJob)" in src
        assert "for " not in src.split("async with")[1][:400]


class TestTheStartupHookIsWiredCorrectly:
    """It was inserted between @asynccontextmanager and lifespan, so the
    decorator landed on it instead: awaiting it raised TypeError, and the
    app's lifespan lost its decorator entirely. The counts in a test run
    happened to match the usual baseline, which is why comparing numbers
    rather than reasons missed it."""

    def test_the_helper_carries_no_decorator(self):
        import ast
        import app.main as m
        tree = ast.parse(open(m.__file__, encoding="utf-8-sig").read())
        for node in tree.body:
            if getattr(node, "name", None) == "_release_orphaned_jobs":
                assert node.decorator_list == [], (
                    "a decorator here means it was inserted above the wrong def"
                )
                return
        pytest.fail("_release_orphaned_jobs is not a module-level function")

    def test_lifespan_still_has_its_decorator(self):
        import ast
        import app.main as m
        tree = ast.parse(open(m.__file__, encoding="utf-8-sig").read())
        for node in tree.body:
            if getattr(node, "name", None) == "lifespan":
                names = [getattr(d, "id", getattr(d, "attr", "")) for d in node.decorator_list]
                assert "asynccontextmanager" in names
                return
        pytest.fail("lifespan is not a module-level function")

    def test_the_helper_is_actually_awaitable(self):
        """The failure mode was a coroutine that could not be awaited."""
        import inspect
        import app.main as m
        assert inspect.iscoroutinefunction(m._release_orphaned_jobs)
