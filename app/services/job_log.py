"""
What happened during one scrape, kept where it can still be read afterwards.

The application log already records a great deal, but it cannot answer the
question that actually gets asked — «این ران چرا نصفه ماند؟». Nothing in
scraper.log carries a job id, so with two runs in a day the lines interleave
with no way to tell them apart; and the file rotates at 10 MB with seven days
of retention, so the run somebody wants to understand is often the one that has
already aged out.

The `scraping_logs` table has existed since the first migration with exactly
the right shape and has never been written to. This fills it.

Two rules that matter more than the content:

* **Its own session, always.** The scraper commits job progress on its session
  while a run is in flight. A failed INSERT inside that transaction would put
  Postgres into an aborted state and take the scrape down with it — turning a
  logging problem into a data-loss problem. Each event is its own tiny
  transaction on its own connection, so it cannot reach the caller.
* **Never raises.** A run must not fail because we could not describe it.
"""
from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import delete, select

from app.database import async_session_maker

# Stages, so the panel can group and colour a timeline without parsing prose.
# Deliberately few — a vocabulary nobody can remember gets used inconsistently
# and then means nothing.
START = "start"          # the run began, with the settings it began with
SESSION = "session"      # which Divar account, and whether contacts are reachable
PAGE = "page"            # a page or batch of listings was processed
ITEM = "item"            # something notable about one listing
CHALLENGE = "challenge"  # Divar asked for a code, or rate-limited us
PAUSE = "pause"
RESUME = "resume"
FINISH = "finish"        # completed — including completed-but-short, with why
ERROR = "error"          # the run died

# Events outlive the log file on purpose, but not forever.
RETENTION_DAYS = 30


async def record(job_id, stage: str, message: str, *, level: str = "info", **details):
    """Write one event for a job. Returns True if it was stored.

    `job_id` is the ScrapingJob.job_id UUID, not the integer primary key —
    that is what the table's foreign key points at.
    """
    if job_id is None:
        return False

    from app.models.scraping_job import ScrapingLog

    payload = {"stage": stage}
    payload.update({k: v for k, v in details.items() if v is not None})

    try:
        async with async_session_maker() as db:
            db.add(ScrapingLog(job_id=job_id, level=level,
                               message=(message or "")[:2000], details=payload))
            await db.commit()
        return True
    except Exception as e:
        # Deliberately swallowed. The alternative is a scrape that dies because
        # its diary was full.
        logger.warning(f"[job-log] could not record {stage} for {job_id}: "
                       f"{type(e).__name__}: {e}")
        return False


async def events_for(db, job_id, limit: int = 500, level: str | None = None):
    """Every recorded event for one job, oldest first — a timeline reads down."""
    from app.models.scraping_job import ScrapingLog

    q = select(ScrapingLog).where(ScrapingLog.job_id == job_id)
    if level:
        q = q.where(ScrapingLog.level == level)
    q = q.order_by(ScrapingLog.created_at.asc(), ScrapingLog.id.asc()).limit(limit)
    return (await db.execute(q)).scalars().all()


async def prune(days: int = RETENTION_DAYS) -> int:
    """Drop events older than `days`. Called at the start of a run.

    No scheduler of its own: a table that only grows while scraping only needs
    tidying while scraping, and hanging it off the run keeps it self-limiting
    on a box with one small disk.
    """
    from app.models.scraping_job import ScrapingLog

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    try:
        async with async_session_maker() as db:
            res = await db.execute(
                delete(ScrapingLog).where(ScrapingLog.created_at < cutoff))
            await db.commit()
            return res.rowcount or 0
    except Exception as e:
        logger.warning(f"[job-log] prune skipped: {type(e).__name__}: {e}")
        return 0
