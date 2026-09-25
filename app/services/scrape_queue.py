"""
The scrape queue: whoever takes the request enqueues, a worker runs it.

A scrape used to be an asyncio task inside whichever process answered the
HTTP request, so that process restarting took the scrape with it, and a
second api replica would have run scrapes of its own. Now _launch_job writes
the row (pending) and pushes its id here; a worker process — role worker, or
all — pops it, claims it and runs run_scraping_job as the request used to.

Postgres stays the source of truth. The queue only says «look at this row»:
a row that is no longer pending when a worker gets to it is skipped, so a
duplicate entry is harmless, and a lost one (Redis down at enqueue, a flush,
a pop cut short by a shutdown) is repaired by the sweep putting pending rows
back.

  sf:scrape:queue             job ids, LPUSH in / BRPOP out — first in, first out
  sf:scrape:running:{job_id}  the claim: which worker runs it. TTL 90 s,
                              re-asserted every 30 s while the run lasts, so a
                              running or paused row without one belongs to a
                              worker that is gone.
"""
import asyncio
import os
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List

from loguru import logger
from redis.exceptions import RedisError, WatchError
from sqlalchemy import select, update

from app import database
from app.config import get_settings
from app.models.scraping_job import ScrapingJob
from app.schemas import ScrapingJobCreate
from app.services import job_log
from app.services.supervisor import HOST, beat, supervise

settings = get_settings()

QUEUE = "sf:scrape:queue"
CLAIM = "sf:scrape:running:{}"
CLAIM_TTL = 90
CLAIM_REFRESH = 30
POP_TIMEOUT = 2          # a BRPOP waits this long, so the consumer beats and stops promptly
SWEEP_EVERY = 60
DRAIN_LOG_EVERY = 60
# A pending row younger than this is left alone by the sweep: it may still be
# on its way into the queue (committed, not pushed yet) — or, during the first
# rollout to this code, be running inside an api pod of the previous release,
# which never claims. Putting that one back would run it twice.
REQUEUE_AFTER = timedelta(minutes=5)
# A pending row this old has been rebuilt and pushed back by the sweep for a
# full day without any worker ever taking it — requeuing it forever only
# hides that something is actually wrong (bad config, nobody consuming the
# queue). Marked failed instead of requeued past this age.
STALE_PENDING_AFTER = timedelta(hours=24)

# The words a run killed with its process has always ended with, so the panel
# and the «ادامه» button read it exactly as before.
ORPHAN_REASON = ("سرور در میانهٔ اجرا ری‌استارت شد — این تسک ادامه پیدا "
                 "نکرد. آگهی‌های ذخیره‌شده سر جایشان هستند؛ با دکمهٔ "
                 "«ادامه» از همان‌جا دنبال می‌شود")
ORPHAN_LOG = ("سرور در میانهٔ این اسکرپ ری‌استارت شد (استقرار نسخهٔ "
              "جدید یا ری‌استارت سرویس) — تسک ادامه پیدا نکرد")

STALE_PENDING_REASON = ("این تسک بیش از ۲۴ ساعت در صف ماند و هیچ ورکری آن را "
                        "اجرا نکرد — ناموفق ثبت شد. با دکمهٔ «ادامه» دوباره "
                        "اجرا کنید")
STALE_PENDING_LOG = ("این تسک بیش از ۲۴ ساعت در صف بود و هیچ ورکری آن را "
                     "برنداشت — ناموفق ثبت شد")

# Unique per process even where the pid is not: a restarted container is
# PID 1 again, under the same hostname, and must not mistake the dead
# process's claims for its own.
WORKER_ID = f"{HOST}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
draining = False
_running: Dict[str, asyncio.Task] = {}      # job id → the task running it, here


def running_ids() -> List[str]:
    return sorted(_running)


async def enqueue(job_id: str) -> None:
    r: Any = await database.get_redis()    # Any: redis-py types the asyncio client as the sync one
    await r.lpush(QUEUE, job_id)


