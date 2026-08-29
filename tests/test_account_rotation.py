"""
Divar account rotation.

The reported symptom was "it should change number every 100 scraped, but it
sends a code every 20". The setting was not being ignored — it counted listings
processed, while Divar's SMS challenge counts contact-info reveals. Because
pre_contact_skip means most listings never ask for a phone number, the two
diverged further the more filtering was applied.

These tests pin the counted unit, and the two behaviours around it that were
also wrong: a failed rotation used to consume the whole window, and a challenge
from Divar did nothing at all.
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_rot.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")


class FakeAuth:
    """Stands in for DivarAuth. Records which accounts were restored, and can be
    told to fail — the interesting cases are the ones where restoring fails."""

    def __init__(self, restorable=True):
        self.restorable = restorable
        self.restored = []

    async def restore_session(self, phone):
        self.restored.append(phone)
        return self.restorable


def make_scraper(pool, every=100, restorable=True):
    """A DivarScraper with only the rotation collaborators wired up.

    Built with __new__ so none of the browser/filesystem setup in __init__ runs:
    rotation touches the account pool and the auth object and nothing else, and
    a test that needed Playwright to check a counter would not be run.
    """
    from app.scraper.divar_scraper import DivarScraper

    s = DivarScraper.__new__(DivarScraper)
    s._rotation_pool = list(pool)
    s._reveals_since_rotation = 0
    s._force_rotate = False
    s._rotate_every_override = every
    s.active_phone = pool[0] if pool else None
    s.auth = FakeAuth(restorable=restorable)

    async def _load_pool():
        return list(pool)
    s._load_rotation_pool = _load_pool

    async def _persist():
        return None
    s._persist_active_session = _persist

    async def _delay(*a, **k):
        return None
    s._human_like_delay = _delay
    return s


def reveals(scraper, n):
    """Simulate n contact-info reveals, rotating between each as the loop does."""
    switches = 0
    for _ in range(n):
        scraper._reveals_since_rotation += 1        # what scrape_property_detail does
        if asyncio.run(scraper.maybe_rotate_account()):
            switches += 1
    return switches


# ── the reported bug ─────────────────────────────────────────────────────────

def test_rotation_is_measured_in_contact_reveals():
    """100 reveals with a threshold of 100 must rotate exactly once."""
    s = make_scraper(["0911", "0922", "0933"], every=100)
    assert reveals(s, 99) == 0, "rotated before the threshold"
    assert reveals(s, 1) == 1, "did not rotate on the 100th reveal"
    assert s.active_phone == "0922"


def test_listings_that_never_reveal_a_phone_do_not_count():
    """The heart of it. A filtered run opens many listings and reveals few
    phones; only the reveals may move the counter, because only the reveals are
    what Divar counts."""
    s = make_scraper(["0911", "0922"], every=100)

    # 500 listings go by, but pre_contact_skip discards every one, so
    # scrape_property_detail returns before ContactExtractor is built.
    for _ in range(500):
        assert asyncio.run(s.maybe_rotate_account()) is False

    assert s.active_phone == "0911", "rotated on listings that never asked Divar anything"
    assert s._reveals_since_rotation == 0

    # and the account still has its full budget of reveals
    assert reveals(s, 99) == 0
    assert reveals(s, 1) == 1


# ── a challenge is louder than the counter ───────────────────────────────────

def test_a_divar_challenge_rotates_immediately():
    """Being asked for a code is the account saying it is spent. Waiting for the
    counter after that is waiting for a number Divar has already stopped
    trusting."""
    s = make_scraper(["0911", "0922"], every=100)
    reveals(s, 10)
    assert s.active_phone == "0911"

    s._note_account_challenged()                    # what ContactExtractor fires
    assert asyncio.run(s.maybe_rotate_account()) is True
    assert s.active_phone == "0922"
    assert s._force_rotate is False, "challenge flag outlived the rotation"
    assert s._reveals_since_rotation == 0


def test_a_challenge_rotates_even_when_the_threshold_is_disabled():
    """every <= 0 means 'do not rotate on a schedule'. It cannot mean 'ignore
    Divar telling us the account is finished'."""
    s = make_scraper(["0911", "0922"], every=0)
    assert reveals(s, 50) == 0                      # no scheduled rotation
    s._note_account_challenged()
    assert asyncio.run(s.maybe_rotate_account()) is True
    assert s.active_phone == "0922"


# ── a failed rotation must not consume the window ────────────────────────────

def test_failed_rotation_retries_soon_instead_of_waiting_a_full_window():
    """The counter used to be zeroed before the attempt, so when no session
    could be restored the next try was a full threshold away — on the account
    that had just proved it needed replacing."""
    s = make_scraper(["0911", "0922"], every=100, restorable=False)
    assert reveals(s, 100) == 0                     # nothing restorable
    assert s.active_phone == "0911"
    assert s._reveals_since_rotation >= 90, (
        "a failed rotation reset the counter and gave up the whole window")

    # once a session becomes usable again it recovers within a few reveals,
    # not another hundred
    s.auth.restorable = True
    assert reveals(s, 6) == 1


def test_single_account_does_not_re_query_the_pool_forever():
    """With one account there is nothing to rotate to, and that will not change
    by asking again on the very next reveal."""
    s = make_scraper(["0911"], every=10)
    calls = {"n": 0}

    async def _counting_pool():
        calls["n"] += 1
        return ["0911"]
    s._load_rotation_pool = _counting_pool

    reveals(s, 100)
    assert s.active_phone == "0911"
    assert calls["n"] <= 12, f"queried the account pool {calls['n']} times for one account"
