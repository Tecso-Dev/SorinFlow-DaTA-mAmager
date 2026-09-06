"""
Keep the listings a run did not save.

«۱۴۸ نامزد — ۱۴ تازه، ۹۷ تکراری، ۳ ناموفق، ۳۲ خارج از دسته‌بندی، ۲ ودیعه» can
be checked for arithmetic and nothing else. Whether those 32 were promoted
junk or 32 real apartments is not a question a count can answer, and the
listings themselves were gone by the time anyone asked.

Asked for as: «یه فیلدی تو اسکرپ باز کنی به اسم اسکرپ های ناموفق … لینک دیوار
اینارو بزاری تو اون فیلد که بشه بعدا اسکرپ تکی کرد».
"""
import inspect
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_skip.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.models.scraping_job import SkippedListing  # noqa: E402
from app.services import skipped_listings as sl  # noqa: E402


class TestTheTable:
    def test_it_hangs_off_the_job(self):
        assert "job_id" in SkippedListing.__table__.c
        fks = list(SkippedListing.__table__.c.job_id.foreign_keys)
        assert fks and "scraping_jobs.job_id" in str(fks[0].target_fullname)

    def test_it_keeps_the_link_that_makes_a_retry_possible(self):
        assert "url" in SkippedListing.__table__.c
        assert "divar_id" in SkippedListing.__table__.c

    def test_it_keeps_why(self):
        assert "reason" in SkippedListing.__table__.c
        assert "detail" in SkippedListing.__table__.c

    def test_the_reason_is_indexed(self):
        """The panel filters by it."""
        assert SkippedListing.__table__.c.reason.index is True

    def test_the_job_is_indexed(self):
        assert SkippedListing.__table__.c.job_id.index is True

    def test_it_is_created_by_create_all_like_every_other_table(self):
        from app.database import Base
        assert "skipped_listings" in Base.metadata.tables


class TestRecordingIsSafeForARunInFlight:
    """The rules job_log established, for the same reasons."""

    def test_it_uses_its_own_session(self):
        src = inspect.getsource(sl.record)
        assert "async_session_maker()" in src, \
            "writing on the scraper's session can abort its transaction"

    def test_it_never_raises(self):
        src = inspect.getsource(sl.record)
        assert "except Exception" in src and "return False" in src

    def test_the_reason_is_documented_as_deliberate(self):
        assert "Deliberately swallowed" in inspect.getsource(sl.record)

    @pytest.mark.asyncio
    async def test_no_job_is_a_no_op_not_a_write(self):
        assert await sl.record(None, divar_id="abc") is False

    @pytest.mark.asyncio
    async def test_no_listing_id_is_a_no_op(self):
        assert await sl.record(uuid.uuid4(), divar_id="") is False


class TestTheStoredRow:
    def test_a_url_is_derived_when_none_is_given(self):
        """The token alone is enough to rebuild the link, and a row without
        one cannot be retried — which is the whole point."""
        src = inspect.getsource(sl.record)
        assert 'f"https://divar.ir/v/{divar_id}"' in src

    def test_every_string_is_bounded_to_its_column(self):
        src = inspect.getsource(sl.record)
        for cap in ("[:32]", "[:400]", "[:300]", "[:64]"):
            assert cap in src, cap

    def test_an_empty_title_is_stored_as_null_not_as_empty(self):
        src = inspect.getsource(sl.record)
        assert "(title or None)" in src


class TestReadingThemBack:
    def test_one_job_s_rows_come_in_the_order_they_were_met(self):
        src = inspect.getsource(sl.for_job)
        assert "SkippedListing.id.asc()" in src

    def test_they_can_be_filtered_by_reason(self):
        src = inspect.getsource(sl.for_job)
        assert "if reason:" in src

    def test_a_summary_does_not_need_every_row(self):
        src = inspect.getsource(sl.counts_for_job)
        assert "select(SkippedListing.reason)" in src


class TestItDoesNotGrowForever:
    def test_there_is_a_retention_window(self):
        assert sl.RETENTION_DAYS > 0

    def test_it_matches_the_job_log_s(self):
        """The two halves of one story should not age out at different times."""
        from app.services import job_log
        assert sl.RETENTION_DAYS == job_log.RETENTION_DAYS

    def test_pruning_never_raises_either(self):
        src = inspect.getsource(sl.prune)
        assert "except Exception" in src and "return 0" in src
