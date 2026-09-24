"""
match_service's reason and semantic-candidate cache — Redis-backed, never
computed on the request that is waiting for a page.

Unit-level, against fakeredis directly: what changes the cache key, the lock
that makes a burst of page loads cost one model call, and that a Redis outage
costs the extras quietly rather than the request. The route itself, with a
real (slow) model call, is proven end to end in test_match_reasons_endpoint.py.
"""
import asyncio
import os
import sys

import fakeredis.aioredis
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_match_reasons_cache.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

import app.database as db                        # noqa: E402
from app.services import match_service as m       # noqa: E402


@pytest.fixture
def redis(monkeypatch):
    """match_service resolves get_redis fresh from app.database on every
    call (a local import), so patching the module attribute is enough —
    the same trick the app itself uses in tests (see test_portal_bridge.py)."""
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)

    async def _get():
        return fake
    monkeypatch.setattr(db, "get_redis", _get)
    return fake


def _row(id_, score=80, price=1_000_000_000, **kw):
    base = dict(id=id_, title=f"ملک {id_}", area=90, rooms=2, price=price,
               district="گلها", city_name="ارومیه", score=score)
    base.update(kw)
    return base


async def _drain(before):
    """Await whatever _attach_reasons/_cached_semantic_candidates just
    scheduled, tolerant of leftover tasks from an earlier test."""
    new = set(m._background_tasks) - before
    assert len(new) == 1, f"expected exactly one new background call, found {len(new)}"
    await next(iter(new))


class TestReasons:

    def test_a_miss_never_blocks_and_a_later_call_is_served_from_cache(self, redis, monkeypatch):
        calls = []

        async def fake_rerank(prompt_items, context):
            calls.append((tuple(i["id"] for i in prompt_items), context))
            return {i["id"]: f"دلیل {i['id']}" for i in prompt_items}
        monkeypatch.setattr(m, "_llm_rerank", fake_rerank)

        async def _go():
            results = [_row(1), _row(2)]
            before = set(m._background_tasks)
            pending = await m._attach_reasons("property", 101, results, "ctx-1")
            assert pending is True
            assert all("ai_reason" not in r for r in results), "never computed inline"
            await _drain(before)
            assert len(calls) == 1

            results2 = [_row(1), _row(2)]   # fresh dicts — nothing carried over by reference
            pending2 = await m._attach_reasons("property", 101, results2, "ctx-1")
            assert pending2 is False
            assert results2[0]["ai_reason"] == "دلیل 1" and results2[1]["ai_reason"] == "دلیل 2"
            assert len(calls) == 1, "served from Redis — no second model call"
        asyncio.run(_go())

    def test_a_burst_for_the_same_rows_makes_one_call(self, redis, monkeypatch):
        calls = []

        async def slow_rerank(prompt_items, context):
            # slow enough that all 5 misses land before this finishes, so the
            # lock is provably still held when the later callers check it —
            # not just "usually fast enough"
            await asyncio.sleep(0.05)
            calls.append(1)
            return {i["id"]: "دلیل" for i in prompt_items}
        monkeypatch.setattr(m, "_llm_rerank", slow_rerank)

        async def _go():
            before = set(m._background_tasks)
            pendings = [await m._attach_reasons("property", 202, [_row(1)], "ctx") for _ in range(5)]
            assert all(pendings), "every caller in the burst is told to check back"
            new = set(m._background_tasks) - before
            assert len(new) == 1, "the lock let only the first caller schedule a call"
            await next(iter(new))
            assert len(calls) == 1
        asyncio.run(_go())

    def test_a_changed_candidate_set_is_a_miss_not_a_stale_answer(self, redis, monkeypatch):
        calls = []

        async def fake_rerank(prompt_items, context):
            calls.append(1)
            return {i["id"]: "دلیل" for i in prompt_items}
        monkeypatch.setattr(m, "_llm_rerank", fake_rerank)

        async def _go():
            before = set(m._background_tasks)
            await m._attach_reasons("property", 303, [_row(1, score=80)], "ctx")
            await _drain(before)
            assert len(calls) == 1

            # the same candidate, a different score: a different fingerprint
            before2 = set(m._background_tasks)
            pending = await m._attach_reasons("property", 303, [_row(1, score=55)], "ctx")
            assert pending is True, "the score moved — must not serve the old sentence"
            assert len(set(m._background_tasks) - before2) == 1
        asyncio.run(_go())

    def test_redis_down_is_quiet_not_pending(self, monkeypatch):
        async def boom():
            raise ConnectionError("redis is down")
        monkeypatch.setattr(db, "get_redis", boom)

        async def _go():
            results = [_row(1)]
            pending = await m._attach_reasons("property", 404, results, "ctx")
            assert pending is False, "no cache, no lock, no promise to check back"
            assert "ai_reason" not in results[0]
        asyncio.run(_go())

    def test_no_rows_is_a_no_op(self, redis):
        assert asyncio.run(m._attach_reasons("property", 1, [], "ctx")) is False


