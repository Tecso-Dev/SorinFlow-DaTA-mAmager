"""
Scrapes go through a queue a worker process drains (app/services/scrape_queue.py).

A scrape used to run as a BackgroundTask of the request that started it, so
it lived and died with that process. Now _launch_job writes the row and
queues its id; a worker claims it in Redis and runs run_scraping_job with
the row's saved config. Postgres stays the truth, so these check both sides:
what runs (and with exactly the arguments the request used to pass), what is
skipped, what a dead worker's runs end as, and what a drain waits for.

The suite's own database — Postgres: scraping_jobs.job_id is a Postgres UUID
column, which sqlite cannot create, so the tests that need rows say so and
skip there like every other Postgres suite. fakeredis for the queue, and
run_scraping_job replaced by a stand-in that moves the row the way the
scraper does.
"""
import asyncio
import inspect
import json
import os
import sys
import time
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_scrape_queue.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

import fakeredis  # noqa: E402
import fakeredis.aioredis  # noqa: E402
from loguru import logger  # noqa: E402
from sqlalchemy import delete, select, update  # noqa: E402

from app import database  # noqa: E402
from app.api.routes import scraper as routes  # noqa: E402
from app.models.property import Category, City  # noqa: E402
from app.models.scraping_job import ScrapingJob, ScrapingLog  # noqa: E402
from app.schemas import ScrapingJobCreate  # noqa: E402
from app.services import scrape_queue as sq  # noqa: E402

TABLES = [City.__table__, Category.__table__, ScrapingJob.__table__, ScrapingLog.__table__]
# before any test swaps the function for a stand-in
RUN_SIGNATURE = inspect.signature(routes.run_scraping_job)


@pytest.fixture
async def rq(monkeypatch):
    """A fakeredis behind app.database.get_redis, quick timings, no run left
    over from the test before."""
    fake = fakeredis.aioredis.FakeRedis(server=fakeredis.FakeServer(), decode_responses=True)

    async def _get():
        return fake
    monkeypatch.setattr(database, "get_redis", _get)
    monkeypatch.setattr(sq, "POP_TIMEOUT", 1)          # BRPOP takes whole seconds
    monkeypatch.setattr(sq, "draining", False)
    sq._running.clear()
    yield SimpleNamespace(redis=fake, made=[])
    for task in list(sq._running.values()):
        task.cancel()
    await asyncio.gather(*sq._running.values(), return_exceptions=True)
    sq._running.clear()


@pytest.fixture
async def queue(rq):
    """rq plus the real scraping_jobs table — and every row a test makes is
    removed after it, so no later worker in the suite finds one to run."""
    if not str(database.engine.url).startswith("postgresql"):
        pytest.skip("needs Postgres — scraping_jobs.job_id is a Postgres UUID column")
    async with database.engine.begin() as conn:
        await conn.run_sync(lambda c: database.Base.metadata.create_all(c, tables=TABLES))
    yield rq
    ids = [uuid.UUID(j) for j in rq.made]
    if ids:
        async with database.async_session_maker() as db:
            await db.execute(delete(ScrapingLog).where(ScrapingLog.job_id.in_(ids)))
            await db.execute(delete(ScrapingJob).where(ScrapingJob.job_id.in_(ids)))
            await db.commit()


@pytest.fixture
def logs():
    seen = []
    sink = logger.add(lambda m: seen.append(m.record["message"]), level="INFO")
    yield seen
    logger.remove(sink)


async def _job(q, status="pending", age=timedelta(minutes=10), config=None) -> str:
    async with database.async_session_maker() as db:
        row = ScrapingJob(status=status, created_at=datetime.now() - age,
                          config=config or {"city": "urmia", "category": "rent-apartment",
                                            "owner_user_id": None})
        db.add(row)
        await db.commit()
        await db.refresh(row)
    q.made.append(str(row.job_id))
    return str(row.job_id)


async def _row(job_id):
    async with database.async_session_maker() as db:
        return (await db.execute(select(ScrapingJob).where(
            ScrapingJob.job_id == uuid.UUID(job_id)))).scalar_one()


async def _set_status(job_id, status):
    async with database.async_session_maker() as db:
        await db.execute(update(ScrapingJob).where(ScrapingJob.job_id == uuid.UUID(job_id))
                         .values(status=status))
        await db.commit()


