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

    def __init__(self, restorable=True, alive=True):
        self.restorable = restorable
        self.restored = []
        # Rotation now asks whether there is still a browser to rotate onto.
        # Default True so every existing case behaves exactly as before; the
        # False path is its own test, because a closed browser used to be
        # reported as four expired sessions.
        self.alive = alive

    def browser_alive(self):
        return self.alive

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


def test_a_challenge_rotates_even_when_the_server_threshold_is_disabled(monkeypatch):
    """A SERVER-WIDE every <= 0 means 'do not rotate on a schedule'. It cannot
    mean 'ignore Divar telling us the account is finished'.

    A PER-JOB 0 is different — see TestAnExplicitZeroPinsTheAccount. The
    operator who sets it has a reason (today's: one phone in hand), and a
    challenge then pauses on the same account rather than hopping to one that
    cannot be unlocked."""
    from app.scraper import divar_scraper as _ds
    s = make_scraper(["0911", "0922"], every=None)
    monkeypatch.setattr(_ds.settings, "cookie_rotate_every", 0)
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


class TestAHeavyChallengedAccountRests:
    """From run 104, the first on rotation that worked:

        challenged 0914*****65 after   1 reveals    cold — verified once, then clean
        challenged 0914*****08 after 225 reveals    burned — challenged on every entry

    Six accounts in between rotated through without a prompt. «Spent» was
    reveals = max(reveals, 10): a no-op at 225. And _rest_all_accounts zeroed
    everyone including the burned one, so it came back looking freshest."""

    def _scraper(self, rows):
        from app.scraper.divar_scraper import DivarScraper
        s = DivarScraper.__new__(DivarScraper)

        class _Res:
            def __init__(self, rows): self._rows = rows
            def scalars(self): return self
            def all(self): return self._rows

        class _DB:
            async def execute(self, q): return _Res(rows)
        s.db_session = _DB()
        return s

    def _row(self, phone, reveals, challenged_hours_ago=None):
        from datetime import datetime, timedelta, timezone
        class R: pass
        r = R(); r.phone_number = phone; r.reveals = reveals; r.is_valid = True
        r.last_used_at = None
        r.challenged_at = (datetime.now(timezone.utc) - timedelta(hours=challenged_hours_ago)
                           if challenged_hours_ago is not None else None)
        return r

    @pytest.mark.asyncio
    async def test_a_heavy_recently_challenged_account_is_left_out(self):
        s = self._scraper([self._row("0914", 225, challenged_hours_ago=1),
                           self._row("0905", 5), self._row("0912", 114)])
        assert await s._load_rotation_pool() == ["0905", "0912"]

    @pytest.mark.asyncio
    async def test_a_cold_account_that_was_challenged_is_not_rested(self):
        """Its first challenge is a one-time verification, after which it is
        trusted on its device. Resting it would waste a good account."""
        s = self._scraper([self._row("0914", 1, challenged_hours_ago=1), self._row("0905", 5)])
        assert await s._load_rotation_pool() == ["0914", "0905"]

    @pytest.mark.asyncio
    async def test_the_rest_expires(self):
        s = self._scraper([self._row("0914", 225, challenged_hours_ago=30), self._row("0905", 5)])
        assert "0914" in await s._load_rotation_pool()

    @pytest.mark.asyncio
    async def test_a_resting_account_beats_no_account(self):
        s = self._scraper([self._row("0914", 225, challenged_hours_ago=1)])
        assert await s._load_rotation_pool() == ["0914"]

    def test_a_challenge_stamps_the_time(self):
        import inspect
        from app.scraper.divar_scraper import DivarScraper
        src = inspect.getsource(DivarScraper._mark_account_spent)
        assert "row.challenged_at = " in src


class TestAnExplicitZeroPinsTheAccount:
    """One phone in hand means one account. With rotate_every=0 a challenge
    used to answer the code on the account that can be answered and then hop
    to one that cannot, parking the run for six hours."""

    @pytest.mark.asyncio
    async def test_a_challenge_does_not_move_a_pinned_account(self):
        from app.scraper.divar_scraper import DivarScraper
        s = DivarScraper.__new__(DivarScraper)
        s._rotate_every_override = 0
        s._force_rotate = True
        s.active_phone = "0905"
        s._reveals_since_rotation = 0
        moved = await s.maybe_rotate_account()
        assert moved is False
        assert s._force_rotate is False, "the flag must clear or the next reveal re-tries the move"

    def test_the_server_default_still_moves_on_a_challenge(self):
        import inspect
        from app.scraper.divar_scraper import DivarScraper
        src = inspect.getsource(DivarScraper.maybe_rotate_account)
        assert "if override == 0:" in src
        # and the forced path below it is intact
        assert "if not forced:" in src
