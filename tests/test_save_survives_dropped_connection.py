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


def _caller_block(src: str) -> str:
    """The save-and-count block with comment lines removed.

    These assertions used to slice a fixed number of characters from the call.
    That budget is spent by whatever comments the block happens to carry, so
    adding an explanation to the code broke tests that were asserting nothing
    about explanations — three times, including the guard that fixed run 109.
    Comments out, structure in."""
    blk = src[src.index("saved = await self.save_property(property_data)"):]
    blk = "\n".join(l for l in blk.splitlines() if not l.strip().startswith("#"))
    # up to the end of the if/elif chain: the first line back at the `saved =`
    # indent that is not part of it
    return blk[:blk.index("await self._human_like_delay(")] if "await self._human_like_delay(" in blk else blk[:4000]



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


class TestTheJobRowSurvivesTheRetry:
    """Run 109 died at listing 7, and the retry had already worked:

        17:06:21  [save] connection dropped mid-save (InterfaceError) — retrying
        17:06:21  Updated property: gajmvaRR
        17:06:21  Failed to process listing: greenlet_spawn has not been called

    A rollback expires every ORM object on that session, `job` included. The
    caller's next statement is `job.new_items += 1`, and touching an expired
    attribute triggers a lazy reload — synchronous, outside the greenlet, so
    MissingGreenlet. The fix for the save broke the line after it."""

    def test_the_wrapper_reports_that_it_rolled_back(self):
        import inspect
        from app.scraper.divar_scraper import DivarScraper
        src = inspect.getsource(DivarScraper.save_property)
        assert "self._last_save_rolled_back = False" in src, "the flag is never reset"
        assert "self._last_save_rolled_back = True" in src, "a rollback is not reported"

    def test_the_caller_re_reads_the_job_before_touching_a_counter(self):
        import inspect
        from app.scraper.divar_scraper import DivarScraper
        src = inspect.getsource(DivarScraper.start_scraping_job)
        blk = _caller_block(src)
        guard = blk.index("_last_save_rolled_back")
        for counter in ("job.failed_items += 1", "job.new_items += 1", "job.updated_items += 1"):
            assert guard < blk.index(counter), \
                f"{counter} is touched before the job row is re-read"

    def test_the_re_read_cannot_itself_kill_the_run_silently(self):
        """refresh() on a session whose connection just died raises too."""
        import inspect
        from app.scraper.divar_scraper import DivarScraper
        src = inspect.getsource(DivarScraper.start_scraping_job)
        blk = src[src.index("_last_save_rolled_back"):][:1200]
        assert "except Exception" in blk and "rollback()" in blk

    def test_a_normal_save_does_not_pay_for_a_refresh(self):
        """The common path is a save that did not roll back; it must not add a
        query per listing."""
        import inspect
        from app.scraper.divar_scraper import DivarScraper
        src = inspect.getsource(DivarScraper.start_scraping_job)
        blk = _caller_block(src)
        assert 'if getattr(self, "_last_save_rolled_back", False):' in blk, \
            "the refresh is unconditional"