def job_kwargs(job) -> dict:
    """run_scraping_job's arguments, by name, from a ScrapingJob row.

    config holds every ScrapingJobCreate field as _launch_job left it — after
    a switched-off number fell back to «خودکار» and the URL list was cleaned —
    plus owner_user_id. By name, not position: the call used to pass thirty
    arguments positionally, where one inserted in the middle shifts every
    filter after it into the wrong parameter without a word.
    """
    cfg = job.config or {}
    fields = ScrapingJobCreate(**{k: v for k, v in cfg.items()
                                  if k in ScrapingJobCreate.model_fields}).model_dump()
    fields["divar_phone"] = fields["divar_phone"] or None      # «» always went on as None
    return {**fields, "job_id": str(job.job_id), "db_url": settings.database_url,
            "owner_user_id": cfg.get("owner_user_id")}


async def _cas(r, job_id: str, keep: bool) -> bool:
    """Compare-and-set on a claim, only while it is ours. keep: hold it for
    another TTL, re-creating it if Redis lost it; otherwise delete it.

    WATCH/MULTI rather than a Lua script: the test suite's fakeredis cannot
    run scripts, and this is two keys' worth of work.
    """
    key = CLAIM.format(job_id)
    async with r.pipeline(transaction=True) as p:
        await p.watch(key)
        holder = await p.get(key)
        if holder != WORKER_ID and not (keep and holder is None):
            return False
        p.multi()
        if keep:
            p.set(key, WORKER_ID, ex=CLAIM_TTL)
        else:
            p.delete(key)
        try:
            await p.execute()
        except WatchError:
            return False
    return True


async def release(job_id: str) -> None:
    try:
        await _cas(await database.get_redis(), job_id, keep=False)
    except Exception as e:
        logger.warning(f"[queue] {job_id[:8]}: claim not released ({type(e).__name__}) — "
                       f"it expires by itself in {CLAIM_TTL}s")


async def _keep_claim(job_id: str) -> None:
    """Every 30 s for as long as the run lasts — through a drain too, or the
    next worker's sweep would take a run that is still finishing for an
    orphan."""
    while True:
        await asyncio.sleep(CLAIM_REFRESH)
        try:
            if not await _cas(await database.get_redis(), job_id, keep=True):
                logger.error(f"[queue] {job_id[:8]}: its claim is held by another worker")
        except Exception as e:
            logger.warning(f"[queue] {job_id[:8]}: claim not refreshed ({type(e).__name__})")


async def _run(job_id: str, kwargs: dict) -> None:
    from app.api.routes import scraper as routes
    keeper = asyncio.create_task(_keep_claim(job_id), name=f"claim:{job_id[:8]}")
    logger.info(f"[queue] {job_id[:8]} started on {WORKER_ID}")
    try:
        await routes.run_scraping_job(**kwargs)
    except Exception as e:
        logger.opt(exception=e).error(f"[queue] {job_id[:8]} ended with {type(e).__name__}")
    finally:
        keeper.cancel()
        _running.pop(job_id, None)
        await release(job_id)
        logger.info(f"[queue] {job_id[:8]} finished")


async def _take(r, job_id: str) -> None:
    """Claim one popped id and start its run — unless another worker holds
    it (a duplicate entry) or the row is no longer pending (cancelled before
    it started, already run, deleted)."""
    try:
        jid = uuid.UUID(job_id)
    except ValueError:
        logger.warning(f"[queue] dropped an entry that is not a job id: {job_id[:40]!r}")
        return
    if not await r.set(CLAIM.format(job_id), WORKER_ID, nx=True, ex=CLAIM_TTL):
        return
    try:
        async with database.async_session_maker() as db:
            job = (await db.execute(
                select(ScrapingJob).where(ScrapingJob.job_id == jid))).scalar_one_or_none()
        if job is None or job.status != "pending":
            logger.info(f"[queue] {job_id[:8]} is {job.status if job else 'gone'} — not running it")
            await release(job_id)
            return
        kwargs = job_kwargs(job)
    except BaseException:
        # a database error, or a drain arriving mid-take: the row is still
        # pending, so the sweep puts it back — only the claim must not linger
        await release(job_id)
        raise
    _running[job_id] = asyncio.create_task(_run(job_id, kwargs), name=f"scrape:{job_id[:8]}")


