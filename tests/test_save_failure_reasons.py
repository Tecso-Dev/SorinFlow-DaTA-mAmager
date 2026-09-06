"""
«ذخیره نشد» is the observation, not the cause.

Six of one run's listings came back under that, with nothing else — and they
were not thin records: the panel showed full titles with deposits and rents,
«اجاره آپارتمان واقع در امین ودیعه: ۵۰,۰۰۰,۰۰۰ تومان …». So the scrape
worked and the write did not, and which write problem it was — a missing
field, a constraint, a connection — is the whole question.

save_property already knows. It just was not saying.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_svf.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRAPER = open(os.path.join(ROOT, "app/scraper/divar_scraper.py"),
               encoding="utf-8-sig").read()

from app.scraper.divar_scraper import DivarScraper  # noqa: E402

SAVE = inspect.getsource(DivarScraper.save_property)


class TestEveryWayASaveGivesUpIsNamed:
    def test_a_missing_id(self):
        assert 'self._last_save_error = "شناسهٔ آگهی نبود"' in SAVE

    def test_a_missing_title(self):
        assert 'self._last_save_error = "عنوان نبود"' in SAVE

    def test_a_missing_url(self):
        assert 'self._last_save_error = "آدرس نبود"' in SAVE

    def test_a_database_error_carries_its_type(self):
        assert "self._last_save_error = type(e).__name__" in SAVE

    def test_every_giving_up_path_sets_one(self):
        """Four reasons plus the clear. A path added without one would report
        as a bare «ذخیره نشد» again, which is where this started."""
        assert SAVE.count("self._last_save_error") == 5

    def test_it_is_cleared_at_the_top(self):
        """Otherwise the previous listing's reason is pinned on this one."""
        assert SAVE.index("self._last_save_error = None") < SAVE.index("try:")


class TestTheReasonReachesTheRun:
    def _branch(self):
        i = SCRAPER.index("_save_why = getattr(self")
        return SCRAPER[i:SCRAPER.index("elif detail is None:", i)]

    def test_the_run_reads_it(self):
        assert '_save_why = getattr(self, "_last_save_error", None)' in SCRAPER

    def test_it_is_appended_to_the_observation_not_instead_of_it(self):
        """«ذخیره نشد — IntegrityError» reads as one fact; the type alone
        would lose which step failed."""
        assert 'f"ذخیره نشد — {_save_why}"' in self._branch()

    def test_a_missing_reason_falls_back_to_the_bare_words(self):
        assert 'else "ذخیره نشد"' in self._branch()

    def test_the_tally_groups_by_the_reason(self):
        assert "fail_tally[_save_why]" in self._branch()

    def test_the_skipped_row_carries_it_too(self):
        """The panel's list is where this will actually be read."""
        assert "detail=_save_why" in self._branch()

    def test_the_row_is_still_filed_under_failed(self):
        assert 'reason="failed"' in self._branch()


class TestTheRollbackStillHappens:
    def test_a_raised_save_rolls_back_before_returning(self):
        """The reason must not be recorded at the cost of leaving the session
        in an aborted transaction."""
        i = SAVE.index("self._last_save_error = type(e).__name__")
        assert "rollback()" in SAVE[i:i + 200]
