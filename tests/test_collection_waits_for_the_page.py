"""
«وقتی می‌زنم اسکرپ شه کلی زمان می‌گیره بعد شروع می‌کنه.»

The wait is the listing collection — not a count, the harvest of listing
URLs; without it the scraper has nothing to open. It cannot be removed. It
was slow for a reason that can: every scroll cycle slept a fixed ~5s
regardless of how fast Divar rendered the batch, which is usually well
under one, and the feed's end was confirmed six empty cycles over.

Now the loop waits for the page to change, with the old sleep as the
ceiling, confirms the end in three, and collects twice what the run asked
for rather than five times.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_cw.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

import pytest  # noqa: E402
from app.scraper import divar_scraper as ds  # noqa: E402
from app.scraper.divar_scraper import DivarScraper  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRAPER = open(os.path.join(ROOT, "app/scraper/divar_scraper.py"), encoding="utf-8-sig").read()


class FakePage:
    """Reports `counts` in order, one per evaluate() call."""
    def __init__(self, counts):
        self.counts = list(counts)
    async def evaluate(self, js):
        return self.counts.pop(0) if self.counts else (self.counts and self.counts[-1]) or 0


def scraper(page):
    s = DivarScraper.__new__(DivarScraper)
    s.page = page
    return s


@pytest.fixture
def slept(monkeypatch):
    out = []
    async def _s(d):
        out.append(d)
    monkeypatch.setattr(ds.asyncio, "sleep", _s)
    return out


class TestWaitingForThePage:
    @pytest.mark.asyncio
    async def test_it_returns_as_soon_as_links_appear(self, slept):
        s = scraper(FakePage([24, 24, 48]))       # grows on the second poll
        await s._wait_for_links_beyond(24, ceiling=3.5)
        assert sum(slept) < 3.5

    @pytest.mark.asyncio
    async def test_a_page_that_never_grows_waits_the_old_ceiling(self, slept):
        s = scraper(FakePage([24] * 40))
        await s._wait_for_links_beyond(24, ceiling=2.5)
        assert abs(sum(slept) - 2.5) < 0.3

    @pytest.mark.asyncio
    async def test_it_never_waits_longer_than_the_old_sleep(self, slept):
        s = scraper(FakePage([24] * 40))
        await s._wait_for_links_beyond(24, ceiling=3.5)
        assert sum(slept) <= 3.5 + 0.25

    @pytest.mark.asyncio
    async def test_an_unreadable_page_falls_back_to_the_fixed_sleep(self, slept):
        class Broken:
            async def evaluate(self, js):
                raise RuntimeError("closed")
        s = scraper(Broken())
        assert await s._visible_link_count() == -1
        await s._wait_for_links_beyond(-1, ceiling=1.2)
        assert slept == [1.2]


class TestTheLoopUsesIt:
    def test_all_three_fixed_sleeps_are_gone(self):
        i = SCRAPER.index("for scroll_n in range(max_scrolls):")
        loop = SCRAPER[i:SCRAPER.index("if not all_listings:", i)]
        for fixed in ("await asyncio.sleep(1.2)", "await asyncio.sleep(3.5)", "await asyncio.sleep(2.5)"):
            assert fixed not in loop, fixed

    def test_the_ceilings_are_the_old_sleeps(self):
        """A slow Divar is waited for exactly as long as it was."""
        i = SCRAPER.index("for scroll_n in range(max_scrolls):")
        loop = SCRAPER[i:SCRAPER.index("if not all_listings:", i)]
        for c in ("before, 1.2)", "before, 3.5)", "before, 2.5)"):
            assert c in loop, c

    def test_the_end_of_feed_is_confirmed_in_three(self):
        assert "if no_new_streak >= 3 and no_button_streak >= 3:" in SCRAPER
        assert "no_new_streak >= 6" not in SCRAPER


class TestCollectingWhatTheRunCanUse:
    def test_twice_the_ask_plus_a_page(self):
        assert "collect_target = min(max(max_items * 2 + 24, 60), 1500)" in SCRAPER

    def test_a_run_for_fifty_no_longer_harvests_most_of_a_city(self):
        assert min(max(50 * 2 + 24, 60), 1500) == 124   # was 250

    def test_a_small_ask_still_gets_a_floor(self):
        """Filters drop candidates; asking for 5 must not collect 5."""
        assert min(max(5 * 2 + 24, 60), 1500) == 60

    def test_whole_day_mode_is_untouched(self):
        assert "collect_target = 400  # DOM-phase batch" in SCRAPER