class TestSemanticCandidates:

    def test_a_miss_never_blocks_and_a_later_call_is_served_from_cache(self, redis, monkeypatch):
        import app.ai.embeddings as emb
        calls = []

        async def fake_semantic(session, need, *, city, listing_type, limit):
            calls.append((need, city, listing_type, limit))
            return [(11, 0.9), (12, 0.8)]
        monkeypatch.setattr(emb, "semantic_candidates", fake_semantic)

        async def _go():
            before = set(m._background_tasks)
            sem = await m._cached_semantic_candidates("۱۰۰ متر طرف گلها", "ارومیه", "buy")
            assert sem == {}, "nothing cached yet — a miss is quiet, not a wait"
            await _drain(before)
            assert calls == [("۱۰۰ متر طرف گلها", "ارومیه", "buy", m.SEMANTIC_EXTRA)]

            sem2 = await m._cached_semantic_candidates("۱۰۰ متر طرف گلها", "ارومیه", "buy")
            assert sem2 == {11: 0.9, 12: 0.8}
            assert len(calls) == 1, "served from cache"
        asyncio.run(_go())

    def test_a_different_need_or_city_is_a_different_cache_entry(self, redis, monkeypatch):
        import app.ai.embeddings as emb
        calls = []

        async def fake_semantic(session, need, *, city, listing_type, limit):
            calls.append(1)
            return []
        monkeypatch.setattr(emb, "semantic_candidates", fake_semantic)

        async def _go():
            before = set(m._background_tasks)
            await m._cached_semantic_candidates("نیاز الف", "ارومیه", "buy")
            assert len(set(m._background_tasks) - before) == 1
            before2 = set(m._background_tasks)
            await m._cached_semantic_candidates("نیاز ب", "ارومیه", "buy")
            assert len(set(m._background_tasks) - before2) == 1, "a different need text schedules its own call"
        asyncio.run(_go())

    def test_llm_error_leaves_no_cache_entry_and_is_quiet(self, redis, monkeypatch):
        import app.ai.embeddings as emb
        from app.services import llm as _llm

        async def fail(session, need, *, city, listing_type, limit):
            raise _llm.BudgetExceeded("سقف پر شد")
        monkeypatch.setattr(emb, "semantic_candidates", fail)

        async def _go():
            before = set(m._background_tasks)
            sem = await m._cached_semantic_candidates("x", "ارومیه", "buy")
            assert sem == {}
            await _drain(before)   # must not raise out of the background task
            # a later call tries again rather than serving a poisoned empty cache
            before2 = set(m._background_tasks)
            sem2 = await m._cached_semantic_candidates("x", "ارومیه", "buy")
            assert sem2 == {}
            assert len(set(m._background_tasks) - before2) == 1
        asyncio.run(_go())

    def test_redis_down_is_quiet(self, monkeypatch):
        async def boom():
            raise ConnectionError("redis is down")
        monkeypatch.setattr(db, "get_redis", boom)
        assert asyncio.run(m._cached_semantic_candidates("x", "ارومیه", "buy")) == {}


