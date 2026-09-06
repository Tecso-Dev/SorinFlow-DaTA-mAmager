"""
The listings a run saw and did not save.

Every scrape drops candidates, usually for good reasons: a filter said no, the
category did not match, the page would not open. Until now the only trace was
a number in the finish line — «۱۴۸ نامزد — ۱۴ تازه، ۹۷ تکراری، ۳ ناموفق، ۳۲
خارج از دسته‌بندی، ۲ ودیعه». That number can be checked for arithmetic and
nothing else: whether those 32 were promoted junk or 32 real apartments is not
a question a count can answer, and the listings themselves were gone.

So keep them, with their Divar link, and let the panel show them.

The two rules are job_log's, for the same reasons:

* **Its own session, always.** The scraper commits job progress on its session
  while a run is in flight; a failed INSERT inside that transaction would put
  Postgres into an aborted state and take the scrape down with it.
* **Never raises.** A run must not fail because we could not write down what
  it skipped.
"""
from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import delete, select

from app.database import async_session_maker

# Kept in step with the job log, which is the other half of the same story.
RETENTION_DAYS = 30


async def record(job_id, *, divar_id: str, url: str = None, title: str = None,
                 reason: str = "unknown", detail: str = None) -> bool:
    """Write down one listing this run did not save. Returns True if stored.

    `job_id` is the ScrapingJob.job_id UUID, not the integer primary key.
    `reason` is the tally bucket the run counted it under, so the panel can
    label it with the Persian names the scraper already has.
    """
    if job_id is None or not divar_id:
        return False

    from app.models.scraping_job import SkippedListing

    try:
        async with async_session_maker() as db:
            db.add(SkippedListing(
                job_id=job_id,
                divar_id=str(divar_id)[:32],
                url=(url or f"https://divar.ir/v/{divar_id}")[:400],
                title=(title or None) and str(title)[:300],
                reason=str(reason)[:64],
                detail=(detail or None) and str(detail)[:300],
            ))
            await db.commit()
        return True
    except Exception as e:
        # Deliberately swallowed, as in job_log: the alternative is a scrape
        # that dies because its notebook was full.
        logger.warning(f"[skipped] could not record {divar_id} for {job_id}: "
                       f"{type(e).__name__}: {e}")
        return False


async def for_job(db, job_id, limit: int = 1000, reason: str = None):
    """What one run skipped, in the order it met them."""
    from app.models.scraping_job import SkippedListing

    q = select(SkippedListing).where(SkippedListing.job_id == job_id)
    if reason:
        q = q.where(SkippedListing.reason == reason)
    q = q.order_by(SkippedListing.id.asc()).limit(limit)
    return (await db.execute(q)).scalars().all()


async def counts_for_job(db, job_id) -> dict:
    """{reason: how many}, for a summary that does not fetch every row."""
    from app.models.scraping_job import SkippedListing

    rows = (await db.execute(
        select(SkippedListing.reason).where(SkippedListing.job_id == job_id)
    )).scalars().all()
    out: dict = {}
    for r in rows:
        out[r] = out.get(r, 0) + 1
    return out


async def prune(days: int = RETENTION_DAYS) -> int:
    """Drop rows older than `days`. Called at the start of a run.

    No scheduler of its own, for job_log's reason: a table that only grows
    while scraping only needs tidying while scraping.
    """
    from app.models.scraping_job import SkippedListing

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    try:
        async with async_session_maker() as db:
            res = await db.execute(
                delete(SkippedListing).where(SkippedListing.created_at < cutoff))
            await db.commit()
            return res.rowcount or 0
    except Exception as e:
        logger.warning(f"[skipped] prune skipped: {type(e).__name__}: {e}")
        return 0
