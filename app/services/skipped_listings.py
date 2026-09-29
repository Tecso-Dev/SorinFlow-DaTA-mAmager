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
from typing import Optional

from loguru import logger
from sqlalchemy import delete, select

from app.database import async_session_maker

# Kept in step with the job log, which is the other half of the same story.
RETENTION_DAYS = 30


async def record(job_id, *, divar_id: str, url: Optional[str] = None,
                 title: Optional[str] = None, reason: str = "unknown",
                 detail: Optional[str] = None) -> bool:
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


async def resolve(divar_id: str) -> int:
    """Drop every skipped row for this listing — it has been saved with a
    number, so it is no longer something to retry by hand.

    Without this a listing recovered by «اسکرپ تکی» stayed in the panel's
    list as though it were still missing, and the count beside «بدون شماره»
    never went down. The rows are removed rather than flagged: the list is
    «what still needs a hand», and this does not.

    Own session, never raises — the same rules as record().
    """
    if not divar_id:
        return 0
    from app.models.scraping_job import SkippedListing

    try:
        async with async_session_maker() as db:
            res = await db.execute(
                delete(SkippedListing).where(SkippedListing.divar_id == str(divar_id)))
            await db.commit()
            n = res.rowcount or 0
            if n:
                logger.info(f"[skipped] {divar_id} resolved — {n} row(s) cleared")
            return n
    except Exception as e:
        logger.warning(f"[skipped] could not resolve {divar_id}: {type(e).__name__}: {e}")
        return 0


# The two ways a listing is saved without a number that a later visit can
# fix. chat_only is the poster's choice and never changes, so it is not here.
AWAITING_PHONE = ("no_phone", "needs_identity")
# A number that never shows — a hidden or a virtual one — must not cost a
# reveal on every run for a month. Three visits, counting the first.
PHONE_ATTEMPTS = 3


async def awaiting_phone(db, *, city_id, category_id, owner_user_id=None,
                         limit: int = 20, attempts: int = PHONE_ATTEMPTS) -> list:
    """The listings the next run of this city and category owes a retry.

    Saved without a phone number by an earlier run of the same city and
    category started by the same person — the account budget a retry spends
    is theirs — newest first, as [{divar_id, url, title}]. Only while the
    listing is still stored, still has no number and is not chat-only; not
    once Divar has said it is gone; and not after `attempts` visits, where
    every skipped row for the listing counts, whichever run wrote it.

    A read on the caller's session; the caller commits.
    """
    from app.models.property import Property
    from app.models.scraping_job import ScrapingJob, SkippedListing

    if city_id is None or category_id is None or limit <= 0:
        return []
    rows = (await db.execute(
        select(SkippedListing.divar_id, SkippedListing.url, SkippedListing.title,
               ScrapingJob.config)
        .join(ScrapingJob, ScrapingJob.job_id == SkippedListing.job_id)
        .where(SkippedListing.reason.in_(AWAITING_PHONE),
               ScrapingJob.city_id == city_id,
               ScrapingJob.category_id == category_id)
        .order_by(SkippedListing.id.desc())
    )).all()
    owed: dict = {}
    for divar_id, url, title, cfg in rows:
        if divar_id in owed or (cfg or {}).get("owner_user_id") != owner_user_id:
            continue
        owed[divar_id] = {"divar_id": divar_id, "url": url, "title": title}
    if not owed:
        return []

    ids = list(owed)
    visits: dict = {}
    for divar_id, reason in (await db.execute(
            select(SkippedListing.divar_id, SkippedListing.reason)
            .where(SkippedListing.divar_id.in_(ids)))).all():
        visits.setdefault(divar_id, []).append(reason)
    held = {d: (phone, channel) for d, phone, channel in (await db.execute(
        select(Property.divar_id, Property.phone_number, Property.contact_channel)
        .where(Property.divar_id.in_(ids)))).all()}

    out = []
    for d in ids:
        if d not in held:
            continue            # removed from the table by hand: not ours to bring back
        phone, channel = held[d]
        if (phone or "").strip() or channel == "chat_only":
            continue
        seen = visits.get(d, [])
        if "deleted" in seen or len(seen) >= attempts:
            continue
        out.append(owed[d])
        if len(out) >= limit:
            break
    return out
