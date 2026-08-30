"""
Recording how much budget an account had spent when Divar challenged it.

COOKIE_ROTATE_EVERY only reduces SMS if it sits *below* Divar's real limit.
Above it, every account is challenged every round and the threshold buys
nothing — Divar sets the SMS rate instead of us. Nothing recorded the one
number needed to tell those two cases apart, so the setting was a guess.
"""
import inspect
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_chal.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")


@pytest.fixture
def rotate_src():
    from app.scraper.divar_scraper import DivarScraper
    return inspect.getsource(DivarScraper.maybe_rotate_account)


class TestTheMetricExists:
    def test_histogram_is_registered(self):
        from app import metrics
        assert hasattr(metrics, "scrape_reveals_at_challenge")

    def test_its_buckets_span_below_and_above_the_default_threshold(self):
        """The default is 100. Buckets have to resolve both a Divar limit well
        under it and the case where 100 is never reached."""
        from app import metrics
        buckets = metrics.scrape_reveals_at_challenge._upper_bounds
        assert min(buckets) <= 10
        assert any(b >= 100 for b in buckets)

    def test_the_panel_snapshot_exposes_it(self):
        from app import metrics
        snap = metrics.snapshot()
        assert "challenge_budget_sum" in snap
        assert "challenge_budget_count" in snap


class TestItIsRecordedOnTheChallengePath:
    def test_recorded_only_when_divar_challenged(self, rotate_src):
        assert "if forced:" in rotate_src

    def test_recorded_before_the_budget_is_overwritten(self, rotate_src):
        """_mark_account_spent sets reveals to `every`; reading after it would
        record the threshold instead of what Divar actually allowed.

        Matched on the call, not the bare name -- the comment above the
        recording mentions _mark_account_spent, and a name search finds that
        first and compares the wrong two positions.
        """
        assert rotate_src.index("scrape_reveals_at_challenge") < \
               rotate_src.index("await self._mark_account_spent(")

    def test_measured_the_same_way_the_threshold_measures(self, rotate_src):
        """Otherwise the histogram is not comparable to COOKIE_ROTATE_EVERY."""
        i = rotate_src.index("scrape_reveals_at_challenge")
        window = rotate_src[max(0, i - 400):i]
        assert "_account_reveals()" in window
        assert "_reveals_since_rotation" in window

    def test_bookkeeping_cannot_break_a_rotation(self, rotate_src):
        i = rotate_src.index("scrape_reveals_at_challenge")
        assert "try:" in rotate_src[max(0, i - 400):i]
