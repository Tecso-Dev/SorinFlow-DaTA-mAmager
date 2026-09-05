"""
«The browser is gone» is not «the session expired».

From a real run:

    12:19:11  [rotate] Divar challenged 0905*****26 after 6 reveals
    12:19:11  ERROR  Failed to get current cookies: browser has been closed
              …thirteen more, one per second…
    12:19:24  0936*****58: access token still unknown — treating the session as expired
    12:19:36  0901*****52: access token still unknown — treating the session as expired
    12:19:49  0905*****52: access token still unknown — treating the session as expired
    12:20:01  0919*****65: access token still unknown — treating the session as expired

Four healthy accounts, each polled twelve times against a browser that had
already closed, each then written off. get_current_cookies() returns [] both
for an empty jar and for a torn-down browser, and the refresh check could not
tell those apart.

It is the same mistake as the bug it was written to fix — reading session
health from the wrong signal — so these tests pin the distinction rather than
the symptom.
"""
import inspect
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_bl.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.scraper.auth import DivarAuth  # noqa: E402


class FakePage:
    def __init__(self, closed=False):
        self._closed = closed

    def is_closed(self):
        return self._closed


class FakeBrowser:
    def __init__(self, connected=True):
        self._connected = connected

    def is_connected(self):
        return self._connected


def auth(page=None, browser=None):
    a = DivarAuth.__new__(DivarAuth)
    a.page = page
    a.browser = browser
    a.context = None
    return a


class TestBrowserAlive:
    def test_a_live_page_and_browser_is_alive(self):
        assert auth(FakePage(), FakeBrowser()).browser_alive() is True

    def test_a_closed_page_is_not(self):
        assert auth(FakePage(closed=True), FakeBrowser()).browser_alive() is False

    def test_a_disconnected_browser_is_not(self):
        assert auth(FakePage(), FakeBrowser(connected=False)).browser_alive() is False

    def test_no_page_at_all_is_not(self):
        assert auth(None, FakeBrowser()).browser_alive() is False

    def test_a_page_with_no_browser_object_is_still_judged_on_the_page(self):
        """initialize_browser sets both, but a caller may hand over only a
        page; refusing outright would break a working path."""
        assert auth(FakePage(), None).browser_alive() is True

    def test_a_raising_page_is_treated_as_dead_not_a_crash(self):
        class Exploding:
            def is_closed(self):
                raise RuntimeError("gone")
        assert auth(Exploding(), FakeBrowser()).browser_alive() is False

    def test_it_is_synchronous(self):
        """It is called inside loops and before awaits; making it async would
        invite a forgotten await that silently reads as truthy."""
        assert not inspect.iscoroutinefunction(DivarAuth.browser_alive)


class TestRestoreDistinguishesTheTwoFailures:
    @pytest.fixture
    def src(self):
        return inspect.getsource(DivarAuth.restore_session)

    def test_it_refuses_before_touching_a_dead_browser(self, src):
        """Every step against a closed page logs an error that reads like a
        session problem."""
        assert src.index("browser_alive()") < src.index("await self.apply_cookies")

    def test_that_refusal_says_it_is_not_an_expiry(self, src):
        i = src.index("browser_alive()")
        assert "not an expired session" in src[i:i + 500]

    def test_the_poll_stops_when_the_browser_dies(self, src):
        i = src.index("for _ in range(12)")
        assert "browser_alive()" in src[i:i + 400]

    def test_a_died_browser_is_reported_as_untested(self, src):
        """«Untested» is the honest word: we never got to ask."""
        assert "untested, not expired" in src

    def test_it_still_reports_a_genuinely_stale_token(self, src):
        """The original check must survive the new one."""
        assert "treating the session as expired" in src


class TestRotationDoesNotWalkThePoolWithNoBrowser:
    @pytest.fixture
    def src(self):
        from app.scraper.divar_scraper import DivarScraper
        return inspect.getsource(DivarScraper.maybe_rotate_account)

    def test_it_checks_before_the_candidate_loop(self, src):
        assert src.index("browser_alive()") < src.index("for offset in range(")

    def test_it_leaves_the_pool_alone(self, src):
        """Nothing is wrong with the accounts; the browser is what went."""
        i = src.index("browser_alive()")
        assert "Leaving the pool untouched" in src[i:i + 500]

    def test_it_clears_the_forced_flag_so_the_next_reveal_can_retry(self, src):
        i = src.index("browser_alive()")
        assert "_force_rotate = False" in src[i:i + 500]


class TestNoneOfThisMarksAnAccountInvalid:
    """A failed restore skips a candidate for one rotation. It must never
    write is_valid=False — the accounts in that log were all fine."""

    def test_the_rotation_path_does_not_invalidate(self):
        from app.scraper.divar_scraper import DivarScraper
        src = inspect.getsource(DivarScraper.maybe_rotate_account)
        assert "is_valid = False" not in src
        assert "is_valid=False" not in src

    def test_restore_does_not_invalidate_either(self):
        src = inspect.getsource(DivarAuth.restore_session)
        assert "is_valid = False" not in src
        assert "is_valid=False" not in src


class TestRotationWithTheRealCollaborators:
    """Driven through the rotation path itself, using the project's own test
    double, rather than by reading the source."""

    def _scraper(self, alive):
        import sys
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from test_account_rotation import FakeAuth, make_scraper
        s = make_scraper(["09110000001", "09110000002", "09110000003"], every=5)
        s.auth = FakeAuth(restorable=True, alive=alive)
        return s

    @pytest.mark.asyncio
    async def test_a_live_browser_still_rotates(self):
        s = self._scraper(alive=True)
        s._force_rotate = True
        assert await s.maybe_rotate_account() is True
        assert s.auth.restored, "a live browser should have been offered an account"

    @pytest.mark.asyncio
    async def test_a_dead_browser_rotates_nothing(self):
        s = self._scraper(alive=False)
        s._force_rotate = True
        assert await s.maybe_rotate_account() is False
        assert s.auth.restored == [], \
            "no account should be tried against a closed browser"

    @pytest.mark.asyncio
    async def test_a_dead_browser_clears_the_forced_flag(self):
        """Otherwise every later reveal re-enters the same dead path."""
        s = self._scraper(alive=False)
        s._force_rotate = True
        await s.maybe_rotate_account()
        assert s._force_rotate is False
