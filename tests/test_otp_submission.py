"""
Finishing a Divar login after the code is typed.

Reported as «اررور داد احراز هویت نکرد»: the code arrives, gets entered, and
the panel answers "Login failed. No authentication tokens found." That message
was reachable in two ways that have nothing to do with a bad code.

The submit control was looked up by text, and when that failed the code fell
back to *any* <button> on the page — on Divar's login modal the first one is
the close control, so the login was dismissed rather than submitted. And the
outcome was read after a flat 5s sleep that only ran when a button had been
found: on the auto-submit path there was no wait at all, and a Divar round
trip slower than 5s was reported as failure for a code that had just worked.
"""
import inspect
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_otp.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")


@pytest.fixture
def submit_src():
    from app.scraper.auth import DivarAuth
    return inspect.getsource(DivarAuth.submit_otp_code)


class TestItNeverClicksAnArbitraryButton:
    def test_no_bare_button_fallback(self, submit_src):
        """query_selector('button') with no qualifier is the close control."""
        assert not re.search(r"query_selector\(\s*['\"]button['\"]\s*\)", submit_src), (
            "falling back to the first button on the page dismisses the login modal"
        )

    def test_it_still_looks_for_a_real_submit_control(self, submit_src):
        assert 'button[type="submit"]' in submit_src
        assert "ورود" in submit_src

    def test_having_no_button_is_a_supported_path(self, submit_src):
        """Divar auto-submits on the last digit; no button is normal."""
        assert "auto-submit" in submit_src


class TestItWaitsForTheRealOutcome:
    def test_the_flat_sleep_is_gone(self, submit_src):
        assert "asyncio.sleep(5)" not in submit_src

    def test_it_polls_for_the_outcome(self, submit_src):
        assert "_await_login_outcome" in submit_src

    def test_the_wait_is_outside_the_button_branch(self, submit_src):
        """It has to run on the auto-submit path too, which had no wait."""
        i = submit_src.index("_await_login_outcome")
        line = submit_src[submit_src.rindex("\n", 0, i) + 1:i]
        indent = len(line) - len(line.lstrip())
        assert indent <= 12, "the wait is nested inside `if login_button:`"


class TestItReportsWhyRatherThanJustThat:
    def test_a_rejection_is_surfaced_to_the_caller(self, submit_src):
        assert "divar_error" in submit_src

    def test_rejection_phrases_are_divars_own_words(self):
        from app.scraper.auth import DivarAuth
        assert any("کد" in p for p in DivarAuth._OTP_REJECTIONS)
        assert any("منقضی" in p for p in DivarAuth._OTP_REJECTIONS)

    def test_a_rejection_stops_the_wait_early(self):
        from app.scraper.auth import DivarAuth
        src = inspect.getsource(DivarAuth._await_login_outcome)
        assert src.index("rejection") < src.index("deadline\n") if "deadline\n" in src \
            else "rejection" in src

    def test_the_poller_has_a_bounded_timeout(self):
        from app.scraper.auth import DivarAuth
        sig = inspect.signature(DivarAuth._await_login_outcome)
        assert sig.parameters["timeout"].default > 0


class TestTheHelpersAreImportable:
    """A Tuple/time annotation without its import is a NameError at import."""

    def test_module_imports(self):
        import app.scraper.auth as m
        assert hasattr(m.DivarAuth, "_await_login_outcome")
        assert hasattr(m.DivarAuth, "_divar_rejection_text")

    def test_time_is_available_to_the_poller(self):
        import app.scraper.auth as m
        assert hasattr(m, "time")
