"""
Finishing a Divar login after the code is typed.

Reported as «اررور داد احراز هویت نکرد»: the code arrives, gets entered, and
the panel answers "Login failed. No authentication tokens found." That message
was reachable without the code being wrong at all: the submit control was
looked up by text, and when that failed the code fell back to *any* <button>
on the page — on Divar's login modal the first one is the close control, so
the login was dismissed rather than submitted.
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
