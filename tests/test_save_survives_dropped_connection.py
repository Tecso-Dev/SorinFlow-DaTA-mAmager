"""
Issue #10 — run 92 scraped 144 listings and lost five AT THE SAVE:

    144 نامزد — 22 تازه، 84 تکراری، 5 ناموفق
    (ذخیره نشد — InterfaceError: 3، ذخیره نشد — PendingRollbackError: 2)

Those two errors are one fault seen twice: the connection going away under
an in-flight statement, then the session still holding the transaction that
died. The listing had been fully scraped, a reveal spent on it, and it was
counted «failed» over a socket.

With NullPool the next statement after a rollback opens a fresh connection,
so one retry on a clean session is the whole fix. Only connection-class
errors are retried: a constraint violation would fail identically twice and
is not a socket's fault.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_drop.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

import pytest  # noqa: E402
from sqlalchemy.exc import (DBAPIError, IntegrityError, InterfaceError,  # noqa: E402
                            OperationalError, PendingRollbackError)
from app.scraper import divar_scraper as ds  # noqa: E402
from app.scraper.divar_scraper import DivarScraper  # noqa: E402


class TestWhatCountsAsDropped:
    """The two errors from run 92, and their relatives — not anything else."""

    def test_interface_error(self):
        assert ds._is_dropped_connection(InterfaceError("s", {}, Exception()))

    def test_pending_rollback(self):
        assert ds._is_dropped_connection(PendingRollbackError("dead"))

    def test_operational_error(self):
        """The server closing it: idle_in_transaction timeout, a restart."""
        assert ds._is_dropped_connection(OperationalError("s", {}, Exception()))

    def test_a_dbapi_error_flagged_invalidated(self):
        e = DBAPIError("s", {}, Exception(), connection_invalidated=True)
        assert ds._is_dropped_connection(e)

    def test_asyncpgs_own_name_for_it(self):
        class ConnectionDoesNotExistError(Exception):
            pass
        assert ds._is_dropped_connection(ConnectionDoesNotExistError())

    def test_a_constraint_violation_is_not(self):
        """It would fail identically twice; retrying is not the fix."""
        assert not ds._is_dropped_connection(IntegrityError("s", {}, Exception()))

    def test_a_plain_bug_is_not(self):
        assert not ds._is_dropped_connection(KeyError("title"))


class FakeSession:
    def __init__(self):
        self.rollbacks = 0
    async def rollback(self):
        self.rollbacks += 1


def scraper(attempts):
    """A scraper whose single attempt behaves per the script — an exception
    to raise, or a value to return — one entry per call."""
    s = DivarScraper.__new__(DivarScraper)
    s.db_session = FakeSession()
    calls = []
    async def _attempt(pd):
        calls.append(pd)
        step = attempts[len(calls) - 1]
        if isinstance(step, BaseException):
            if ds._is_dropped_connection(step):
                raise ds._DroppedConnection(step)
            raise step
        return step
    s._save_property_attempt = _attempt
    s.calls = calls
    return s


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    async def _s(d):
        return None
    monkeypatch.setattr(ds.asyncio, "sleep", _s)


class TestTheRetry:
    @pytest.mark.asyncio
    async def test_a_drop_then_success_saves_the_listing(self):
        s = scraper([InterfaceError("s", {}, Exception()), "ROW"])
        assert await s.save_property({"divar_id": "x"}) == "ROW"
        assert len(s.calls) == 2

    @pytest.mark.asyncio
    async def test_the_dead_transaction_is_rolled_back_before_the_retry(self):
        """Without this the retry meets the PendingRollbackError itself."""
        s = scraper([PendingRollbackError("dead"), "ROW"])
        await s.save_property({"divar_id": "x"})
        assert s.db_session.rollbacks >= 1

    @pytest.mark.asyncio
    async def test_a_clean_save_is_not_retried(self):
        s = scraper(["ROW"])
        assert await s.save_property({}) == "ROW"
        assert len(s.calls) == 1

    @pytest.mark.asyncio
    async def test_two_drops_give_up_and_say_so(self):
        s = scraper([InterfaceError("s", {}, Exception()), InterfaceError("s", {}, Exception())])
        assert await s.save_property({}) is None
        assert len(s.calls) == 2
        assert "InterfaceError" in s._last_save_error and "×2" in s._last_save_error

    @pytest.mark.asyncio
    async def test_it_never_loops_past_two(self):
        s = scraper([InterfaceError("s", {}, Exception())] * 5)
        await s.save_property({})
        assert len(s.calls) == 2


class TestOnlyDroppedConnectionsAreRetried:
    def test_the_attempt_raises_the_marker_for_those_only(self):
        src = inspect.getsource(DivarScraper._save_property_attempt)
        i = src.rindex("except Exception as e:")
        tail = src[i:]
        assert "if _is_dropped_connection(e):" in tail
        assert "raise _DroppedConnection(e)" in tail

    def test_every_other_failure_still_records_its_reason(self):
        """The #10 fix must not undo the «say why a save failed» work."""
        src = inspect.getsource(DivarScraper._save_property_attempt)
        assert "self._last_save_error = type(e).__name__" in src
