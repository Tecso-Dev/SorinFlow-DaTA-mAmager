"""
درخواست پرتال → تحلیل نیاز در پس‌زمینه.

create_request used to call need_parser.enrich_request inline (see
test_portal_bridge.py for proof that it no longer does). What is left is
portal_bridge.enrich_needs — the background pass, run by the engine's own
tick through sync_open — and its own bookkeeping: a request is read exactly
once, a real failure spends one of 3 tries, and a gateway-state failure (not
configured, disabled, over budget) spends none. What enrich_request itself
reads out of a description is covered in test_ai_need.py; this file fakes it.
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_need_enrich_unused.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.crm import portal_bridge as pb          # noqa: E402
from app.ai import need_parser                   # noqa: E402
from app.database import Base                     # noqa: E402
from app.services import llm                       # noqa: E402
from app.models.crm_models import Customer         # noqa: E402
from app.models.portal import PropertyRequest       # noqa: E402


def _engine():
    """A fresh in-memory database, alive for exactly one test (StaticPool
    keeps the one connection — and its data — across calls). Only the two
    tables enrich_needs touches: the full metadata has Postgres-only column
    types (e.g. scraping_jobs.job_id, UUID) that sqlite cannot create."""
    eng = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
                              connect_args={"check_same_thread": False})

    async def _make():
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all,
                                tables=[Customer.__table__, PropertyRequest.__table__])
    asyncio.run(_make())
    return eng


def _seed(eng, *, description="طرف گلها، پارکینگ حتماً", customer_kw=None):
    """A customer built from a bare form, and the request it came from —
    exactly what create_request leaves behind now (no enrichment yet)."""
    maker = async_sessionmaker(eng, expire_on_commit=False)

    async def _go():
        async with maker() as s:
            cust = Customer(full_name="کاربر پرتال", source="portal", temperature="warm",
                            deal_type="buy", **(customer_kw or {}))
            s.add(cust)
            await s.flush()
            # user_id has no matching row — sqlite does not enforce the FK,
            # and nothing here reads the user
            req = PropertyRequest(user_id=1, deal_type="buy", description=description,
                                  customer_id=cust.id, status="new")
            s.add(req)
            await s.commit()
            return cust.id, req.id
    return asyncio.run(_go())


def _run_pass(eng):
    """One engine tick's worth of enrich_needs, on its own session — the way
    sync_open really calls it."""
    maker = async_sessionmaker(eng, expire_on_commit=False)

    async def _go():
        async with maker() as s:
            return await pb.enrich_needs(s)
    return asyncio.run(_go())


def _fetch(eng, model, id_):
    maker = async_sessionmaker(eng, expire_on_commit=False)

    async def _go():
        async with maker() as s:
            return await s.get(model, id_)
    return asyncio.run(_go())


class TestExactlyOnce:

    def test_success_fills_only_what_was_empty_and_never_runs_again(self, monkeypatch):
        eng = _engine()
        cust_id, req_id = _seed(eng, customer_kw={"desired_city": "ارومیه"})
        calls = []

        async def fake(db, req):
            calls.append(req.id)
            return {"desired_city": "شهر دیگر", "desired_district": "گلها", "red_lines": "طبقهٔ اول"}
        monkeypatch.setattr(need_parser, "enrich_request", fake)

        done = _run_pass(eng)
        assert done == 1 and calls == [req_id]

        cust = _fetch(eng, Customer, cust_id)
        assert cust.desired_city == "ارومیه", "already known from the form — the model does not overrule it"
        assert cust.desired_district == "گلها" and cust.red_lines == "طبقهٔ اول", "these were empty"

        req = _fetch(eng, PropertyRequest, req_id)
        assert req.need_enriched_at is not None and req.need_enrich_attempts == 0

        # a second pass must not read it again
        def boom(db, req):
            raise AssertionError("a request already enriched must not be read again")
        monkeypatch.setattr(need_parser, "enrich_request", boom)
        assert _run_pass(eng) == 0

    def test_no_description_is_marked_done_without_a_call(self):
        eng = _engine()
        _cust_id, req_id = _seed(eng, description=None)
        assert _run_pass(eng) == 0, "nothing was added to the customer, so it does not count as done"
        req = _fetch(eng, PropertyRequest, req_id)
        assert req.need_enriched_at is not None, "still marked — an empty description never changes"

    def test_a_deleted_customer_is_marked_done_without_crashing(self, monkeypatch):
        eng = _engine()
        cust_id, req_id = _seed(eng)

        def boom(db, req):
            raise AssertionError("no customer to enrich — must not reach the model")
        monkeypatch.setattr(need_parser, "enrich_request", boom)

        async def _delete_customer():
            maker = async_sessionmaker(eng, expire_on_commit=False)
            async with maker() as s:
                cust = await s.get(Customer, cust_id)
                await s.delete(cust)
                await s.commit()
        asyncio.run(_delete_customer())

        assert _run_pass(eng) == 0
        req = _fetch(eng, PropertyRequest, req_id)
        assert req.need_enriched_at is not None


class TestRetries:

    def test_real_failures_cost_an_attempt_and_stop_after_three(self, monkeypatch):
        eng = _engine()
        _cust_id, req_id = _seed(eng)
        calls = []

        async def fail(db, req):
            calls.append(1)
            raise RuntimeError("the gateway answered garbage")
        monkeypatch.setattr(need_parser, "enrich_request", fail)

        for n in (1, 2, 3):
            done = _run_pass(eng)
            assert done == 0
            req = _fetch(eng, PropertyRequest, req_id)
            assert req.need_enrich_attempts == n
        assert req.need_enriched_at is not None, "3 real failures — stop trying"
        assert len(calls) == 3

        # a 4th pass must not call the model again
        assert _run_pass(eng) == 0
        assert len(calls) == 3

    def test_gateway_state_failures_cost_nothing_and_are_retried_forever(self, monkeypatch):
        eng = _engine()
        _cust_id, req_id = _seed(eng)
        errors = iter([llm.NotConfigured("خاموش"), llm.Disabled("خاموش از پنل"),
                      llm.BudgetExceeded("سقف پر شد"), llm.CircuitOpen("مسیر موقتاً متوقف است"),
                      llm.RateLimited("HTTP 429", 30.0), llm.NotConfigured("باز هم")])
        calls = []

        async def deferred(db, req):
            calls.append(1)
            raise next(errors)
        monkeypatch.setattr(need_parser, "enrich_request", deferred)

        for _ in range(6):
            assert _run_pass(eng) == 0
        req = _fetch(eng, PropertyRequest, req_id)
        assert req.need_enrich_attempts == 0, "not configured/disabled/over budget/an open breaker is not this request's fault"
        assert req.need_enriched_at is None, "worth trying again once the gateway is usable"
        assert len(calls) == 6, "no cap — a gateway-state error is retried every pass"


    def test_a_closed_request_is_not_read(self, monkeypatch):
        """The pass is for requests someone may still act on."""
        eng = _engine()
        _cust_id, req_id = _seed(eng)

        async def close():
            from sqlalchemy.ext.asyncio import async_sessionmaker
            async with async_sessionmaker(eng, expire_on_commit=False)() as db:
                (await db.get(PropertyRequest, req_id)).status = "done"
                await db.commit()
        asyncio.run(close())
        calls = []

        async def read(db, req):
            calls.append(1)
            return {"desired_district": "گلها"}
        monkeypatch.setattr(need_parser, "enrich_request", read)
        assert _run_pass(eng) == 0 and calls == []


@pytest.mark.skipif(not os.environ.get("DATABASE_URL", "").startswith("postgresql"),
                    reason="the boot step is Postgres SQL, like the others in app/database.py")
def test_the_boot_step_marks_the_requests_already_there_as_read():
    """They were read on the visitor's own request before this release; the
    background pass must not read the whole history again."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    import app.database as database
    schema = "sf_needmark"

    async def _go():
        eng = create_async_engine(os.environ["DATABASE_URL"],
                                  connect_args={"server_settings": {"search_path": schema}})
        async with eng.begin() as c:
            await c.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
            await c.execute(text(f"CREATE SCHEMA {schema}"))
            await c.execute(text("CREATE TABLE portal_property_requests (id SERIAL PRIMARY KEY, status TEXT)"))
            await c.execute(text("INSERT INTO portal_property_requests (status) VALUES ('new'), ('done')"))
        async with eng.begin() as c:
            await database._migrate_portal_need_enrich(c)
        async with eng.begin() as c:
            await c.execute(text("INSERT INTO portal_property_requests (status) VALUES ('new')"))
            rows = (await c.execute(text("SELECT id, need_enriched_at IS NOT NULL, need_enrich_attempts "
                                         "FROM portal_property_requests ORDER BY id"))).all()
            await c.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        await eng.dispose()
        return rows

    rows = asyncio.run(_go())
    assert [tuple(r) for r in rows] == [(1, True, 0), (2, True, 0), (3, False, 0)]