async def consume() -> None:
    """Pop, claim, run — at most SCRAPE_WORKER_CONCURRENCY at once, and that
    cap is GLOBAL, not per process: during a worker rollout (maxSurge 1) the
    draining pod keeps its own runs going while the new pod's local
    `_running` starts at zero, so checking only that let a rollout briefly
    run double the configured Chromiums on one node. Beats every few
    seconds, idle or full."""
    warned = False
    while not draining:
        beat("scrape_consumer")
        if len(_running) >= settings.scrape_worker_concurrency:
            await asyncio.wait(list(_running.values()), timeout=POP_TIMEOUT,
                               return_when=asyncio.FIRST_COMPLETED)
            continue
        try:
            # ponytail: a count-then-act check, not atomic, so two workers
            # racing this at once can briefly go one job over the cap — far
            # better than today's whole extra process worth, and a Lua-scripted
            # reserve is precision the test suite's fakeredis cannot exercise
            # anyway (see _cas above).
            if len(await claims()) >= settings.scrape_worker_concurrency:
                await asyncio.sleep(POP_TIMEOUT)
                continue
            r: Any = await database.get_redis()
            got = await r.brpop(QUEUE, timeout=POP_TIMEOUT)
        except (RedisError, OSError) as e:
            if not warned:
                warned = True
                logger.warning(f"[queue] Redis unavailable ({type(e).__name__}) — "
                               "no scrape is taken until it is back")
            await asyncio.sleep(POP_TIMEOUT)
            continue
        if warned:
            warned = False
            logger.info("[queue] Redis is back — taking scrapes again")
        if got and draining:
            # SIGTERM came while this pop was waiting: the id goes back to the
            # end it was popped from, first in line for the next worker.
            await r.rpush(QUEUE, got[1])
        elif got:
            await _take(r, got[1])


def _older_than(ts, age: timedelta) -> bool:
    """created_at comes back aware from Postgres and naive from sqlite."""
    if ts is None:
        return True
    now = datetime.now(ts.tzinfo) if ts.tzinfo else datetime.now()
    return now - ts > age


async def release_orphans(job_ids) -> int:
    """Close out runs whose worker is gone, exactly as a restart always
    closed them: failed, the finish line pointing at «ادامه», the same line
    in the run's own log. Only rows still running or paused — one that
    finished a moment ago keeps its real ending."""
    async with database.async_session_maker() as db:
        released = (await db.execute(
            update(ScrapingJob)
            .where(ScrapingJob.job_id.in_(list(job_ids)),
                   ScrapingJob.status.in_(("running", "paused")))
            .values(status="failed", completed_at=datetime.now(), finish_reason=ORPHAN_REASON)
            .returning(ScrapingJob.job_id)
            .execution_options(synchronize_session=False))).scalars().all()
        await db.commit()
    if released:
        logger.warning(f"{len(released)} scraping job(s) were left running by a "
                       "process that is gone and have been marked failed")
    # Said in the run log too, not only in finish_reason. The گزارش timeline is
    # where anyone looks first when a run stops, and a job killed by a deploy
    # otherwise ends with its last ordinary event — which reads as though the
    # scraper gave up on its own. It did not; the process it ran in went away.
    # That happened twice, both times during an unrelated deploy.
    for job_id in released:
        await job_log.record(job_id, job_log.ERROR, ORPHAN_LOG, level="error")
    return len(released)


async def _fail_stale_pending(job_ids) -> int:
    """Pending rows the sweep has been rebuilding and re-queueing for a full
    day without any worker ever taking them. Requeuing them again would only
    hide whatever actually keeps them from running (bad config, nobody
    consuming the queue); failed says so instead, the same way any other
    stop does. Same shape as release_orphans, for pending rows instead of
    running/paused ones."""
    async with database.async_session_maker() as db:
        failed = (await db.execute(
            update(ScrapingJob)
            .where(ScrapingJob.job_id.in_(list(job_ids)),
                   ScrapingJob.status == "pending")
            .values(status="failed", completed_at=datetime.now(), finish_reason=STALE_PENDING_REASON)
            .returning(ScrapingJob.job_id)
            .execution_options(synchronize_session=False))).scalars().all()
        await db.commit()
    if failed:
        logger.warning(f"{len(failed)} scraping job(s) sat pending over 24h with no worker "
                       "taking them and have been marked failed")
    for job_id in failed:
        await job_log.record(job_id, job_log.ERROR, STALE_PENDING_LOG, level="error")
    return len(failed)


