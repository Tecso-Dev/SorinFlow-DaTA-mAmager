"""
Keeping what the SMS code bought.

Divar issues the code to establish trust in a session. Once it is answered,
that trust lives in the cookies the browser is holding — and nothing saved
them. The stored jar stayed at its pre-verification state, so the next use of
the account restored an untrusted session and was challenged again. Five
accounts had managed nought to four reveals between them, and the run log
shows the loop plainly: rotate, thirty seconds, code demanded, rotate.

This is the same property Crawlee gets from persistCookiesPerSession, and the
reason its docs say preserving session continuity is what removes the need to
re-authenticate.
"""
import inspect
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_vs.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")


@pytest.fixture
def otp_src():
    from app.scraper.contact_extractor import ContactExtractor
    return inspect.getsource(ContactExtractor._handle_sms_otp_if_present)


class TestTheVerifiedSessionIsKept:
    def test_the_extractor_accepts_an_on_verified_callback(self):
        from app.scraper.contact_extractor import ContactExtractor
        assert "on_verified" in inspect.signature(ContactExtractor.__init__).parameters

    def test_it_fires_after_the_code_is_submitted(self, otp_src):
        """Before submission there is nothing new to save."""
        assert otp_src.index("SMS-OTP handled") < otp_src.index("on_verified()")

    def test_it_fires_on_the_success_path_not_the_timeout_path(self, otp_src):
        """A prompt nobody answered granted no trust, so there is nothing to
        persist and firing there would just rewrite the same jar."""
        i = otp_src.index("on_verified()")
        assert otp_src.index("pop_code") < i

    def test_a_failure_to_persist_does_not_lose_the_phone_number(self, otp_src):
        """The reveal is what the run is for; bookkeeping must not cost it."""
        i = otp_src.index("on_verified()")
        assert "except Exception" in otp_src[max(0, i - 200):i + 300]

    def test_the_scraper_wires_it_to_the_session_writer(self):
        from app.scraper.divar_scraper import DivarScraper
        src = inspect.getsource(DivarScraper.scrape_property_detail)
        assert "on_verified=self._persist_active_session" in src

    def test_the_writer_it_is_wired_to_saves_both_copies(self):
        """A jar saved to the file but not the database is restored from the
        database next time, which is where the stale one came back from."""
        from app.scraper.divar_scraper import DivarScraper
        src = inspect.getsource(DivarScraper._persist_active_session)
        assert "save_cookies_to_file" in src
        assert "save_cookies_to_db" in src


class TestTheOtherCallbacksStillDoTheirJob:
    """on_verified sits beside three existing hooks; adding it must not
    disturb the sequence they encode."""

    def test_the_challenge_hook_still_fires_first(self, otp_src):
        """Rotation is decided the moment Divar asks, before anyone waits."""
        assert otp_src.index("on_challenge") < otp_src.index("on_verified")

    def test_pause_and_resume_still_bracket_the_wait(self, otp_src):
        assert otp_src.index("on_pause") < otp_src.index("on_resume")

    def test_all_four_hooks_are_optional(self):
        from app.scraper.contact_extractor import ContactExtractor
        params = inspect.signature(ContactExtractor.__init__).parameters
        for hook in ("on_pause", "on_resume", "on_challenge", "on_verified"):
            assert params[hook].default is None, f"{hook} is not optional"

    def test_the_extractor_still_builds_without_any_of_them(self):
        from pathlib import Path
        from app.scraper.contact_extractor import ContactExtractor
        ex = ContactExtractor(page=None, images_dir=Path("/tmp"))
        assert ex.on_verified is None
