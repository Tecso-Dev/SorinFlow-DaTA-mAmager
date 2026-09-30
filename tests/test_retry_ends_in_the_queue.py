"""
A «تلاش دوباره» the queue has to end ends the retry, not the run (#58 review).

The queue closes out three kinds of row by itself: a running row whose
worker is gone (release_orphans), a pending row nobody took for a day
(_fail_stale_pending) and a row whose config does not build a run
(_fail_unreadable). Each wrote «failed» with its own line — so a completed
run whose retry was interrupted became a failed run, with config.retry
still on it. A row in a retry goes back to the status it had, with the
reason in front of its old finish line, and without config.retry.
"""
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

import _scrape_harness as h  # noqa: E402

from app import database  # noqa: E402
from app.models.scraping_job import ScrapingJob  # noqa: E402
from app.services import scrape_queue as sq  # noqa: E402

BEFORE = "آگهی بیشتری پیدا نشد — 3 از 10 درخواستی"


@pytest.fixture(autouse=True)
async def schema():
    """The tables, on the Postgres test database (tests/_scrape_harness.py)."""
    eng, _ = await h.open_db()
    await eng.dispose()
    yield


async def _row(status, *, retry=True, config=None):
    h.pg_url()
    cfg = config or {"city": h.CITY, "category": h.CATEGORY, "max_items": 10,
                     "download_images": False}
    if retry:
        cfg = {**cfg, "retry": {"items": [{"divar_id": "fkaaaa0001"}], "at": "2026-09-29T10:00:00+00:00",
                                "prev_status": "completed", "prev_finish": BEFORE}}
    async with database.async_session_maker() as db:
        job = ScrapingJob(job_id=uuid.uuid4(), status=status, config=cfg)
        db.add(job)
        await db.commit()
        return job.job_id


async def _get(jid):
    from sqlalchemy import select
    async with database.async_session_maker() as db:
        return (await db.execute(select(ScrapingJob).where(ScrapingJob.job_id == jid))).scalar_one()


class TestARetryEndsAsTheRunWas:
    async def test_an_orphaned_retry(self):
        jid = await _row("running")
        assert await sq.release_orphans([jid]) == 1
        row = await _get(jid)
        assert row.status == "completed"
        assert row.finish_reason.startswith("تلاش دوباره") and row.finish_reason.endswith(BEFORE)
        assert "retry" not in row.config and row.completed_at is not None

    async def test_a_retry_nobody_took_for_a_day(self):
        jid = await _row("pending")
        assert await sq._fail_stale_pending([jid]) == 1
        row = await _get(jid)
        assert row.status == "completed" and row.finish_reason.endswith(BEFORE)
        assert "retry" not in row.config

    async def test_a_retry_whose_config_does_not_read(self):
        jid = await _row("pending", config={"city": h.CITY, "category": h.CATEGORY, "max_items": "many"})
        assert await sq._fail_unreadable(str(jid), ValueError("max_items")) is True
        row = await _get(jid)
        assert row.status == "completed" and row.finish_reason.endswith(BEFORE)
        assert "retry" not in row.config

    async def test_a_plain_run_still_fails_as_before(self):
        jid = await _row("running", retry=False)
        await sq.release_orphans([jid])
        row = await _get(jid)
        assert row.status == "failed" and row.finish_reason == sq.ORPHAN_REASON