async def sweep() -> dict:
    """What Redis lost, put right from Postgres.

    A running or paused row that nobody claims belongs to a worker that is
    gone: closed out as a restart always did. A pending row neither queued
    nor claimed lost its entry: pushed back, oldest first — unless it has
    been going in circles for a full day, in which case it is marked failed
    instead of pushed back yet again. Runs this process holds are never
    touched, even in the moment after a Redis restart before their claims
    are re-asserted.
    """
    r: Any = await database.get_redis()
    async with database.async_session_maker() as db:
        rows = (await db.execute(
            select(ScrapingJob.job_id, ScrapingJob.status, ScrapingJob.created_at)
            .where(ScrapingJob.status.in_(("pending", "running", "paused")))
            .order_by(ScrapingJob.id))).all()
    orphans, stale_pending, requeued = [], [], 0
    for job_id, status, created_at in rows:
        jid = str(job_id)
        if jid in _running or await r.exists(CLAIM.format(jid)):
            continue
        if status != "pending":
            orphans.append(job_id)
        elif _older_than(created_at, STALE_PENDING_AFTER):
            stale_pending.append(job_id)
        elif _older_than(created_at, REQUEUE_AFTER) and await r.lpos(QUEUE, jid) is None:
            await r.lpush(QUEUE, jid)
            requeued += 1
    released = await release_orphans(orphans) if orphans else 0
    failed_stale = await _fail_stale_pending(stale_pending) if stale_pending else 0
    return {"released": released, "requeued": requeued, "failed_stale": failed_stale}


async def sweep_loop() -> None:
    """At start and every minute: what used to be the boot-time «release
    orphaned jobs» — safe now while other processes live, because it only
    takes rows nobody claims — plus lost queue entries put back."""
    while True:
        beat("scrape_sweep")
        try:
            res = await sweep()
            if res["released"] or res["requeued"] or res["failed_stale"]:
                logger.info(f"[queue] sweep: {res}")
        except Exception as e:
            logger.warning(f"[queue] sweep failed: {type(e).__name__}: {e}")
        await asyncio.sleep(SWEEP_EVERY)


def start(role: str) -> List[asyncio.Task]:
    """The worker's half of a process (roles all and worker): the consumer
    and the sweep, each supervised. The sweep's first pass runs at once."""
    global draining
    draining = False          # a previous lifespan in this process may have drained
    return [asyncio.create_task(supervise("scrape_consumer", consume, 60, role),
                                name="supervise:scrape_consumer"),
            asyncio.create_task(supervise("scrape_sweep", sweep_loop, 10 * 60, role),
                                name="supervise:scrape_sweep")]


async def drain(tasks: List[asyncio.Task]) -> None:
    """SIGTERM on a worker: take nothing new, let what runs finish.

    No timeout of our own. A scrape cut short loses its place in the feed and
    whatever code somebody typed for it; Kubernetes' grace period (7200 s on
    the worker) is the ceiling. The process heartbeat and each run's claim
    refresher keep going meanwhile, and nothing here touches Chromium — each
    run closes its own browser as it ends.
    """
    global draining
    draining = True
    # The consumer stops by itself within one POP_TIMEOUT. Cancelled mid-BRPOP
    # it left the pop waiting on the Redis server, which then handed the next
    # enqueued id to nobody — off the list until the sweep's five-minute
    # re-queue (seen on CI). Only what is still going after that is cancelled.
    _done, still_running = await asyncio.wait(tasks, timeout=POP_TIMEOUT + 3)
    for t in still_running:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    while _running:
        logger.info(f"[queue] draining — waiting for {len(_running)} run(s): "
                    f"{', '.join(j[:8] for j in _running)}")
        await asyncio.wait(list(_running.values()), timeout=DRAIN_LOG_EVERY)
    logger.info("[queue] drained — no scrape running")


async def claims() -> Dict[str, str]:
    """Every run some worker holds right now: job id → worker."""
    r: Any = await database.get_redis()
    keys = [k async for k in r.scan_iter(match=CLAIM.format("*"), count=200)]
    holders = await r.mget(keys) if keys else []
    return {k.rsplit(":", 1)[1]: h for k, h in zip(keys, holders, strict=True) if h}
