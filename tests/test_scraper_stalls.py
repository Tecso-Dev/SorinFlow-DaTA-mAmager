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

    async def test_the_store_accepts_a_bare_job_id(self):
        from app.scraper import otp_store
        assert await otp_store.is_cancelled("some-job-id") is False
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
    """A run's process can go away mid-scrape — a deploy, a crash. The row
    it was updating must not say «running» forever. This used to happen once
    at boot, which was only right while one process ran everything; now the
    worker's sweep does it every minute, for runs whose claim is gone. The
    rows themselves are tested on Postgres in test_scrape_queue.py."""

    def test_the_worker_releases_them(self):
        from app.services import scrape_queue as sq
        assert inspect.iscoroutinefunction(sq.release_orphans)
        assert "release_orphans(orphans)" in inspect.getsource(sq.sweep)

    def test_it_runs_as_soon_as_the_worker_starts(self):
        """The sweep's first pass is its first statement after the beat, not
        after a minute's sleep."""
        from app.services import scrape_queue as sq
        src = inspect.getsource(sq.sweep_loop)
        assert src.index("await sweep()") < src.index("await asyncio.sleep(SWEEP_EVERY)")

    def test_it_covers_paused_too(self):
        """A job paused waiting for a code is just as dead."""
        from app.services import scrape_queue as sq
        src = inspect.getsource(sq.release_orphans)
        assert '"paused"' in src and '"running"' in src

    def test_it_explains_itself_rather_than_vanishing(self):
        from app.services import scrape_queue as sq
        assert "finish_reason=ORPHAN_REASON" in inspect.getsource(sq.release_orphans)

    async def test_a_failing_sweep_cannot_stop_the_worker(self, monkeypatch):
        """A cosmetic row is not worth a worker that stops sweeping."""
        import asyncio
        from app.services import scrape_queue as sq
        calls = []

        async def _broken():
            calls.append(1)
            raise RuntimeError("database is down")
        monkeypatch.setattr(sq, "sweep", _broken)
        monkeypatch.setattr(sq, "SWEEP_EVERY", 0.01)
        task = asyncio.create_task(sq.sweep_loop())
        await asyncio.sleep(0.1)
        assert not task.done() and len(calls) > 1
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    def test_it_is_a_single_bulk_update(self):
        """Row by row is how a sweep of many turns into a long one."""
        from app.services import scrape_queue as sq
        src = inspect.getsource(sq.release_orphans)
        assert "update(ScrapingJob)" in src
        assert "for " not in src[src.index("async with"):src.index("await db.commit()")]


class TestTheStartupHookIsWiredCorrectly:
    """A helper was once inserted between @asynccontextmanager and lifespan,
    so the decorator landed on it instead: awaiting it raised TypeError, and
    the app's lifespan lost its decorator entirely. The counts in a test run
    happened to match the usual baseline, which is why comparing numbers
    rather than reasons missed it."""

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

    def test_the_helpers_above_it_carry_no_decorator(self):
        import ast
        import app.main as m
        tree = ast.parse(open(m.__file__, encoding="utf-8-sig").read())
        helpers = {node.name: node for node in tree.body
                   if getattr(node, "name", None) in ("_loops", "_start_background")}
        assert set(helpers) == {"_loops", "_start_background"}
        for name, node in helpers.items():
            assert node.decorator_list == [], (
                f"a decorator on {name} means it was inserted above the wrong def")
