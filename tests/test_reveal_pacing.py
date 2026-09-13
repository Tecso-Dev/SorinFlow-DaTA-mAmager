"""
Pace the one action Divar counts.

«update humanaize secrion to less get orp challenge from divar.»

_human_like_delay spaces out LISTINGS, and that is not what Divar is
counting. pre_contact_skip drops most listings before the contact button is
ever clicked, so a tightly filtered run opens a hundred ads, reveals eight,
and fires those eight back to back — slow from the outside, and a burst on
the only axis that matters.

And a challenge is Divar saying «slow down» in the only words it has.
Rotating to a fresh number and carrying on at the same speed is how one
challenge becomes five; the pace is ours, not the account's.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_pace.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

import pytest  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.scraper.divar_scraper import DivarScraper  # noqa: E402

SPACE = inspect.getsource(DivarScraper._space_out_reveal)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRAPER = open(os.path.join(ROOT, "app/scraper/divar_scraper.py"),
               encoding="utf-8-sig").read()


def scraper():
    s = DivarScraper.__new__(DivarScraper)
    return s


class TestTheSettings:
    def test_the_gap_has_a_default_and_an_env_var(self):
        s = get_settings()
        assert s.reveal_min_gap_seconds > 0

    def test_the_cooldown_has_one_too(self):
        assert get_settings().challenge_cooldown_seconds > 0

    def test_the_cooldown_is_longer_than_the_gap(self):
        """A challenge should cost more than an ordinary reveal, or it says
        nothing."""
        s = get_settings()
        assert s.challenge_cooldown_seconds > s.reveal_min_gap_seconds


class TestItIsCalledWhereDivarCounts:
    def test_before_the_reveal_is_charged(self):
        i = SCRAPER.index("await self._space_out_reveal()")
        j = SCRAPER.index("await self._charge_reveal()")
        assert i < j

    def test_before_the_contact_extractor_is_built(self):
        """Pacing after the click would be pacing nothing."""
        i = SCRAPER.index("await self._space_out_reveal()")
        assert i < SCRAPER.index("contact_extractor = ContactExtractor(")

    def test_the_listing_pacing_is_untouched(self):
        """It solves a different problem and still does."""
        assert "_human_like_delay" in SCRAPER


class TestTheGap:
    @pytest.mark.asyncio
    async def test_the_first_reveal_of_a_run_does_not_wait(self, monkeypatch):
        slept = []
        monkeypatch.setattr("app.scraper.divar_scraper.asyncio.sleep",
                            lambda d: slept.append(d) or _done())
        s = scraper()
        await s._space_out_reveal()
        assert slept == []

    @pytest.mark.asyncio
    async def test_a_reveal_straight_after_another_waits(self, monkeypatch):
        slept = []
        monkeypatch.setattr("app.scraper.divar_scraper.asyncio.sleep",
                            lambda d: slept.append(d) or _done())
        s = scraper()
        await s._space_out_reveal()
        await s._space_out_reveal()
        assert slept and slept[0] > 0

    @pytest.mark.asyncio
    async def test_zero_turns_it_off(self, monkeypatch):
        slept = []
        monkeypatch.setattr("app.scraper.divar_scraper.asyncio.sleep",
                            lambda d: slept.append(d) or _done())
        monkeypatch.setattr(get_settings(), "reveal_min_gap_seconds", 0)
        s = scraper()
        await s._space_out_reveal()
        await s._space_out_reveal()
        assert slept == []

    def test_the_wait_is_jittered(self):
        """A reveal exactly every twelve seconds is a signature of its own."""
        assert "random.uniform(0, gap" in SPACE


class TestTheChallengeCooldown:
    def test_a_challenge_sets_a_hold(self):
        src = inspect.getsource(DivarScraper._note_account_challenged)
        assert "_reveal_hold_until" in src

    def test_the_hold_is_waited_out_before_the_gap(self):
        assert SPACE.index("_reveal_hold_until") < SPACE.index("reveal_min_gap_seconds")

    def test_it_still_forces_a_rotation(self):
        """The cooldown is in addition to changing account, not instead."""
        src = inspect.getsource(DivarScraper._note_account_challenged)
        assert "self._force_rotate = True" in src

    def test_the_hold_survives_the_rotation(self):
        """The pace is ours, not the account's — a fresh number revealed at
        the same speed is just a fresh number spent."""
        assert "getattr(self, \"_reveal_hold_until\", 0.0)" in SPACE


class TestItSurvivesAScraperBuiltWithoutInit:
    """The rotation tests construct DivarScraper with __new__."""

    @pytest.mark.asyncio
    async def test_no_attribute_is_assumed(self, monkeypatch):
        monkeypatch.setattr("app.scraper.divar_scraper.asyncio.sleep",
                            lambda d: _done())
        await scraper()._space_out_reveal()


def _done():
    async def _noop():
        return None
    return _noop()