class TestThroughTheRealFunctions:
    """similar_to_property and matches_for_customer themselves — that they
    return (results, pending) and that pending really means the ranking
    already shipped, not that anything waited. Property is the only table
    needed (matches_for_customer takes its customer as a plain object, never
    queried — a bare, unpersisted Customer instance is enough)."""

    @pytest.fixture
    def engine(self):
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy.pool import StaticPool
        from app.models.property import Property
        eng = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
                                  connect_args={"check_same_thread": False})

        async def _make():
            async with eng.begin() as conn:
                await conn.run_sync(Property.metadata.create_all, tables=[Property.__table__])
        asyncio.run(_make())
        return eng

    def _seed_properties(self, eng):
        from sqlalchemy.ext.asyncio import async_sessionmaker
        from app.models.property import Property
        maker = async_sessionmaker(eng, expire_on_commit=False)

        async def _go():
            async with maker() as s:
                target = Property(tag_number="mf-1", divar_id="mf-1", url="#", title="آپارتمان ۱۰۰ متری خیابان گلها",
                                  city_name="ارومیه", district="خیابان گلها", area=100, rooms=2,
                                  property_type="آپارتمان", listing_type="buy", total_price=4_500_000_000,
                                  is_active=True)
                cand = Property(tag_number="mf-2", divar_id="mf-2", url="#", title="آپارتمان ۹۵ متری خیابان گلها",
                                city_name="ارومیه", district="خیابان گلها", area=95, rooms=2,
                                property_type="آپارتمان", listing_type="buy", total_price=4_400_000_000,
                                is_active=True)
                s.add_all([target, cand])
                await s.commit()
                return target.id, cand.id
        return asyncio.run(_go())

    def test_similar_to_property_reports_pending_without_waiting(self, engine, redis, monkeypatch):
        from sqlalchemy.ext.asyncio import async_sessionmaker
        from app.models.property import Property
        target_id, _cand_id = self._seed_properties(engine)

        async def fake_rerank(prompt_items, context):
            return {i["id"]: "دلیل" for i in prompt_items}
        monkeypatch.setattr(m, "_llm_rerank", fake_rerank)

        async def _go():
            maker = async_sessionmaker(engine, expire_on_commit=False)
            before = set(m._background_tasks)
            async with maker() as s:
                target = await s.get(Property, target_id)
                results, pending = await m.similar_to_property(s, target, limit=5)
            assert pending is True
            assert results and all("ai_reason" not in r for r in results)
            await asyncio.gather(*(set(m._background_tasks) - before))

            async with maker() as s:
                target = await s.get(Property, target_id)
                results2, pending2 = await m.similar_to_property(s, target, limit=5)
            assert pending2 is False
            assert any(r.get("ai_reason") for r in results2)
        asyncio.run(_go())

    def test_matches_for_customer_reports_pending_without_waiting(self, engine, redis, monkeypatch):
        from sqlalchemy.ext.asyncio import async_sessionmaker
        from app.models.crm_models import Customer
        self._seed_properties(engine)

        async def fake_rerank(prompt_items, context):
            return {i["id"]: "دلیل" for i in prompt_items}
        monkeypatch.setattr(m, "_llm_rerank", fake_rerank)
        # never persisted — matches_for_customer only reads attributes off it
        customer = Customer(id=555, full_name="مشتری آزمایشی", desired_city="ارومیه",
                            desired_type="apartment", deal_type="buy", budget_max=5_000_000_000)

        async def _go():
            maker = async_sessionmaker(engine, expire_on_commit=False)
            before = set(m._background_tasks)
            async with maker() as s:
                results, pending = await m.matches_for_customer(s, customer, limit=5)
            assert pending is True
            assert results and all("ai_reason" not in r for r in results)
            await asyncio.gather(*(set(m._background_tasks) - before))

            async with maker() as s:
                results2, pending2 = await m.matches_for_customer(s, customer, limit=5)
            assert pending2 is False
            assert any(r.get("ai_reason") for r in results2)
        asyncio.run(_go())
