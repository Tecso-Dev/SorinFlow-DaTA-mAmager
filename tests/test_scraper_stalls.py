"""
An unanswered code prompt made every later listing wait all over again.

Reported as «اسکرپر گیر کرد و کار نمیکنه» — a job showing 6% after 34
minutes, three listings saved.

Divar challenges a contact reveal, the scraper
waits `otp_wait_timeout` (300s) for someone to type the code, times out, and
then does the same thing on the next listing. The suppression window that
exists for exactly this was only armed when the user *dismissed* a prompt;
a timeout armed nothing. Fifty listings meant four hours of waiting for
codes nobody was there to enter.
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
