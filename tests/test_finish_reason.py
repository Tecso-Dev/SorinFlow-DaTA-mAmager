"""
A run that stops short has to say why.

Reported as «چرا رو ۴۲ درصد موند وقتی که باید ۱۲۶ عدد اسکرپ میکرد» — a job
finishing at 42% and labelled «تکمیل شده», with nothing anywhere saying that
the chosen day simply held fewer ads than the cap. Nothing was broken; the
system just could not explain itself, so it looked broken.

The date-mode case was the one with no explanation at all: the existing
warning was gated on `not date_mode`, and date mode is exactly where a cap
routinely exceeds what exists.
"""
import inspect
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_fin.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRAPER = open(os.path.join(ROOT, "app/scraper/divar_scraper.py"),
               encoding="utf-8-sig").read()


class TestItIsStored:
    def test_the_model_has_the_column(self):
        from app.models.scraping_job import ScrapingJob
        assert hasattr(ScrapingJob, "finish_reason")

    def test_the_api_returns_it(self):
        from app.schemas import ScrapingJobResponse
        assert "finish_reason" in ScrapingJobResponse.model_fields

    def test_the_migration_is_registered(self):
        from app import database
        src = inspect.getsource(database.init_db)
        assert "_migrate_job_finish_reason" in src

    def test_the_migration_does_not_backfill(self):
        from app import database
        src = inspect.getsource(database._migrate_job_finish_reason)
        assert "UPDATE" not in src.upper()

    def test_it_is_committed_not_just_assigned(self):
        i = SCRAPER.index("job.finish_reason = finish_reason")
        assert "commit()" in SCRAPER[i:i + 200]


class TestDateModeIsNoLongerSilent:
    """The regression: the explanation used to skip the one case that needed it."""

    def test_the_explanation_is_not_gated_on_not_date_mode(self):
        assert "job.new_items < max_items and not date_mode" not in SCRAPER, (
            "date mode is where a cap most often exceeds what exists"
        )

    def test_date_mode_gets_its_own_message(self):
        i = SCRAPER.index("Day exhausted:")
        assert i > 0
        window = SCRAPER[max(0, i - 300):i]
        assert "if date_mode:" in window

    def test_the_break_reason_is_not_overwritten_by_the_summary(self):
        """The specific reason from the break beats the generic one."""
        i = SCRAPER.index("Day exhausted:")
        assert "if not finish_reason:" in SCRAPER[i:i + 400]


class TestItReadsAsAnExplanationNotAFault:
    def test_a_reason_is_written_in_persian(self):
        i = SCRAPER.index("Day exhausted:")
        window = SCRAPER[i:i + 600]
        assert re.search(r"[؀-ۿ]", window), "the panel shows this to the user"

    def test_it_does_not_use_the_error_field(self):
        """error_message renders as a failure; this is a healthy job."""
        i = SCRAPER.index("Day exhausted:")
        assert "error_message" not in SCRAPER[i:i + 600]

    def test_a_run_that_hit_its_target_explains_nothing(self):
        """No message is the right message when nothing went short."""
        i = SCRAPER.index("finish_reason: Optional[str] = None")
        assert i > 0


class TestThePanelShowsIt:
    def test_the_job_row_renders_the_reason(self):
        js = open(os.path.join(ROOT, "frontend/js/app.js"), encoding="utf-8").read()
        assert "job.finish_reason" in js

    def test_it_is_escaped(self):
        js = open(os.path.join(ROOT, "frontend/js/app.js"), encoding="utf-8").read()
        i = js.index("job.finish_reason")
        assert "esc(job.finish_reason)" in js[i:i + 400]


class TestTheStreakEarlyStopIsGone:
    """Date mode used to stop after 15 consecutive listings older than the
    target day. Divar interleaves promoted and pinned posts, which are
    routinely older, so fifteen in a row said nothing about how much of the
    day remained — and it could only ever end a run early, never complete
    one. A job capped at 126 finished at 42% because of it."""

    def test_the_break_is_removed(self):
        assert "day exhausted, stopping" not in SCRAPER
        assert "older_streak" not in SCRAPER

    def test_the_publish_date_filter_still_drops_older_listings(self):
        """Removing the early stop must not stop the filtering."""
        assert "is before" in SCRAPER
        i = SCRAPER.index("is before")
        assert "_skip" in SCRAPER[max(0, i - 200):i]

    def test_collection_is_bounded_by_date_not_by_count(self):
        """What makes removing the break safe: the pool is already the day."""
        assert "until_day" in SCRAPER
        i = SCRAPER.index("day fully covered")
        assert "cursor_dt.date() < until_day" in SCRAPER[max(0, i - 300):i]

    def test_there_is_still_a_safety_cap_on_the_pool(self):
        """Unbounded pagination on a busy day is the failure this must not
        trade for the one it fixes."""
        i = SCRAPER.index("date-mode safety cap")
        assert ">= 1500" in SCRAPER[max(0, i - 200):i]

    def test_a_short_date_run_is_still_explained(self):
        """The reason used to be set at the break; it must survive its removal."""
        assert "Day exhausted:" in SCRAPER
        i = SCRAPER.index("Day exhausted:")
        assert "finish_reason" in SCRAPER[i:i + 600]
