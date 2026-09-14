"""
«زمان استخراج هر آگهی به حالت انسانی بیشتر شبیه باشد … goal = get divar OTP
challenge every 100 item.»

The scraper landed on an ad, waited three hundred milliseconds, and pressed
«اطلاعات تماس». Nobody does that. A reader scrolls through the description,
pauses on it for as long as there is to read, sometimes goes back up to the
photos, and only then asks for the number — and does not do it forty times
in a row without looking up.

And a goal nobody measures is a wish. The run now reports its own ratio at
the end — «۱۴۰ افشا، ۱ چالش» — held against the setting, so the pacing can
be tuned toward the number instead of toward a feeling.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_dwell.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

import pytest  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.scraper.divar_scraper import DivarScraper  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRAPER = open(os.path.join(ROOT, "app/scraper/divar_scraper.py"), encoding="utf-8-sig").read()
DWELL = inspect.getsource(DivarScraper._dwell_like_a_reader)
SPACE = inspect.getsource(DivarScraper._space_out_reveal)


class FakeMouse:
    def __init__(self):
        self.wheels = []
    async def wheel(self, dx, dy):
        self.wheels.append(dy)


class FakePage:
    def __init__(self):
        self.mouse = FakeMouse()


def scraper():
    s = DivarScraper.__new__(DivarScraper)
    s.page = FakePage()
    return s


@pytest.fixture
def slept(monkeypatch):
    out = []
    async def _sleep(d):
        out.append(d)
    monkeypatch.setattr("app.scraper.divar_scraper.asyncio.sleep", _sleep)
    return out


class TestTheDwell:
    @pytest.mark.asyncio
    async def test_a_long_ad_is_read_longer_than_a_short_one(self, slept):
        s = scraper()
        await s._dwell_like_a_reader({"description": "کوتاه"})
        short = sum(slept); slept.clear()
        await s._dwell_like_a_reader({"description": "متن " * 200})
        long_ = sum(slept)
        assert long_ > short

    @pytest.mark.asyncio
    async def test_it_is_bounded_above(self, slept):
        cfg = get_settings()
        s = scraper()
        await s._dwell_like_a_reader({"description": "x" * 100000})
        # bound × the widest jitter, plus the optional look-back
        assert sum(slept) <= cfg.reveal_dwell_max_seconds * 1.4 * 1.4 + 2.5

    @pytest.mark.asyncio
    async def test_it_is_bounded_below(self, slept):
        cfg = get_settings()
        s = scraper()
        await s._dwell_like_a_reader({"description": ""})
        assert sum(slept) >= cfg.reveal_dwell_min_seconds * 0.7 * 0.6 * 0.9

    @pytest.mark.asyncio
    async def test_it_scrolls_the_way_reading_moves(self, slept):
        s = scraper()
        await s._dwell_like_a_reader({"description": "متن " * 50})
        assert len(s.page.mouse.wheels) >= 2
        assert all(dy > 0 for dy in s.page.mouse.wheels[:2])

    @pytest.mark.asyncio
    async def test_no_page_is_a_no_op_not_a_crash(self, slept):
        s = DivarScraper.__new__(DivarScraper)
        await s._dwell_like_a_reader({"description": "x"})
        assert slept == []

    @pytest.mark.asyncio
    async def test_a_page_whose_mouse_throws_still_dwells(self, slept):
        class Broken:
            class mouse:
                @staticmethod
                async def wheel(dx, dy):
                    raise RuntimeError("closed")
        s = DivarScraper.__new__(DivarScraper); s.page = Broken()
        await s._dwell_like_a_reader({"description": "متن " * 50})
        assert sum(slept) > 0

    def test_it_is_jittered(self):
        """Two ads of the same length must not be read in the same time."""
        assert "random.uniform(0.7, 1.4)" in DWELL

    def test_it_happens_before_divar_counts(self):
        i = SCRAPER.index("await self._dwell_like_a_reader(property_data)")
        assert i < SCRAPER.index("await self._charge_reveal()")


class TestTheBreaks:
    @pytest.mark.asyncio
    async def test_a_longer_break_eventually_arrives(self, slept, monkeypatch):
        monkeypatch.setattr(get_settings(), "reveal_min_gap_seconds", 0)
        monkeypatch.setattr(get_settings(), "reveal_break_every", 4)
        monkeypatch.setattr(get_settings(), "reveal_break_seconds", 60)
        s = scraper()
        for _ in range(40):
            await s._space_out_reveal()
        assert any(d >= 15 for d in slept), "no break in forty reveals"

    @pytest.mark.asyncio
    async def test_zero_turns_them_off(self, slept, monkeypatch):
        monkeypatch.setattr(get_settings(), "reveal_min_gap_seconds", 0)
        monkeypatch.setattr(get_settings(), "reveal_break_every", 0)
        s = scraper()
        for _ in range(40):
            await s._space_out_reveal()
        assert slept == []

    def test_the_rhythm_has_no_period(self):
        """Every twelfth reveal exactly would be a signature."""
        assert "random.gauss(every, every / 3)" in SPACE


class TestTheGoalIsMeasured:
    def test_reveals_and_challenges_are_counted_per_run(self):
        assert "self._reveals_this_run = getattr(self, \"_reveals_this_run\", 0) + 1" in SCRAPER
        assert "self._challenges_this_run = getattr(self, \"_challenges_this_run\", 0) + 1" in SCRAPER

    def test_rotation_does_not_reset_them(self):
        """The ratio is the run's, not any one account's."""
        for name in ("_reveals_this_run", "_challenges_this_run"):
            assert SCRAPER.count(f"self.{name} = 0") == 1, name

    def test_the_finish_line_reports_the_ratio(self):
        assert "افشا یک چالش" in SCRAPER
        assert "reveals_per_challenge=" in SCRAPER

    def test_it_is_held_against_the_goal(self):
        assert "challenge_goal_reveals" in SCRAPER
        assert "✓ هدف" in SCRAPER and "✗ هدف" in SCRAPER

    def test_missing_the_goal_is_a_warning(self):
        # anchored on the code, not the comment that quotes the same words
        i = SCRAPER.index('_ratio = (f"هر {_rv // _ch} افشا یک چالش"')
        assert 'level="info" if (not _ch or _rv // _ch >= _goal) else "warning"' in SCRAPER[i:i + 900]

    def test_the_goal_defaults_to_one_hundred(self):
        assert get_settings().challenge_goal_reveals == 100
