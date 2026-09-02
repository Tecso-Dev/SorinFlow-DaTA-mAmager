"""
A job id is a UUID object, and the OTP store assumed a string.

Seen in production as a warning and nothing else, because the caller wrapped
the check in a bare except:

    could not check OTP suppression:
    'asyncpg.pgproto.pgproto.UUID' object has no attribute 'split'

ScrapingJob.job_id is UUID(as_uuid=True), so a caller holding the row hands
over a uuid.UUID. The check it guarded never ran.
"""
import ast
import inspect
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_recon.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")


class TestAUuidJobIdDoesNotCrashTheStore:
    """ScrapingJob.job_id is UUID(as_uuid=True); callers hand over the object."""

    def test_job_of_accepts_a_uuid(self):
        import uuid
        from app.scraper import otp_store
        u = uuid.UUID("118a7ddc-415b-4ef5-b86a-6cc9e0f751e1")
        assert otp_store.job_of(u) == str(u)

    def test_a_uuid_matches_the_key_built_from_it(self):
        """Coercion is only correct if it yields the same prefix the keys use."""
        import uuid
        from app.scraper import otp_store
        u = uuid.UUID("118a7ddc-415b-4ef5-b86a-6cc9e0f751e1")
        assert otp_store.job_of(u) == otp_store.job_of(f"{u}:some-divar-id")

    def test_is_cancelled_accepts_a_uuid(self):
        import uuid
        from app.scraper import otp_store
        assert otp_store.is_cancelled(uuid.uuid4()) is False

    def test_none_and_empty_are_still_safe(self):
        from app.scraper import otp_store
        assert otp_store.job_of(None) == ""
        assert otp_store.job_of("") == ""
