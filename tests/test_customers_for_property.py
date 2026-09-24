"""
customers_for_property — which of our customers were looking for this?

The old picker scored only the newest CANDIDATE_POOL (300) customers, so a
customer added before the table passed 300 rows silently stopped being
offered anything, no matter how well a new listing fit them. This scores
everyone who could possibly want the listing, with a SQL prefilter for what
customer_wants would reject outright (an explicit deal type, city or
property family that conflicts) so a single-listing call still stays cheap.
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_customers_for_property.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

import pytest  # noqa: E402

from app.models.crm_models import Customer  # noqa: E402
from app.models.property import Property  # noqa: E402
from app.services import match_service as m  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def maker(tmp_path):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool
    from app.database import Base
    tables = [Customer.__table__, Property.__table__]
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/cfp.db", poolclass=NullPool)

    async def _build():
        async with eng.begin() as c:
            await c.run_sync(lambda sc: Base.metadata.create_all(sc, tables=tables))
    asyncio.run(_build())
    yield async_sessionmaker(eng, expire_on_commit=False)
    asyncio.run(eng.dispose())


def _prop(i=1, **kw):
    base = dict(tag_number=f"c{i}", divar_id=f"c{i}", url=f"https://divar.ir/v/c{i}",
                title="آپارتمان ۱۰۰ متری خیابان گلها", city_name="ارومیه", district="خیابان گلها",
                property_type="آپارتمان", listing_type="buy", total_price=4_000_000_000,
                area=100, rooms=2, is_active=True, serial_no=1000 + i)
    base.update(kw)
    return Property(**base)


def _cust(i, **kw):
    base = dict(full_name=f"مشتری {i}", mobile1=f"0912000{i:04d}", desired_city="ارومیه",
                desired_district="خیابان گلها", desired_type="apartment", deal_type="buy",
                budget_max=5_000_000_000)
    base.update(kw)
    return Customer(**base)


async def _run(maker, fn):
    async with maker() as s:
        return await fn(s)


class TestEveryCustomerIsScored:

    def test_a_customer_past_the_old_300_cap_still_matches(self, maker):
        async def scenario(s):
            # 305 filler customers with nothing in common with the listing
            # (a different city), then one old, low-id customer who fits —
            # the OLD ORDER BY id DESC LIMIT 300 would never have reached it
            s.add_all([_cust(i, desired_city="تبریز", full_name=f"filler {i}") for i in range(305)])
            fit = _cust(9999, full_name="اولین مشتری")
            s.add(fit)
            await s.commit()
            p = _prop()
            s.add(p)
            await s.commit()
            return await m.customers_for_property(s, p, limit=5, use_llm=False), fit.id
        results, fit_id = asyncio.run(_run(maker, scenario))
        assert any(r["id"] == fit_id for r in results), "the fix: not only the newest 300 rows"

    def test_the_sql_prefilter_matches_customer_wants_exactly(self, maker):
        """A customer the SQL prefilter would drop is one customer_wants
        would have scored 0 for anyway — same result, fewer rows loaded."""
        async def scenario(s):
            fits_city = _cust(1, desired_city="ارومیه")
            wrong_city = _cust(2, desired_city="تبریز")
            wrong_deal = _cust(3, deal_type="rent")
            wrong_family = _cust(4, desired_type="shop")
            blank = _cust(5, desired_city=None, desired_type=None, deal_type=None)
            s.add_all([fits_city, wrong_city, wrong_deal, wrong_family, blank])
            await s.commit()
            p = _prop()
            s.add(p)
            await s.commit()
            prefiltered = {c.id for c, _ in await m._customer_candidates(s, p)}
            full_pass = {c["id"] for c in await m.customers_for_property(s, p, limit=50, use_llm=False)}
            return prefiltered, full_pass, {fits_city.id, blank.id}
        prefiltered, full_pass, should_score = asyncio.run(_run(maker, scenario))
        assert should_score <= prefiltered, "an explicit-nothing customer is never dropped before the Python gate"
        assert full_pass == should_score, "the ones that actually want it, exactly"


class TestPreloadCustomers:

    def test_preloaded_customers_score_the_same_as_a_fresh_query(self, maker):
        async def scenario(s):
            s.add_all([_cust(1), _cust(2, desired_city="تبریز"), _cust(3, budget_max=100_000_000)])
            await s.commit()
            p = _prop()
            s.add(p)
            await s.commit()
            fresh = await m.customers_for_property(s, p, limit=10, use_llm=False)
            preloaded = await m.preload_customers(s)
            reused = await m.customers_for_property(s, p, limit=10, use_llm=False, customers=preloaded)
            return fresh, reused
        fresh, reused = asyncio.run(_run(maker, scenario))
        assert fresh == reused

    def test_preload_runs_one_query_for_the_whole_pass(self, maker, monkeypatch):
        """The point of preload_customers: app/crm/match_engine.py calls it
        once per pass, not once per listing."""
        async def scenario(s):
            s.add_all([_cust(1), _cust(2)])
            props = [_prop(1), _prop(2), _prop(3)]
            s.add_all(props)
            await s.commit()
            calls = []
            real = s.execute

            async def counting(*a, **kw):
                calls.append(1)
                return await real(*a, **kw)
            monkeypatch.setattr(s, "execute", counting)
            preloaded = await m.preload_customers(s)
            queries_for_preload = len(calls)
            for p in props:
                await m.customers_for_property(s, p, limit=5, use_llm=False, customers=preloaded)
            return queries_for_preload, len(calls) - queries_for_preload
        preload_queries, extra_queries_for_3_listings = asyncio.run(_run(maker, scenario))
        assert preload_queries == 1
        assert extra_queries_for_3_listings == 0, "no customer query at all once the list is preloaded"


class TestSignatureIsUnchanged:

    def test_the_call_shape_scripts_and_price_watch_use_still_works(self):
        import inspect
        sig = inspect.signature(m.customers_for_property)
        assert list(sig.parameters)[:4] == ["db", "prop", "limit", "use_llm"]
        assert sig.parameters["limit"].default == 12 and sig.parameters["use_llm"].default is True

    def test_price_watch_and_the_engine_call_it_unmodified(self):
        pw = (ROOT / "app/crm/price_watch.py").read_text(encoding="utf-8")
        assert "customers_for_property(db, p, limit=MATCH_PER_LISTING, use_llm=False)" in pw
