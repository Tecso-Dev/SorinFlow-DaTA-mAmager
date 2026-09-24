"""
app/crm/match_engine.py's _due() — who gets judged, and when.

Before this, run_once walked a pure id cursor: a listing was judged once,
ever, the moment it first appeared, whether or not the reader had reached
it yet — and never again if its content changed afterwards. This is a fast,
sqlite-only look at _due() and run_once() directly (test_match_engine.py's
own TestThroughTheApp already covers the end-to-end Postgres path).
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_match_engine_staleness.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

import pytest  # noqa: E402

from app.ai import listing_reader  # noqa: E402
from app.crm import match_engine as me  # noqa: E402
from app.models.crm_models import Customer, CustomerMatch  # noqa: E402
from app.models.portal import PropertyRequest  # noqa: E402
from app.models.property import Property  # noqa: E402
from app.services import llm  # noqa: E402


@pytest.fixture
def maker(tmp_path):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool
    from app.database import Base
    from app.models.app_setting import AppSetting
    tables = [Property.__table__, Customer.__table__, CustomerMatch.__table__,
             PropertyRequest.__table__, AppSetting.__table__]
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/me.db", poolclass=NullPool)

    async def _build():
        async with eng.begin() as c:
            await c.run_sync(lambda sc: Base.metadata.create_all(sc, tables=tables))
    asyncio.run(_build())
    yield async_sessionmaker(eng, expire_on_commit=False)
    asyncio.run(eng.dispose())


def _prop(i=1, **kw):
    base = dict(tag_number=f"m{i}", divar_id=f"m{i}", url=f"https://divar.ir/v/m{i}",
                title="آپارتمان ۱۰۰ متری خیابان گلها", city_name="ارومیه", district="خیابان گلها",
                property_type="آپارتمان", listing_type="buy", total_price=4_000_000_000,
                area=100, rooms=2, is_active=True, serial_no=2000 + i)
    base.update(kw)
    return Property(**base)


async def _run(maker, fn):
    async with maker() as s:
        return await fn(s)


class TestReaderUnconfigured:
    """No LLM key set (the default in every test env here): reader_will_run
    is False, so waiting for a read is pointless and a fresh listing is
    judged on the first pass — the same thing the Postgres integration test
    (test_match_engine.py) proves through the real app."""

    def test_a_never_read_listing_is_judged_right_away(self, maker):
        async def scenario(s):
            p = _prop()
            s.add(p)
            await s.commit()
            out = await me.run_once(s, notify=False)
            row = await s.get(Property, p.id)
            return out, row.ai_matched_at, row.ai_match_fp
        out, matched_at, match_fp = asyncio.run(_run(maker, scenario))
        assert out["scanned"] == 1 and matched_at is not None and match_fp is not None

    def test_a_settled_listing_is_not_rescanned(self, maker):
        async def scenario(s):
            p = _prop()
            s.add(p)
            await s.commit()
            first = await me.run_once(s, notify=False)
            again = await me.run_once(s, notify=False)
            return first, again
        first, again = asyncio.run(_run(maker, scenario))
        assert first["scanned"] == 1 and again["scanned"] == 0

    def test_a_content_edit_reopens_a_judged_listing(self, maker):
        async def scenario(s):
            p = _prop()
            s.add(p)
            await s.commit()
            await me.run_once(s, notify=False)
            p2 = await s.get(Property, p.id)
            p2.description = "یک توضیح کاملاً تازه"
            await s.commit()
            again = await me.run_once(s, notify=False)
            return again
        again = asyncio.run(_run(maker, scenario))
        assert again["scanned"] == 1, "the content changed, so it is due again"


class TestReaderConfigured:
    """With the reader able to run, judging waits for a read at the current
    version — unless this listing is past MAX_ATTEMPTS on its current
    content, or the safety window has passed."""

    @pytest.fixture(autouse=True)
    def _configured(self, monkeypatch):
        monkeypatch.setattr(llm.settings, "llm_api_key", "k-test", raising=False)
        monkeypatch.setattr(llm.settings, "llm_base_url", "https://ai.liara.ir/api/x/v1", raising=False)

        async def spent(_db):
            return 0.0
        monkeypatch.setattr(llm, "spent_today", spent)

    def test_an_unread_listing_waits_for_the_reader(self, maker):
        async def scenario(s):
            p = _prop()
            s.add(p)
            await s.commit()
            return await me.run_once(s, notify=False)
        out = asyncio.run(_run(maker, scenario))
        assert out["scanned"] == 0, "the reader has not had its turn yet"

    def test_a_listing_read_at_the_current_version_is_judged(self, maker):
        async def scenario(s):
            p = _prop()
            s.add(p)
            await s.commit()
            p.ai_facts = {"kind": "apartment", "prompt_version": listing_reader.PROMPT_VERSION}
            p.ai_read_at = datetime.now(timezone.utc)
            await s.commit()
            return await me.run_once(s, notify=False)
        out = asyncio.run(_run(maker, scenario))
        assert out["scanned"] == 1

    def test_a_listing_the_reader_gave_up_on_is_judged_anyway(self, maker):
        async def scenario(s):
            p = _prop()
            s.add(p)
            await s.commit()
            p2 = await s.get(Property, p.id)
            p2.ai_read_attempts = listing_reader.MAX_ATTEMPTS
            p2.ai_read_fp = p2.ai_content_fp     # stuck on the content that is still on the row
            await s.commit()
            return await me.run_once(s, notify=False)
        out = asyncio.run(_run(maker, scenario))
        assert out["scanned"] == 1

    def test_capped_but_on_stale_content_still_waits(self, maker):
        """Attempts were exhausted against OLD content; the content since
        changed, so the reader gets a fresh shot before the matcher judges
        it — matching listing_reader's own reset-on-change rule."""
        async def scenario(s):
            p = _prop()
            s.add(p)
            await s.commit()
            p2 = await s.get(Property, p.id)
            p2.ai_read_attempts = listing_reader.MAX_ATTEMPTS
            p2.ai_read_fp = "some-old-fingerprint-not-matching-current"
            await s.commit()
            return await me.run_once(s, notify=False)
        out = asyncio.run(_run(maker, scenario))
        assert out["scanned"] == 0

    def test_the_safety_timeout_judges_a_stuck_listing_regardless(self, maker, monkeypatch):
        monkeypatch.setattr(me, "READ_WAIT_TIMEOUT", timedelta(seconds=0))

        async def scenario(s):
            p = _prop()
            s.add(p)
            await s.commit()
            return await me.run_once(s, notify=False)
        out = asyncio.run(_run(maker, scenario))
        assert out["scanned"] == 1, "created_at is always in the past, so a zero timeout always fires"


