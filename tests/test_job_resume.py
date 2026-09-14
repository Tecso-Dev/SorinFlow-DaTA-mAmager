"""
«وقتی به هر دلیلی اسکرپ متوقف یا ناموفق می‌شود، امکان ادامه دادن وجود داشته باشد.»

A restart mid-run — seven of eight jobs on one day — threw away ten minutes
of collection and the code somebody had typed by hand, and left «برای بقیه
دوباره اجرا کنید» in the finish line.

Continuing is a NEW run with the old run's exact settings, linked back to
it. New rather than revived on purpose: the old row's counters and log are
the record of what happened. Nothing has to remember a position — every
listing the earlier run saved with a number is skipped by the same rule that
skips duplicates, so the second run starts at the first listing the first
one did not finish.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_res.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.api.routes import scraper as sr  # noqa: E402
from app.models.scraping_job import ScrapingJob  # noqa: E402
from app.schemas import ScrapingJobResponse  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_JS = open(os.path.join(ROOT, "frontend/js/app.js"), encoding="utf-8").read()
DB = open(os.path.join(ROOT, "app/database.py"), encoding="utf-8").read()
MAIN = open(os.path.join(ROOT, "app/main.py"), encoding="utf-8").read()


class TestTheRunRemembersHowItStarted:
    def test_the_config_column(self):
        assert "config" in ScrapingJob.__table__.c

    def test_the_link_back(self):
        assert "resumed_from" in ScrapingJob.__table__.c

    def test_start_writes_the_full_config(self):
        src = inspect.getsource(sr._launch_job)
        assert "config={**job_config.model_dump()" in src

    def test_and_who_started_it(self):
        """So a resume can be refused to somebody else."""
        src = inspect.getsource(sr._launch_job)
        assert '"owner_user_id": current_user.id if current_user else None' in src

    def test_the_migration_is_registered(self):
        assert "_migrate_job_resume," in DB
        assert "ADD COLUMN IF NOT EXISTS config JSON" in DB


class TestStartAndResumeShareOneLauncher:
    """A resume that skipped a check the start makes would be the side door."""

    def test_start_delegates(self):
        assert "_launch_job(job_config, background_tasks, db, current_user)" in \
            inspect.getsource(sr.start_scraping_job)

    def test_resume_delegates(self):
        src = inspect.getsource(sr.resume_scraping_job)
        assert "_launch_job(config, background_tasks, db, current_user" in src

    def test_the_launcher_still_makes_the_ownership_check(self):
        src = inspect.getsource(sr._launch_job)
        assert "به حساب کاربری شما تعلق ندارد" in src

    def test_and_the_running_jobs_cap(self):
        assert "Too many running jobs" in inspect.getsource(sr._launch_job)


class TestResume:
    def test_it_is_routed(self):
        assert "/jobs/{job_id}/resume" in [r.path for r in sr.router.routes]

    def test_it_links_the_new_run_to_the_old(self):
        src = inspect.getsource(sr.resume_scraping_job)
        assert "resumed_from=job.job_id" in src

    def test_a_running_job_cannot_be_resumed(self):
        src = inspect.getsource(sr.resume_scraping_job)
        assert '("running", "paused", "pending")' in src
        assert "status_code=409" in src

    def test_a_job_from_before_this_existed_says_so(self):
        """Rather than inventing a config."""
        src = inspect.getsource(sr.resume_scraping_job)
        assert "if not job.config:" in src
        assert "تنظیماتش ذخیره نشده" in src

    def test_somebody_elses_run_is_refused(self):
        src = inspect.getsource(sr.resume_scraping_job)
        assert "کاربر دیگری شروع کرده" in src

    def test_an_admin_may_resume_anyones(self):
        src = inspect.getsource(sr.resume_scraping_job)
        assert '("root", "super_admin")' in src

    def test_only_known_fields_are_replayed(self):
        """A key added to the stored config later must not crash an older
        run's resume."""
        src = inspect.getsource(sr.resume_scraping_job)
        assert "if k in ScrapingJobCreate.model_fields" in src

    def test_the_new_run_s_log_says_what_it_continues(self):
        src = inspect.getsource(sr.resume_scraping_job)
        assert "ادامهٔ اسکرپ" in src


class TestTheResponseSaysWhetherItCan:
    def test_the_fields_exist(self):
        assert "can_resume" in ScrapingJobResponse.model_fields
        assert "resumed_from" in ScrapingJobResponse.model_fields

    def test_can_resume_needs_a_config_and_a_stopped_status(self):
        src = inspect.getsource(sr)
        assert 'can_resume=bool(j.config) and j.status in ("failed", "cancelled", "completed")' in src

    def test_the_model_agrees(self):
        assert '"can_resume": bool(self.config) and self.status in' in \
            inspect.getsource(ScrapingJob.to_dict)


class TestThePanel:
    def test_there_is_a_button(self):
        assert "resumeJob('${job.job_id}')" in APP_JS

    def test_it_only_shows_when_the_server_says_it_can(self):
        i = APP_JS.index("resumeJob('${job.job_id}')")
        assert "job.can_resume ?" in APP_JS[i - 200:i]

    def test_it_calls_the_endpoint(self):
        i = APP_JS.index("async function resumeJob(")
        assert "/resume`, { method: 'POST' }" in APP_JS[i:i + 500]

    def test_a_continued_run_is_marked_as_one(self):
        assert "ادامهٔ ${esc(String(job.resumed_from).slice(0, 8))}" in APP_JS


class TestTheRestartMessagePointsAtTheButton:
    def test_it_no_longer_says_run_it_again(self):
        i = MAIN.index("async def _release_orphaned_jobs")
        block = MAIN[i:i + 2000]
        assert "دوباره اجرا کنید" not in block
        assert "«ادامه»" in block