async def _until(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while not (await cond() if inspect.iscoroutinefunction(cond) else cond()):
        assert time.monotonic() < end, "condition not reached in time"
        await asyncio.sleep(0.01)


def _stand_in(monkeypatch, gate=None):
    """run_scraping_job as the scraper moves the row: running, then done."""
    runs = []

    async def fake_run(**kw):
        runs.append(kw)
        await _set_status(kw["job_id"], "running")
        if gate is not None:
            await gate.wait()
        await _set_status(kw["job_id"], "completed")
    monkeypatch.setattr(routes, "run_scraping_job", fake_run)
    return runs


def _old_call(job_id, cfg, owner_id):
    """What _launch_job used to hand BackgroundTasks, positionally, in its own
    order — bound to run_scraping_job's signature to get it by name."""
    args = (job_id, cfg.city, cfg.category, cfg.max_items, cfg.download_images,
            routes.settings.database_url, cfg.divar_phone or None,
            cfg.min_price, cfg.max_price, cfg.min_deposit, cfg.max_deposit,
            cfg.min_rent, cfg.max_rent, cfg.min_price_per_meter, cfg.max_price_per_meter,
            cfg.min_area, cfg.max_area, cfg.min_rooms, cfg.max_rooms,
            cfg.has_images, cfg.has_elevator, cfg.has_parking, cfg.has_storage, cfg.has_balcony,
            cfg.advertiser_type, cfg.max_age_hours, cfg.posted_date, cfg.rotate_every,
            owner_id, cfg.urls)
    return dict(RUN_SIGNATURE.bind(*args).arguments)


class TestNothingIsLostOnTheWay:

    def test_every_launch_field_is_a_parameter_of_the_run(self):
        """By name now: a field added to the form but not to the run fails
        here, not as a TypeError in a worker at 08:00."""
        params = set(RUN_SIGNATURE.parameters)
        assert set(ScrapingJobCreate.model_fields) | {"job_id", "db_url", "owner_user_id"} == params

    @pytest.mark.parametrize("cfg,owner", [
        (dict(city="urmia", category="rent-apartment", max_items=40, download_images=False,
              divar_phone="", min_price=1_000_000_000, max_price=5_000_000_000,
              min_deposit=100, max_deposit=200, min_rent=5, max_rent=9,
              min_price_per_meter=10, max_price_per_meter=20, min_area=60, max_area=120,
              min_rooms=1, max_rooms=3, has_images=True, has_elevator=False, has_parking=True,
              has_storage=None, has_balcony=True, advertiser_type="personal",
              max_age_hours=24, rotate_every=25), 7),
        (dict(city="—", category="بازاسکرپ", download_images=True, posted_date="2026-09-20",
              urls=["https://divar.ir/v/a/AbCd1234", "not a listing", "https://divar.ir/v/b/EfGh5678"],
              max_items=2), None),
    ], ids=["filters", "url-list-no-owner"])
    async def test_a_queued_job_runs_with_what_the_request_used_to_pass(self, queue, monkeypatch,
                                                                       cfg, owner):
        runs = _stand_in(monkeypatch)
        job_config = ScrapingJobCreate(**cfg)
        user = SimpleNamespace(id=owner) if owner else None
        async with database.async_session_maker() as db:
            resp = await routes._launch_job(job_config, db, user)
        queue.made.append(resp.job_id)
        assert resp.status == "pending"
        assert await queue.redis.lrange(sq.QUEUE, 0, -1) == [resp.job_id]
        # _launch_job cleaned the URL list on the object itself, before the
        # old call read it — the expectation reads it after, the same way
        expected = _old_call(resp.job_id, job_config, owner)
        consumer = asyncio.create_task(sq.consume())
        await _until(lambda: runs)
        consumer.cancel()
        assert runs[0] == expected

        async def _released():
            return await queue.redis.get(sq.CLAIM.format(resp.job_id)) is None
        await _until(_released)                  # the claim goes when the run ends
        assert not sq.running_ids() and (await _row(resp.job_id)).status == "completed"


class TestWhatIsSkipped:

    async def test_a_duplicate_entry_does_not_run_the_job_twice(self, queue, monkeypatch):
        gate = asyncio.Event()
        runs = _stand_in(monkeypatch, gate)
        jid = await _job(queue)
        await sq.enqueue(jid)
        await sq.enqueue(jid)
        consumer = asyncio.create_task(sq.consume())
        await _until(lambda: runs)

        async def _drained():
            return await queue.redis.llen(sq.QUEUE) == 0
        await _until(_drained)
        await asyncio.sleep(0.1)
        assert len(runs) == 1, "the claim is held: the second entry is skipped"
        gate.set()
        await _until(lambda: not sq.running_ids())
        await sq.enqueue(jid)             # a late third entry finds the row no longer pending
        await _until(_drained)
        await asyncio.sleep(0.1)
        consumer.cancel()
        assert len(runs) == 1

    async def test_a_job_cancelled_before_a_worker_got_to_it_is_not_run(self, queue, monkeypatch, logs):
        runs = _stand_in(monkeypatch)
        jid = await _job(queue, status="cancelled")
        await sq.enqueue(jid)
        consumer = asyncio.create_task(sq.consume())

        async def _drained():
            return await queue.redis.llen(sq.QUEUE) == 0
        await _until(_drained)
        await asyncio.sleep(0.1)
        consumer.cancel()
        assert runs == [] and (await _row(jid)).status == "cancelled"
        assert await queue.redis.get(sq.CLAIM.format(jid)) is None, "the claim is let go at once"
        assert any("is cancelled — not running it" in m for m in logs)

    async def test_an_entry_that_is_not_a_job_id_is_dropped(self, rq, monkeypatch, logs):
        runs = _stand_in(monkeypatch)
        await rq.redis.lpush(sq.QUEUE, "not-a-uuid")
        consumer = asyncio.create_task(sq.consume())
        await _until(lambda: any("not a job id" in m for m in logs))
        await asyncio.sleep(0.05)
        assert not consumer.done() and runs == []
        consumer.cancel()


class TestTheConcurrencyCap:

    async def test_no_more_than_the_cap_run_at_once(self, queue, monkeypatch):
        monkeypatch.setattr(sq.settings, "scrape_worker_concurrency", 2)
        gate = asyncio.Event()
        runs = _stand_in(monkeypatch, gate)
        ids = [await _job(queue) for _ in range(3)]
        for jid in ids:
            await sq.enqueue(jid)
        consumer = asyncio.create_task(sq.consume())
        await _until(lambda: len(runs) == 2)
        await asyncio.sleep(0.2)
        assert len(sq.running_ids()) == 2 and len(runs) == 2
        assert await queue.redis.lrange(sq.QUEUE, 0, -1) == [ids[2]], "the third waits in the queue"
        gate.set()
        await _until(lambda: len(runs) == 3)
        assert [r["job_id"] for r in runs] == ids, "first in, first out"
        consumer.cancel()


class TestTheSweep:

    async def test_it_closes_out_only_runs_nobody_claims(self, queue):
        dead_running = await _job(queue, status="running")
        dead_paused = await _job(queue, status="paused")
        elsewhere = await _job(queue, status="running")
        mine = await _job(queue, status="running")
        done = await _job(queue, status="completed")
        waiting = await _job(queue, status="pending", age=timedelta(0))
        await queue.redis.set(sq.CLAIM.format(elsewhere), "another-worker", ex=90)
        sq._running[mine] = asyncio.get_running_loop().create_future()   # held here, claim not yet re-asserted

        res = await sq.sweep()
        sq._running.pop(mine).cancel()

        assert res["released"] == 2
        for jid in (dead_running, dead_paused):
            row = await _row(jid)
            assert row.status == "failed" and row.finish_reason == sq.ORPHAN_REASON
            assert "«ادامه»" in row.finish_reason and row.completed_at is not None
        assert (await _row(elsewhere)).status == "running"
        assert (await _row(mine)).status == "running"
        assert (await _row(done)).status == "completed"
        assert (await _row(waiting)).status == "pending"
        async with database.async_session_maker() as db:
            lines = (await db.execute(select(ScrapingLog).where(
                ScrapingLog.job_id == uuid.UUID(dead_paused)))).scalars().all()
        assert [(line.level, line.message) for line in lines] == [("error", sq.ORPHAN_LOG)], \
            "the run's own log says the process went away, not that it gave up"

    async def test_the_queue_is_rebuilt_after_redis_loses_it(self, queue):
        older = await _job(queue, age=timedelta(minutes=30))
        newer = await _job(queue, age=timedelta(minutes=20))
        young = await _job(queue, age=timedelta(seconds=5))
        claimed = await _job(queue, age=timedelta(minutes=40))
        await sq.enqueue(older)
        await queue.redis.flushdb()
        await queue.redis.set(sq.CLAIM.format(claimed), "another-worker", ex=90)

        assert (await sq.sweep())["requeued"] >= 2
        mine = [j for j in await queue.redis.lrange(sq.QUEUE, 0, -1) if j in queue.made]
        assert mine == [newer, older], "oldest first: BRPOP takes from the tail"
        assert young not in mine and claimed not in mine
        await sq.sweep()
        again = [j for j in await queue.redis.lrange(sq.QUEUE, 0, -1) if j in queue.made]
        assert again == mine, "an entry already queued is not queued twice"

    async def test_a_young_pending_row_is_left_to_its_launcher(self, queue):
        """It may still be on its way in — or, in the first rollout, running
        inside an api pod of the previous release, which never claims."""
        young = await _job(queue, age=timedelta(seconds=30))
        await sq.sweep()
        assert await queue.redis.lpos(sq.QUEUE, young) is None


class TestTheDrain:

    async def test_it_waits_for_the_run_and_takes_nothing_new(self, queue, monkeypatch, logs):
        monkeypatch.setattr(sq, "CLAIM_TTL", 2)
        monkeypatch.setattr(sq, "CLAIM_REFRESH", 0.1)
        monkeypatch.setattr(sq, "DRAIN_LOG_EVERY", 0.2)
        gate = asyncio.Event()
        runs = _stand_in(monkeypatch, gate)
        first = await _job(queue)
        await sq.enqueue(first)
        tasks = sq.start("worker")
        await _until(lambda: runs)

        drain = asyncio.create_task(sq.drain(tasks))
        await _until(lambda: all(t.done() for t in tasks))
        assert sq.draining and not drain.done()
        second = await _job(queue)
        await sq.enqueue(second)
        await asyncio.sleep(2.5)          # longer than the claim's TTL
        assert [r["job_id"] for r in runs] == [first], "nothing new is taken while draining"
        assert await queue.redis.lrange(sq.QUEUE, 0, -1) == [second], "left for the next worker"
        assert await queue.redis.get(sq.CLAIM.format(first)) == sq.WORKER_ID, \
            "the claim is kept alive through the drain"
        assert any("draining — waiting for 1 run(s)" in m for m in logs)
        assert not drain.done(), "no timeout of its own"

        gate.set()
        await asyncio.wait_for(drain, 5)
        assert (await _row(first)).status == "completed"
        assert await queue.redis.get(sq.CLAIM.format(first)) is None


class TestTheClaim:

    async def test_release_and_refresh_touch_only_our_own(self, rq):
        jid = str(uuid.uuid4())
        key = sq.CLAIM.format(jid)
        await rq.redis.set(key, "another-worker", ex=90)
        await sq.release(jid)
        assert await rq.redis.get(key) == "another-worker", "never delete a claim somebody else holds"
        assert not await sq._cas(rq.redis, jid, keep=True)
        await rq.redis.delete(key)                          # Redis lost it: ours again on the refresh
        assert await sq._cas(rq.redis, jid, keep=True)
        assert await rq.redis.get(key) == sq.WORKER_ID and 0 < await rq.redis.ttl(key) <= sq.CLAIM_TTL
        await sq.release(jid)
        assert await rq.redis.get(key) is None


class TestWhatTheApiSays:

    async def test_active_tasks_keeps_its_shape_from_the_claims(self, rq):
        a, b = str(uuid.uuid4()), str(uuid.uuid4())
        await rq.redis.set(sq.CLAIM.format(a), "w1", ex=90)
        await rq.redis.set(sq.CLAIM.format(b), "w2", ex=90)
        assert await routes.get_active_tasks() == {"active_count": 2, "task_ids": sorted([a, b])}

    async def test_with_redis_down_the_launch_still_answers_and_the_row_waits(self, queue, monkeypatch, logs):
        async def _down():
            raise ConnectionError("redis is down")
        monkeypatch.setattr(database, "get_redis", _down)
        async with database.async_session_maker() as db:
            resp = await routes._launch_job(
                ScrapingJobCreate(city="urmia", category="rent-apartment"), db, SimpleNamespace(id=3))
        queue.made.append(resp.job_id)
        assert resp.status == "pending" and resp.job_id
        assert (await _row(resp.job_id)).status == "pending"
        assert any("could not queue" in m and "sweep" in m for m in logs)

    async def test_the_saved_config_is_what_the_worker_reads(self, queue):
        """«ادامه» and the worker read the same column."""
        async with database.async_session_maker() as db:
            resp = await routes._launch_job(
                ScrapingJobCreate(city="urmia", category="rent-apartment", max_items=5),
                db, SimpleNamespace(id=11))
        queue.made.append(resp.job_id)
        kw = sq.job_kwargs(await _row(resp.job_id))
        assert kw["owner_user_id"] == 11 and kw["max_items"] == 5 and kw["job_id"] == resp.job_id
        assert json.dumps(kw)                   # nothing in it that a later change could not store
