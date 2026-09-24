"""
یابندهٔ معنایی و تکراری‌یاب — every listing's text as a vector.

Three uses, one module (app/ai/embeddings.py): a customer's own words → the
nearest listings the exact filters miss; one more signal for «ملک‌های
مشابه»; and the same flat posted twice under two titles, flagged and never
merged. Vectors come through the gateway's one door (llm.embed), which
masks and meters; here the door opens onto a fake, and nothing touches the
network or Postgres.
"""
import asyncio
import json
import os
import sys
import zlib
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_ai_embed.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.ai import embeddings as emb  # noqa: E402
from app.models.property import Property  # noqa: E402
from app.services import llm, secret_box  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
_REAL_CLIENT = httpx.AsyncClient
DIM = 64


def fake_vec(text: str):
    """A stand-in embedder: a bag of character trigrams, unit length. Two
    ads worded almost alike land close; a shop and a flat do not."""
    v = [0.0] * DIM
    t = " ".join(text.split())
    for i in range(max(0, len(t) - 2)):
        v[zlib.crc32(t[i:i + 3].encode("utf-8")) % DIM] += 1.0
    n = sum(x * x for x in v) ** 0.5 or 1.0
    return [x / n for x in v]


def _gateway(monkeypatch, handler):
    class Fake(_REAL_CLIENT):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **kw)
    monkeypatch.setattr(llm.httpx, "AsyncClient", Fake)


def _embeddings(calls, cost=0.0):
    """The gateway's /embeddings, answering in the OpenAI shape, out of order
    on purpose — llm.embed sorts by index."""
    def handler(req):
        assert req.url.path.endswith("/embeddings")
        texts = json.loads(req.read())["input"]
        calls.append(list(texts))
        data = [{"index": i, "embedding": fake_vec(t)} for i, t in enumerate(texts)]
        return httpx.Response(200, json={"data": list(reversed(data)),
                                         "usage": {"prompt_tokens": 5 * len(texts), "cost": cost, "total_cost_toman": 0}})
    return handler


@pytest.fixture
def configured(monkeypatch):
    """A gateway that exists and a ledger that records — nothing else faked:
    the cursor and the cap's panel value go through the real app_settings."""
    monkeypatch.setattr(llm.settings, "llm_api_key", "k-test", raising=False)
    monkeypatch.setattr(llm.settings, "llm_base_url", "https://ai.liara.ir/api/6aa50e58b5e9e82406b93188/v1", raising=False)
    monkeypatch.setattr(llm.settings, "llm_model_embed", "openai/text-embedding-3-small", raising=False)
    ledger = []

    async def record(agent, job, model, usage, ms, ok, error=""):
        ledger.append({"agent": agent, "job": job, "model": model, "ok": ok, "cost": float(usage.get("cost") or 0)})

    async def spent(_db):
        return sum(r["cost"] for r in ledger)

    monkeypatch.setattr(llm, "_record", record)
    monkeypatch.setattr(llm, "spent_today", spent)
    return ledger


