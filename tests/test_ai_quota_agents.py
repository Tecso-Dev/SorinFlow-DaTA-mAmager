"""
The agents stop asking while the gateway has no credit — and say so once (#35).

llm.py (test_ai_quota_pause.py) pauses every agent when the gateway says the
credit, the quota or the balance is used up. What is held here is what each
agent does about it: the pass that meets the refusal ends at the first listing
and charges none an attempt; while the pause lasts a pass asks nothing — the
photo tagger does not even fetch a photo from Divar for a listing nobody can
tag — and the log gets one note for the whole pause, not one line per listing.
And a refusal that is NOT about credit now names its cause where the log used
to say only «HTTP …».

Listings, settings and ledger are a sqlite file of each test's own; the gateway
is an httpx.MockTransport. The fakes are in _ai_quota_kit.py.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_ai_quota_agents.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from sqlalchemy import select                                                # noqa: E402

from _ai_quota_kit import (                                                  # noqa: E402
    GATEWAY_WORDS, GOOD_FACTS, OUT_OF_CREDIT, _answer, _gateway, _pause_now, _refuse, _seed_photo_listings,
    _seed_text_listings, capture_logs, open_world, reset_process,
)
from app.ai import embeddings as emb                                         # noqa: E402
from app.ai import listing_reader as reader                                  # noqa: E402
from app.ai import photo_tagger as pt                                        # noqa: E402
from app.models.property import Property                                     # noqa: E402
from app.services import llm                                                 # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_process():
    reset_process()


@pytest.fixture
def logs():
    with capture_logs() as lines:
        yield lines


@pytest.fixture
async def world(monkeypatch, tmp_path):
    async with open_world(monkeypatch, tmp_path) as maker:
        yield maker


class TestThePhotoTagger:

    @pytest.fixture
    async def listings(self, world, monkeypatch, tmp_path):
        root = tmp_path / "images"
        monkeypatch.setattr(pt.settings, "images_path", str(root))
        return await _seed_photo_listings(world, root, 5)

    async def test_a_credit_refusal_ends_the_pass_at_the_first_listing_and_costs_none_an_attempt(self, world, listings, monkeypatch):
        asked = _gateway(monkeypatch, lambda r: _refuse(402, OUT_OF_CREDIT))
        async with world() as db:
            res = await pt.run_once(db, limit=5)
        assert res["stopped"] == "QuotaExhausted" and res["failed"] == 0 and res["tagged"] == 0
        assert len(asked) == 1, "five listings were due; one call found out, and the pass stopped"
        async with world() as db:
            rows = (await db.execute(select(Property.ai_photo_attempts, Property.ai_photos_at))).all()
        assert all(a in (0, None) for a, _ in rows), "not the listings' fault"
        assert all(at is None for _, at in rows), "and none was stamped as looked at: they are all still to do"

    async def test_while_it_lasts_a_pass_asks_nothing_and_does_not_even_fetch_the_photos(self, world, listings, monkeypatch):
        await _pause_now(monkeypatch)
        asked = _gateway(monkeypatch, lambda r: _answer(json.dumps({"condition": "normal"})))

        async def boom(*a, **kw):
            raise AssertionError("a photo was fetched from Divar for a listing nobody can tag")

        def no_prepare(*a, **kw):
            raise AssertionError("photos were prepared for a listing nobody can tag")
        monkeypatch.setattr(pt, "fetch_remote_photos", boom)
        monkeypatch.setattr(pt, "prepare_images", no_prepare)
        for _ in range(3):
            async with world() as db:
                res = await pt.run_once(db)
            assert res["stopped"] == "QuotaExhausted" and res["scanned"] == 0
        assert asked == []

    async def test_the_pause_is_logged_once_not_once_per_listing_or_per_pass(self, world, listings, monkeypatch, logs):
        _gateway(monkeypatch, lambda r: _refuse(402, OUT_OF_CREDIT))
        for _ in range(6):
            async with world() as db:
                await pt.run_once(db, limit=5)
        assert not [line for line in logs if "not tagged" in line], "no listing was blamed"
        loud = [line for line in logs if line.startswith(("WARNING", "ERROR"))]
        assert len(loud) == 1 and GATEWAY_WORDS in loud[0], loud
        assert len([line for line in logs if "[photo] paused" in line]) == 1, "one note for the whole pause"
        assert len([line for line in logs if "[photo]" in line]) <= 3, "the pass that met it (two lines) and that one note"

    async def test_when_the_credit_is_back_the_tagger_works_again_and_a_later_pause_is_noted_again(self, world, listings, monkeypatch, logs):
        await _pause_now(monkeypatch)
        async with world() as db:
            await pt.run_once(db)
        async with world() as db:
            assert await llm.clear_pause(db) is True
        _gateway(monkeypatch, lambda r: _answer(json.dumps({"condition": "renovated", "confidence": 0.9})))
        async with world() as db:
            res = await pt.run_once(db, limit=2)
        assert res["tagged"] == 2 and res["stopped"] is None
        assert len([line for line in logs if "[photo] paused" in line]) == 1
        await _pause_now(monkeypatch, "second pause")
        async with world() as db:
            await pt.run_once(db)
        assert len([line for line in logs if "[photo] paused" in line]) == 2, "a new pause is news again"

    async def test_a_refusal_that_is_not_about_credit_names_its_cause_in_the_log(self, world, listings, monkeypatch, logs):
        cause = "unsupported image format " + "z" * 200 + " END-OF-CAUSE"
        _gateway(monkeypatch, lambda r: _refuse(400, {"error": {"message": cause}}))
        async with world() as db:
            res = await pt.run_once(db, limit=2)
        assert res["failed"] == 2
        blamed = [line for line in logs if "not tagged" in line]
        assert len(blamed) == 2 and all("END-OF-CAUSE" in line and "HTTP 400" in line for line in blamed), blamed


class TestTheListingReader:

    @pytest.fixture
    async def listings(self, world):
        return await _seed_text_listings(world, 5)

    async def test_a_credit_refusal_ends_the_pass_at_the_first_listing_and_costs_none_an_attempt(self, world, listings, monkeypatch):
        asked = _gateway(monkeypatch, lambda r: _refuse(402, OUT_OF_CREDIT))
        async with world() as db:
            res = await reader.run_once(db)
        assert res["stopped"] == "QuotaExhausted" and res["failed"] == 0 and res["read"] == 0
        assert len(asked) == 1
        async with world() as db:
            attempts = (await db.execute(select(Property.ai_read_attempts))).scalars().all()
        assert all(a in (0, None) for a in attempts)

    async def test_while_it_lasts_a_pass_asks_nothing_and_says_so_once(self, world, listings, monkeypatch, logs):
        await _pause_now(monkeypatch)
        asked = _gateway(monkeypatch, lambda r: _answer(GOOD_FACTS))
        for _ in range(5):
            async with world() as db:
                res = await reader.run_once(db)
            assert res["stopped"] == "QuotaExhausted" and res["scanned"] == 0
        assert asked == []
        assert len([line for line in logs if "[reader]" in line]) == 1, "one note for the whole pause"
        assert not [line for line in logs if "not read" in line]

    async def test_the_engine_stops_waiting_for_a_reader_that_cannot_run(self, world, monkeypatch):
        async with world() as db:
            assert await reader.reader_will_run(db) is True
        await _pause_now(monkeypatch)
        async with world() as db:
            assert await reader.reader_will_run(db) is False, "otherwise every new listing waits out the 30 minutes"
        async with world() as db:
            await llm.clear_pause(db)
        async with world() as db:
            assert await reader.reader_will_run(db) is True

    async def test_a_refusal_that_is_not_about_credit_names_its_cause_in_the_log(self, world, listings, monkeypatch, logs):
        cause = "context length exceeded " + "w" * 200 + " END-OF-CAUSE"
        _gateway(monkeypatch, lambda r: _refuse(400, {"error": {"message": cause}}))
        async with world() as db:
            res = await reader.run_once(db, limit=2)
        assert res["failed"] == 2
        blamed = [line for line in logs if "not read" in line]
        assert len(blamed) == 2 and all("END-OF-CAUSE" in line for line in blamed), blamed


class TestTheEmbedder:

    async def test_a_credit_refusal_ends_the_pass_and_the_pause_stops_the_next_one(self, world, monkeypatch, logs):
        await _seed_text_listings(world, 5)
        asked = _gateway(monkeypatch, lambda r: _refuse(402, OUT_OF_CREDIT))
        async with world() as db:
            res = await emb.run_once(db)
        assert res["embedded"] == 0 and res["stopped"]
        assert len(asked) == 1
        for _ in range(4):
            async with world() as db:
                res = await emb.run_once(db)
            assert res["scanned"] == 0 and res["stopped"] == "QuotaExhausted"
        assert len(asked) == 1
        assert len([line for line in logs if "[embed]" in line]) <= 2, "the pass that met it, and one note"


class TestTheExplainer:

    ITEMS = [{"id": 1, "title": "آپارتمان", "area": 80, "rooms": 2, "price": 1, "district": "", "city": "", "score": 70}]

    async def test_a_credit_refusal_gives_back_nothing_and_logs_only_the_pause(self, world, monkeypatch, logs):
        from app.services import match_service as ms
        asked = _gateway(monkeypatch, lambda r: _refuse(402, OUT_OF_CREDIT))
        for _ in range(4):
            assert await ms._llm_rerank(self.ITEMS, "معیار") == {}
        assert len(asked) == 1, "asked once; the other three were stopped at the door"
        warnings = [line for line in logs if line.startswith("WARNING")]
        assert len(warnings) == 1 and GATEWAY_WORDS in warnings[0], warnings

    async def test_a_rerank_failure_that_is_not_about_credit_is_still_a_warning(self, world, monkeypatch, logs):
        from app.services import match_service as ms
        _gateway(monkeypatch, lambda r: _refuse(500, "boom"))
        assert await ms._llm_rerank(self.ITEMS, "معیار") == {}
        assert [line for line in logs if line.startswith("WARNING") and "re-rank skipped" in line and "boom" in line]

    async def test_no_background_call_is_scheduled_while_paused_and_nobody_is_told_to_wait(self, world, monkeypatch):
        import fakeredis.aioredis
        import app.database as appdb
        from app.services import match_service as ms
        fake = fakeredis.aioredis.FakeRedis(decode_responses=True)

        async def _get():
            return fake
        monkeypatch.setattr(appdb, "get_redis", _get)
        await _pause_now(monkeypatch)
        row = dict(id=1, title="ملک ۱", area=90, rooms=2, price=1_000_000_000, district="گلها", city_name="ارومیه", score=80)
        before = set(ms._background_tasks)
        assert await ms._attach_reasons("property", 7, [dict(row)], "ctx") is False, \
            "no «check back in a moment» for reasons that will not come"
        assert set(ms._background_tasks) == before
        assert await ms._cached_semantic_candidates("نیاز مشتری", "ارومیه", "buy") == {}
        assert set(ms._background_tasks) == before

        async with world() as db:
            await llm.clear_pause(db)
        assert await ms._attach_reasons("property", 7, [dict(row)], "ctx") is True, "credit is back: the ordinary path"
        for t in set(ms._background_tasks) - before:
            t.cancel()


class TestThePortalEnrichment:

    async def test_it_does_not_walk_the_queue_while_paused(self, world, monkeypatch, logs):
        from app.crm import portal_bridge
        await _pause_now(monkeypatch)

        class Tripwire:
            """The real session for the settings row; anything else fails the test."""
            def __init__(self, inner):
                self.inner = inner

            async def execute(self, stmt, *a, **kw):
                if "app_settings" in str(stmt):
                    return await self.inner.execute(stmt, *a, **kw)
                raise AssertionError("the enrichment queue was read while the gateway is paused")

        async with world() as inner:
            assert await portal_bridge.enrich_needs(Tripwire(inner)) == 0
        assert not [line for line in logs if "deferred" in line], "no line per request"
