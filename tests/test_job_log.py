"""
گزارش اسکرپ — the per-run event log.

The application log cannot answer «این ران چرا نصفه ماند؟». No line in
scraper.log carries a job id, so two runs in a day interleave with no way to
tell them apart, and the file rotates at 10 MB with seven days of retention —
the run somebody wants to understand is usually the one that has aged out.

The `scraping_logs` table has existed since the first migration, with exactly
the right shape, and had never been written to.

The property that matters most here is not what gets recorded. It is that
recording cannot hurt the run: the scraper commits job progress on its own
session while a run is in flight, and a failed INSERT inside that transaction
would put Postgres into an aborted state and take the scrape down with it —
turning a logging problem into a data-loss one.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_joblog.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")


class TestRecordingCannotBreakTheRun:

    @pytest.mark.asyncio
    async def test_a_database_failure_is_swallowed(self, monkeypatch):
        """A scrape must not die because its diary was full."""
        from app.services import job_log

        class _Boom:
            def __call__(self, *a, **kw): return self
            async def __aenter__(self): raise RuntimeError("no connection")
            async def __aexit__(self, *a): return False
        monkeypatch.setattr(job_log, "async_session_maker", _Boom())

        assert await job_log.record("some-uuid", job_log.START, "x") is False

    @pytest.mark.asyncio
    async def test_a_missing_job_id_is_a_no_op(self):
        from app.services import job_log
        assert await job_log.record(None, job_log.START, "x") is False

    def test_it_uses_its_own_session_not_the_callers(self):
        """The caller's transaction is mid-scrape. An aborted transaction there
        loses the run's progress, which is far worse than losing a log line."""
        import inspect
        from app.services import job_log

        src = inspect.getsource(job_log.record)
        assert "async_session_maker()" in src
        assert "db.commit()" in src
        # and it must not accept a session from outside
        assert "db" not in inspect.signature(job_log.record).parameters

    @pytest.mark.asyncio
    async def test_a_prune_failure_is_swallowed(self, monkeypatch):
        from app.services import job_log

        class _Boom:
            def __call__(self, *a, **kw): return self
            async def __aenter__(self): raise RuntimeError("nope")
            async def __aexit__(self, *a): return False
        monkeypatch.setattr(job_log, "async_session_maker", _Boom())
        assert await job_log.prune() == 0


class TestTheScraperActuallyReports:
    """Instrumentation that exists but is never called explains nothing."""

    @pytest.mark.parametrize("stage", ["START", "SESSION", "PAUSE", "RESUME",
                                       "FINISH", "ERROR"])
    def test_the_stage_vocabulary_is_defined(self, stage):
        from app.services import job_log
        assert isinstance(getattr(job_log, stage), str)

    def test_the_run_reports_start_finish_and_failure(self):
        import inspect
        from app.scraper.divar_scraper import DivarScraper

        src = inspect.getsource(DivarScraper)
        for stage in ("job_log.START", "job_log.FINISH", "job_log.ERROR"):
            assert stage in src, f"the scraper never records {stage}"

    def test_the_otp_pause_is_recorded(self):
        """A run parked on an OTP looks identical to a hung one from outside."""
        import inspect
        from app.scraper.divar_scraper import DivarScraper
        src = inspect.getsource(DivarScraper)
        assert "job_log.PAUSE" in src and "job_log.RESUME" in src

    def test_a_missing_divar_session_is_recorded(self):
        """This is the silent one: the run 'succeeds' but every listing comes
        back without a phone number, and nothing said why."""
        import inspect
        from app.scraper.divar_scraper import DivarScraper
        src = inspect.getsource(DivarScraper)
        assert "job_log.SESSION" in src

    def test_events_are_pruned_so_the_table_cannot_grow_without_bound(self):
        import inspect
        from app.scraper.divar_scraper import DivarScraper
        assert "job_log.prune()" in inspect.getsource(DivarScraper)


class TestTheEndpoint:

    def test_it_is_registered(self):
        from app.main import app
        paths = {getattr(r, "path", "") for r in app.routes}
        assert "/api/scraper/jobs/{job_id}/events" in paths

    def test_it_accepts_both_id_forms(self):
        """The panel holds job_id UUIDs; the jobs table also has integer ids."""
        import inspect
        from app.api.routes import scraper
        src = inspect.getsource(scraper.get_job_events)
        assert "uuid.UUID(job_id)" in src and "ScrapingJob.id" in src

    def test_events_come_back_oldest_first(self):
        """A timeline reads downwards."""
        import inspect
        from app.services import job_log
        assert "created_at.asc()" in inspect.getsource(job_log.events_for)

    def test_the_stage_is_lifted_out_of_details(self):
        import inspect
        from app.api.routes import scraper
        src = inspect.getsource(scraper.get_job_events)
        assert '"stage"' in src and 'k != "stage"' in src


class TestTheViewerIsWired:

    def test_the_button_and_the_modal_exist(self):
        from pathlib import Path
        js = Path("frontend/js/app.js").read_text(encoding="utf-8")
        html = Path("frontend/index.html").read_text(encoding="utf-8")
        assert "function showJobLog" in js
        assert "showJobLog(" in js
        assert 'id="jobLogModal"' in html
        assert 'id="joblog-body"' in html

    def test_every_stage_the_scraper_emits_has_a_label(self):
        """An unlabelled stage renders as «—», which is worse than no timeline."""
        import re
        from pathlib import Path
        from app.services import job_log

        js = Path("frontend/js/app.js").read_text(encoding="utf-8")
        block = js[js.index("const JOB_STAGE_FA"):]
        block = block[:block.index("};")]
        labelled = set(re.findall(r"^\s*(\w+):", block, re.M))

        import inspect
        src = inspect.getsource(
            __import__("app.scraper.divar_scraper", fromlist=["x"]).DivarScraper)
        emitted = {getattr(job_log, n) for n in
                   ("START", "SESSION", "PAUSE", "RESUME", "FINISH", "ERROR")
                   if f"job_log.{n}" in src}
        assert emitted <= labelled, f"unlabelled stages: {sorted(emitted - labelled)}"

    def test_messages_are_escaped(self):
        """Event text includes Divar's own error strings."""
        from pathlib import Path
        js = Path("frontend/js/app.js").read_text(encoding="utf-8")
        fn = js.split("async function showJobLog")[1].split("\n}\n")[0]
        assert "esc(e.message" in fn