@pytest.fixture
def maker(tmp_path):
    """A sqlite database of its own with the tables this module touches
    (scraping_jobs carries a Postgres UUID, so not every table), one
    session factory."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool
    from app.database import Base
    from app.models.ai_usage import AiUsage
    from app.models.app_setting import AppSetting
    from app.models.property import Category, City
    tables = [t.__table__ for t in (City, Category, Property, AppSetting, AiUsage)]
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/embed.db", poolclass=NullPool)

    async def _build():
        async with eng.begin() as c:
            await c.run_sync(lambda sc: Base.metadata.create_all(sc, tables=tables))
    asyncio.run(_build())
    yield async_sessionmaker(eng, expire_on_commit=False)
    asyncio.run(eng.dispose())


def P(i, title, *, district="خیابان گلها", city="ارومیه", listing_type="buy", price=4_000_000_000,
      area=100, rooms=2, description="", kind="آپارتمان", **kw):
    return Property(tag_number=f"e-{i}", divar_id=f"e-{i}", url=f"https://divar.ir/v/e-{i}", title=title,
                    description=description, city_name=city, district=district, area=area, rooms=rooms,
                    property_type=kind, listing_type=listing_type,
                    total_price=price if listing_type == "buy" else None,
                    deposit=price if listing_type == "rent" else None,
                    serial_no=1000 + i, is_active=True, **kw)


async def _add(maker, props):
    async with maker() as s:
        s.add_all(props)
        await s.commit()
        return [p.id for p in props]


async def _get(maker, pid):
    """The row, vector included — deferred, so this test file's own reads of
    it undefer explicitly, the way any caller that actually needs it must."""
    from sqlalchemy import select
    from sqlalchemy.orm import undefer
    async with maker() as s:
        return (await s.execute(select(Property).where(Property.id == pid)
                                .options(undefer(Property.ai_embedding)))).scalar_one()


# ── the text ──────────────────────────────────────────────────────────────────

class TestText:

    def test_it_is_deterministic_bounded_and_without_the_price(self):
        p = P(1, "آپارتمان ۱۰۰ متری خیابان گلها", description="x " * 5000)
        assert emb.text_of(p) == emb.text_of(p)
        assert len(emb.text_of(p)) <= emb.MAX_CHARS
        short = P(2, "آپارتمان ۱۰۰ متری خیابان گلها", description="طبقه سوم")
        t = emb.text_of(short)
        assert t.splitlines()[0] == "آپارتمان ۱۰۰ متری خیابان گلها"
        assert "خیابان گلها · ارومیه" in t and "100 متر" in t and "2 خواب" in t and "فروش" in t and "طبقه سوم" in t
        assert "4000000000" not in t, "the same flat at two asking prices must still read as one flat"

    def test_the_readers_facts_ride_along_when_they_exist(self):
        """text_of used to read ai_facts["flags"], a key the reader
        (app/ai/listing_reader.py: ListingFacts) never writes. These are its
        actual keys."""
        base = dict(title="ویلا در بند", district=None, neighborhood="بند", city_name="ارومیه", property_type="ویلا",
                    category_name=None, area=300, rooms=None, listing_type="rent", description="")
        plain = SimpleNamespace(**base)
        assert "ai_facts" not in emb.text_of(plain)
        with_facts = SimpleNamespace(**base, ai_facts={
            "summary": "دوبلکس با استخر", "kind": "house", "document": "تک‌برگ", "condition": "نوساز",
            "convertible": True, "exchange": False, "vacant": True, "negotiable": False,
            "suitable_for": ["کافه", "مهدکودک"]})
        t = emb.text_of(with_facts)
        assert "دوبلکس با استخر" in t and "بند · ارومیه" in t and "رهن و اجاره" in t
        assert "خانه، تک‌برگ، نوساز، قابل تبدیل، تخلیه" in t, "kind in Persian, document/condition as-is, true flags only"
        assert "معاوضه" not in t and "قابل مذاکره" not in t, "false flags say nothing"
        assert "مناسب کافه، مهدکودک" in t
        bare = SimpleNamespace(**base, ai_facts={"kind": "apartment"})
        assert emb.text_of(bare).splitlines()[-1] == "آپارتمان", "no summary, no suitable_for: just the one word"

    def test_it_masks_nothing_itself_because_the_door_does(self, configured, monkeypatch, maker):
        p = P(3, "آپارتمان", description="تماس 09143495300")
        assert "09143495300" in emb.text_of(p), "masking lives in llm.embed, once, for every agent"
        calls = []
        _gateway(monkeypatch, _embeddings(calls))

        async def _go():
            async with maker() as s:
                await llm.embed([emb.text_of(p)], agent="embed", db=s)
        asyncio.run(_go())
        assert calls and "09143495300" not in calls[0][0] and "۰۹×××××××××" in calls[0][0]


# ── the arithmetic ────────────────────────────────────────────────────────────

class TestArithmetic:

    def test_cosine(self):
        assert emb.cosine([1, 0], [1, 0]) == pytest.approx(1.0)
        assert emb.cosine([1, 0], [0, 1]) == pytest.approx(0.0)
        assert emb.cosine([1, 0], [-1, 0]) == pytest.approx(-1.0)
        assert emb.cosine([], []) == 0.0 and emb.cosine([0, 0], [1, 1]) == 0.0
        assert emb.cosine([1, 0], [1, 0, 0]) == 0.0, "different models are not comparable"

    def test_nearest_is_ordered_capped_and_skips_foreign_dimensions(self):
        rows = [(1, [1, 0, 0]), (2, [0.9, 0.1, 0]), (3, [0, 1, 0]), (4, [1, 0]), (5, [0, 0, 0])]
        out = emb.nearest([1, 0, 0], rows, limit=2)
        assert [i for i, _ in out] == [1, 2]
        assert out[0][1] == pytest.approx(1.0) and 0.99 < out[1][1] < 1.0
        assert [i for i, _ in emb.nearest([1, 0, 0], rows, limit=10)] == [1, 2, 3, 5]
        assert emb.nearest([1, 0, 0], [], 5) == [] and emb.nearest([], rows, 5) == [] and emb.nearest([1, 0, 0], rows, 0) == []

    def test_nearest_is_build_matrix_plus_nearest_in(self):
        """nearest() keeps its old signature and answer; the matrix it
        builds each call is the same object shape a pass can build once and
        reuse — see TestTheMatrixIsBuiltOnce."""
        rows = [(1, [1, 0, 0]), (2, [0.9, 0.1, 0]), (3, [0, 1, 0]), (4, [1, 0]), (5, [0, 0, 0])]
        m = emb.build_matrix(rows)
        assert m is not None and m.ids == [1, 2, 3, 5] and m.vecs.shape == (4, 3)
        assert emb.nearest_in([1, 0, 0], m, limit=2) == emb.nearest([1, 0, 0], rows, limit=2)
        assert emb.build_matrix([]) is None
        assert emb.nearest_in([1, 0, 0], None, limit=5) == []
        assert emb.nearest_in([1, 0], m, limit=5) == [], "the query's own dimension does not match the matrix"

    def test_text_similarity_needs_two_vectors_of_one_version(self):
        a = SimpleNamespace(ai_embedding=[1, 0], ai_embed_version=1)
        b = SimpleNamespace(ai_embedding=[1, 1], ai_embed_version=1)
        assert emb.text_similarity(a, b) == pytest.approx(2 ** -0.5)
        assert emb.text_similarity(a, SimpleNamespace(ai_embedding=None, ai_embed_version=1)) is None
        assert emb.text_similarity(SimpleNamespace(), b) is None
        assert emb.text_similarity(a, SimpleNamespace(ai_embedding=[1, 1], ai_embed_version=2)) is None


# ── the vectors on the rows ───────────────────────────────────────────────────

class TestEmbedProperties:

    def test_batches_of_thirty_two_and_the_vector_lands_on_the_row(self, configured, monkeypatch, maker):
        calls = []
        _gateway(monkeypatch, _embeddings(calls))
        ids = asyncio.run(_add(maker, [P(i, f"آپارتمان {i} متری") for i in range(1, 34)]))

        async def _go():
            from sqlalchemy import select
            async with maker() as s:
                props = (await s.execute(select(Property).order_by(Property.id))).scalars().all()
                return await emb.embed_properties(s, props)
        assert asyncio.run(_go()) == 33
        assert [len(c) for c in calls] == [32, 1]
        assert calls[0][0].startswith("آپارتمان 1 متری")
        row = asyncio.run(_get(maker, ids[0]))
        assert len(row.ai_embedding) == DIM and row.ai_embed_version == emb.EMBED_VERSION and row.ai_embedded_at is not None
        assert row.ai_embedding == pytest.approx(fake_vec(emb.text_of(row))), "in order, whatever order the gateway answered in"
        assert all(r["agent"] == "embed" and r["job"] == "embed" for r in configured)


class TestRunOnce:

    def test_the_cursor_moves_past_what_landed_and_stops_at_the_cap(self, configured, monkeypatch, maker):
        calls = []
        _gateway(monkeypatch, _embeddings(calls, cost=0.0015))
        ids = asyncio.run(_add(maker, [P(i, f"آپارتمان {i} متری خیابان {i}", district=f"خ {i}") for i in range(1, 41)]))

        async def _go(cap):
            async with maker() as s:
                await secret_box.put(s, llm.KEY_CAP, cap, "t")
                return await emb.run_once(s, limit=200)
        first = asyncio.run(_go("0.001"))      # the first call spends 0.0015: the second is refused
        assert first["scanned"] == 40 and first["embedded"] == 32 and first["cursor"] == ids[31]
        assert first["stopped"] and "سقف" in first["stopped"]
        assert [len(c) for c in calls] == [32]
        assert asyncio.run(_get(maker, ids[31])).ai_embedding and asyncio.run(_get(maker, ids[32])).ai_embedding is None

        second = asyncio.run(_go("10"))        # tomorrow: the eight that waited
        assert second["scanned"] == 8 and second["embedded"] == 8 and second["cursor"] == ids[39] and second["stopped"] is None
        assert asyncio.run(_go("10")) == {"scanned": 0, "embedded": 0, "duplicates": 0, "cursor": ids[39], "stopped": None}

    def test_an_unconfigured_gateway_ends_the_pass_quietly(self, monkeypatch, maker):
        monkeypatch.setattr(llm.settings, "llm_api_key", "", raising=False)
        asyncio.run(_add(maker, [P(1, "آپارتمان")]))

        async def _go():
            async with maker() as s:
                return await emb.run_once(s)
        out = asyncio.run(_go())
        assert out["scanned"] == 1 and out["embedded"] == 0 and out["cursor"] == 0 and "تنظیم نشده" in out["stopped"]

    def test_a_pass_flags_the_repeat_among_the_new_ones(self, configured, monkeypatch, maker):
        _gateway(monkeypatch, _embeddings([]))
        desc = "طبقه سوم، دو خواب، آسانسور و پارکینگ، نورگیر عالی، بازسازی‌شده، سند تک‌برگ"
        a, b, shop = asyncio.run(_add(maker, [
            P(1, "آپارتمان ۱۰۰ متری خیابان گلها", description=desc, price=4_000_000_000),
            P(2, "فروش آپارتمان ۱۰۰ متری خیابان گلها", description=desc, price=4_100_000_000),
            P(3, "مغازه ۴۰ متری خیابان گلها", description="بر خیابان اصلی، سند تجاری", kind="مغازه", area=40, rooms=None),
        ]))

        async def _go():
            async with maker() as s:
                return await emb.run_once(s)
        out = asyncio.run(_go())
        assert out["embedded"] == 3 and out["duplicates"] == 1
        assert asyncio.run(_get(maker, b)).ai_duplicate_of == a, "the newer twin points at the older"
        assert asyncio.run(_get(maker, a)).ai_duplicate_of is None
        assert asyncio.run(_get(maker, shop)).ai_duplicate_of is None


class TestStaleness:
    """After a content change, a re-read, or an EMBED_VERSION bump, a
    listing already embedded is due again — even behind the stored cursor,
    which is why run_once no longer bounds its query on id > cursor."""

    def test_an_unchanged_vector_is_not_touched_again(self, configured, monkeypatch, maker):
        _gateway(monkeypatch, _embeddings([]))
        asyncio.run(_add(maker, [P(1, "آپارتمان ۱۰۰ متری خیابان گلها")]))

        async def _go():
            async with maker() as s:
                return await emb.run_once(s)
        first = asyncio.run(_go())
        again = asyncio.run(_go())
        assert first["embedded"] == 1 and again["embedded"] == 0 and again["scanned"] == 0

    def test_a_content_edit_re_embeds_even_behind_the_cursor(self, configured, monkeypatch, maker):
        _gateway(monkeypatch, _embeddings([]))
        ids = asyncio.run(_add(maker, [P(1, "آپارتمان یک"), P(2, "آپارتمان دو")]))

        async def first_pass():
            async with maker() as s:
                return await emb.run_once(s)
        asyncio.run(first_pass())
        before = asyncio.run(_get(maker, ids[0]))
        before_fp = before.ai_embed_fp

        async def edit_and_rerun():
            async with maker() as s:
                p = await s.get(Property, ids[0])
                p.description = "متن کاملاً تازه که قبلاً نبود"
                await s.commit()
                return await emb.run_once(s)
        again = asyncio.run(edit_and_rerun())
        assert again["scanned"] == 1 and again["embedded"] == 1, "the id-1 listing, not the untouched id-2 one"
        after = asyncio.run(_get(maker, ids[0]))
        assert after.ai_embed_fp == after.ai_content_fp and after.ai_embed_fp != before_fp

    def test_a_fresher_read_re_embeds_even_when_the_raw_columns_did_not_move(self, configured, monkeypatch, maker):
        """text_of() folds in the reader's facts (kind, document, …); a
        re-read after the vector was written means the text the vector
        stands for changed too, even though title/description are the same
        columns as before."""
        _gateway(monkeypatch, _embeddings([]))
        [pid] = asyncio.run(_add(maker, [P(1, "آپارتمان یک")]))

        async def embed_it():
            async with maker() as s:
                return await emb.run_once(s)
        asyncio.run(embed_it())

        async def read_then_rerun():
            from datetime import datetime, timezone
            async with maker() as s:
                p = await s.get(Property, pid)
                p.ai_facts = {"kind": "house", "prompt_version": 1}
                p.ai_read_at = datetime.now(timezone.utc)
                await s.commit()
                return await emb.run_once(s)
        again = asyncio.run(read_then_rerun())
        assert again["scanned"] == 1 and again["embedded"] == 1

    def test_a_version_bump_reopens_every_listing_not_only_new_ones(self, configured, monkeypatch, maker):
        _gateway(monkeypatch, _embeddings([]))
        asyncio.run(_add(maker, [P(1, "آپارتمان یک")]))

        async def embed_it():
            async with maker() as s:
                return await emb.run_once(s)
        asyncio.run(embed_it())
        assert asyncio.run(embed_it())["scanned"] == 0, "settled, at the current version"

        monkeypatch.setattr(emb, "EMBED_VERSION", emb.EMBED_VERSION + 1)
        again = asyncio.run(embed_it())
        assert again["scanned"] == 1 and again["embedded"] == 1, "a version bump is due even with unchanged content"


# ── duplicates, on hand-made vectors ──────────────────────────────────────────

U = [1.0] + [0.0] * (DIM - 1)                 # the original
V = [1.0, 0.2] + [0.0] * (DIM - 2)            # cosine 0.98 to U
W = [0.7, 0.7] + [0.0] * (DIM - 2)            # cosine 0.71 to U


def _vec(p, v):
    p.ai_embedding, p.ai_embed_version = v, emb.EMBED_VERSION
    return p


class TestDuplicates:

    def _run(self, maker, fn):
        async def _go():
            async with maker() as s:
                return await fn(s)
        return asyncio.run(_go())

    def test_same_street_close_price_alike_text_is_a_duplicate_of_the_older(self, maker):
        a, b = asyncio.run(_add(maker, [_vec(P(1, "آپارتمان ۱۰۰ متری گلها", district="خیابان گلها", price=4_000_000_000), U),
                                        _vec(P(2, "۱۰۰ متر گلها فروشی", district="خ گلها", price=4_100_000_000), V)]))
        async def _find(s):
            from sqlalchemy import select
            row = (await s.execute(select(Property).where(Property.id == b))).scalar_one()
            return await emb.find_duplicates(s, row)
        dups = self._run(maker, _find)
        assert [d["id"] for d in dups] == [a] and dups[0]["score"] >= emb.DUPLICATE_THRESHOLD
        assert dups[0]["serial_no"] == 1001 and dups[0]["title"] == "آپارتمان ۱۰۰ متری گلها"

        async def _mark(s):
            from sqlalchemy import select
            rows = (await s.execute(select(Property).order_by(Property.id))).scalars().all()
            return await emb.mark_duplicates(s, rows)
        assert self._run(maker, _mark) == 1
        assert asyncio.run(_get(maker, b)).ai_duplicate_of == a
        assert asyncio.run(_get(maker, a)).ai_duplicate_of is None, "the older listing is the original, never the repeat"

    @pytest.mark.parametrize("twist", ["district", "price", "text", "city", "deal", "inactive"])
    def test_what_is_not_a_duplicate(self, maker, twist):
        older = _vec(P(1, "آپارتمان ۱۰۰ متری گلها", district="خیابان گلها", price=4_000_000_000), U)
        kw = dict(district="خیابان گلها", price=4_000_000_000)
        vec = V
        if twist == "district":
            kw["district"] = "بلوار سعدی"
        elif twist == "price":
            kw["price"] = 4_800_000_000                 # 20% apart: another flat, or a very different ask
        elif twist == "text":
            vec = W
        elif twist == "city":
            kw["city"] = "تبریز"
        elif twist == "deal":
            kw["listing_type"] = "rent"
        elif twist == "inactive":
            older.is_active = False
        a, b = asyncio.run(_add(maker, [older, _vec(P(2, "۱۰۰ متر گلها", **kw), vec)]))

        async def _find(s):
            from sqlalchemy import select
            return await emb.find_duplicates(s, (await s.execute(select(Property).where(Property.id == b))).scalar_one())
        assert self._run(maker, _find) == []

    def test_no_district_on_either_still_counts_and_a_missing_vector_says_nothing(self, maker):
        a, b, c = asyncio.run(_add(maker, [_vec(P(1, "زمین", district=None, price=None), U),
                                           _vec(P(2, "زمین کلنگی", district=None, price=None), V),
                                           P(3, "بدون بردار", district=None, price=None)]))

        async def _find(s, pid):
            from sqlalchemy import select
            return await emb.find_duplicates(s, (await s.execute(select(Property).where(Property.id == pid))).scalar_one())
        assert [d["id"] for d in self._run(maker, lambda s: _find(s, b))] == [a]
        assert self._run(maker, lambda s: _find(s, c)) == []


# ── deferred() — the column is not loaded unless something asks for it ────────

class TestDeferredColumn:

    def test_a_plain_select_does_not_carry_the_vector(self, maker):
        """The whole point of deferred(): a query nobody wrote for embeddings
        does not pay for 1536 floats it never reads."""
        from sqlalchemy import inspect as sa_inspect, select
        [pid] = asyncio.run(_add(maker, [_vec(P(1, "آپارتمان"), U)]))

        async def _go():
            async with maker() as s:
                row = (await s.execute(select(Property).where(Property.id == pid))).scalar_one()
                return "ai_embedding" in sa_inspect(row).unloaded
        assert asyncio.run(_go()) is True

    def test_ordinary_property_reads_do_not_crash_on_the_deferred_column(self, maker):
        """to_dict() — what every listing endpoint sends — never touches
        ai_embedding, loaded or not; ordinary access stays safe."""
        from sqlalchemy import select
        [pid] = asyncio.run(_add(maker, [_vec(P(1, "آپارتمان"), U)]))

        async def _go():
            async with maker() as s:
                row = (await s.execute(select(Property).where(Property.id == pid))).scalar_one()
                return row.to_dict()
        d = asyncio.run(_go())
        assert "ai_embedding" not in d and d["id"] == pid

    def test_text_similarity_and_find_duplicates_survive_an_unloaded_row(self, maker):
        """text_similarity has no db handle to fall back on (a sync
        function): an unloaded vector reads as "no signal", not a crash.
        find_duplicates does have one and fetches the column itself — proven
        already by TestDuplicates, which loads every row through a plain
        select; this just names the two failure modes explicitly."""
        from sqlalchemy import select
        a, b = asyncio.run(_add(maker, [_vec(P(1, "آپارتمان یک"), U), _vec(P(2, "آپارتمان دو"), V)]))

        async def _go():
            async with maker() as s:
                ra = (await s.execute(select(Property).where(Property.id == a))).scalar_one()
                rb = (await s.execute(select(Property).where(Property.id == b))).scalar_one()
                return emb.text_similarity(ra, rb), await emb.find_duplicates(s, ra)
        sim, dups = asyncio.run(_go())
        assert sim is None, "neither row's vector was loaded by this query"
        assert dups == [] or all(isinstance(x, dict) for x in dups), "a fetch, not a MissingGreenlet"


# ── the matrix is built once, not once per listing or per query ───────────────

class TestTheMatrixIsBuiltOnce:

    def test_mark_duplicates_builds_the_matrix_once_for_the_whole_batch(self, maker, monkeypatch):
        rows = [_vec(P(i, f"آپارتمان {i} متری گلها", price=4_000_000_000), U) for i in range(1, 6)]
        ids = asyncio.run(_add(maker, rows))
        calls = []
        real = emb.build_matrix

        def counting(*a, **kw):
            calls.append(1)
            return real(*a, **kw)
        monkeypatch.setattr(emb, "build_matrix", counting)

        async def _go():
            async with maker() as s:
                from sqlalchemy import select
                props = (await s.execute(select(Property).where(Property.id.in_(ids)))).scalars().all()
                return await emb.mark_duplicates(s, props)
        asyncio.run(_go())
        assert calls == [1], "one matrix for five listings, not five"

    def test_semantic_candidates_reuses_the_cached_matrix_within_the_ttl(self, configured, monkeypatch, maker):
        _gateway(monkeypatch, _embeddings([]))
        asyncio.run(_add(maker, [_vec(P(1, "آپارتمان ۱۰۰ متری خیابان گلها"), U)]))
        emb._cache.clear()
        emb._generation = 0
        loads = []
        real = emb.load_index

        async def counting(*a, **kw):
            loads.append(1)
            return await real(*a, **kw)
        monkeypatch.setattr(emb, "load_index", counting)

        async def _go():
            async with maker() as s:
                await emb.semantic_candidates(s, "آپارتمان گلها", city="ارومیه")
                await emb.semantic_candidates(s, "یک متن دیگر", city="ارومیه")   # same (city, type): cached
                await emb.semantic_candidates(s, "و باز هم", city="تبریز")       # a different key: its own load
        asyncio.run(_go())
        assert loads == [1, 1], "two distinct (city, listing_type) keys, not three lookups"

    def test_an_embed_pass_bumps_the_generation_and_invalidates_the_cache(self, configured, monkeypatch, maker):
        _gateway(monkeypatch, _embeddings([]))
        asyncio.run(_add(maker, [_vec(P(1, "آپارتمان یک"), U)]))
        emb._cache.clear()
        emb._generation = 0

        async def _go():
            async with maker() as s:
                await emb.semantic_candidates(s, "آپارتمان", city="ارومیه")
                gen_before = emb._cache[("ارومیه", None)][2]
                await emb.embed_properties(s, [(await s.get(Property, 1))])
                return gen_before, emb._generation
        gen_before, gen_after = asyncio.run(_go())
        assert gen_after > gen_before, "the next semantic_candidates for this key rebuilds, not serves stale"


# ── the routes ────────────────────────────────────────────────────────────────

def _client(maker, role="super_admin"):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.routes import ai_embed
    from app.auth.dependencies import get_current_user
    from app.database import get_db
    app = FastAPI()
    app.include_router(ai_embed.router, prefix="/ai/embed")
    app.include_router(ai_embed.crm_router, prefix="/ai/embed")

    async def _db():
        async with maker() as s:
            yield s
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(role=role, username="t")
    return TestClient(app)


class TestRoutes:

    def test_status_shape_and_who_may_ask(self, configured, monkeypatch, maker):
        asyncio.run(_add(maker, [_vec(P(1, "الف"), U), P(2, "ب"), P(3, "", district=None)]))
        r = _client(maker).get("/ai/embed/status")
        assert r.status_code == 200, r.text
        s = r.json()
        assert set(s) >= {"cursor", "version", "embedded", "behind", "duplicates", "threshold", "interval_seconds", "enabled", "configured"}
        assert s["cursor"] == 0 and s["version"] == emb.EMBED_VERSION and s["embedded"] == 1 and s["behind"] == 2 and s["duplicates"] == 0
        assert s["configured"] is True and s["threshold"] == 0.95
        admin = _client(maker, role="admin")
        assert admin.get("/ai/embed/status").status_code == 403 and admin.post("/ai/embed/run").status_code == 403
        assert admin.post("/ai/embed/run?limit=501").status_code in (403, 422)

    def test_run_then_search_similar_and_duplicates_for_a_crm_user(self, configured, monkeypatch, maker):
        _gateway(monkeypatch, _embeddings([]))
        desc = "طبقه سوم، دو خواب، آسانسور و پارکینگ، نورگیر عالی، بازسازی‌شده"
        a, b, shop = asyncio.run(_add(maker, [
            P(1, "آپارتمان ۱۰۰ متری خیابان گلها", description=desc),
            P(2, "فروش آپارتمان ۱۰۰ متری خیابان گلها", description=desc, price=4_050_000_000),
            P(3, "مغازه ۴۰ متری بلوار سعدی", description="بر خیابان اصلی، سند تجاری", kind="مغازه", area=40, rooms=None, district="بلوار سعدی"),
        ]))
        boss, mina = _client(maker), _client(maker, role="admin")
        run = boss.post("/ai/embed/run?limit=10")
        assert run.status_code == 200 and run.json()["embedded"] == 3 and run.json()["duplicates"] == 1

        r = mina.post("/ai/embed/search", json={"text": "مغازه بر خیابان با سند تجاری", "city": "ارومیه"})
        assert r.status_code == 200, r.text
        items = r.json()["items"]
        assert items and items[0]["id"] == shop and items[0]["reasons"] == ["شباهت متن"]
        assert {"id", "serial_no", "title", "district", "price", "score", "similarity"} <= set(items[0])
        assert items[0]["price"] == 4_000_000_000 and 0 <= items[0]["score"] <= 100
        assert mina.post("/ai/embed/search", json={"text": "x"}).status_code == 422
        assert mina.post("/ai/embed/search", json={"text": "آپارتمان", "listing_type": "sell"}).status_code == 422

        sim = mina.get(f"/ai/embed/similar/{a}").json()
        assert [i["id"] for i in sim["items"]][0] == b and all(i["id"] != a for i in sim["items"])
        dup = mina.get(f"/ai/embed/duplicates/{b}").json()
        assert dup["duplicate_of"] == a and [d["id"] for d in dup["items"]] == [a]
        assert mina.get("/ai/embed/similar/999").status_code == 404

    def test_search_without_a_gateway_is_a_502_not_a_crash(self, monkeypatch, maker):
        monkeypatch.setattr(llm.settings, "llm_api_key", "", raising=False)
        r = _client(maker, role="admin").post("/ai/embed/search", json={"text": "دو خواب نزدیک دانشگاه"})
        assert r.status_code == 502 and "تنظیم نشده" in r.json()["detail"]
        asyncio.run(_add(maker, [P(1, "بدون بردار")]))
        r = _client(maker, role="admin").get("/ai/embed/similar/1")
        assert r.status_code == 200 and r.json()["items"] == [] and r.json()["pending"] is True


# ── the shape ─────────────────────────────────────────────────────────────────

class TestTheShape:

    def test_the_columns_and_what_to_dict_shows(self):
        cols = Property.__table__.c
        for name in ("ai_embedding", "ai_embedded_at", "ai_embed_version", "ai_duplicate_of"):
            assert name in cols
        assert cols.ai_duplicate_of.index is True
        d = P(1, "x").to_dict()
        assert "ai_embedded_at" in d and "ai_duplicate_of" in d
        assert "ai_embedding" not in d, "the vector is big and stays off the wire"

    def test_it_is_a_background_loop_like_the_others(self):
        src = (ROOT / "app/ai/embeddings.py").read_text(encoding="utf-8")
        assert 'getattr(settings, "match_engine", True)' in src and "TICK_SECONDS = 180" in src
        assert "ZoneInfo" not in src and "zoneinfo" not in src
        assert "import httpx" not in src and "llm.embed(" in src, "the gateway is reached through the one door only"
        assert "pgvector" in src

    def test_the_routes_are_split_by_audience(self):
        src = (ROOT / "app/api/routes/ai_embed.py").read_text(encoding="utf-8")
        assert '_role_dep("root", "super_admin")' in src
        assert src.count("_super_admin)") == 2 and src.count("_crm_user)") == 3
        assert "crm_router = APIRouter()" in src

    def test_the_panel_side_has_no_native_dialogs(self):
        js = (ROOT / "frontend/js/ai/embed.js").read_text(encoding="utf-8")
        assert "function aiDuplicateBadge" in js and "احتمالاً تکراری" in js and "viewProperty(${id})" in js
        assert "async function aiSemanticSearch" in js and "apiCall('/ai/embed/search'" in js
        assert "function aiRenderSemanticResults" in js
        for bad in ("prompt(", "confirm(", "alert("):
            assert bad not in js

    def test_the_integration_notes_exist(self):
        md = (ROOT / "app/ai/EMBED.INTEGRATION.md").read_text(encoding="utf-8")
        for needle in ("embed_loop", "0006", "text_similarity", "semantic_candidates", "aiDuplicateBadge", "pgvector"):
            assert needle in md