class TestRunOnceKeepsItsShape:

    def test_the_return_keys_are_unchanged(self, maker):
        async def scenario(s):
            return await me.run_once(s, notify=False)
        out = asyncio.run(_run(maker, scenario))
        assert set(out) == {"scanned", "matched", "cursor"}

    def test_dedup_still_skips_an_existing_row(self, maker):
        """The (listing, customer) de-duplication is untouched: judging the
        same listing twice (via a content edit) does not double the match."""
        async def scenario(s):
            p = _prop()
            c = Customer(full_name="مشتری", mobile1="09120000000", desired_city="ارومیه",
                        desired_district="خیابان گلها", desired_type="apartment", deal_type="buy",
                        budget_max=5_000_000_000)
            s.add_all([p, c])
            await s.commit()
            first = await me.run_once(s, notify=False)
            p2 = await s.get(Property, p.id)
            p2.description = "توضیح تازه برای دور دوم"
            await s.commit()
            second = await me.run_once(s, notify=False)
            from sqlalchemy import select, func
            total = (await s.execute(select(func.count(CustomerMatch.id)))).scalar_one()
            return first, second, total
        first, second, total = asyncio.run(_run(maker, scenario))
        assert first["matched"] == 1 and second["matched"] == 0, "re-judged, not re-matched"
        assert total == 1, "still one row for this (listing, customer)"
