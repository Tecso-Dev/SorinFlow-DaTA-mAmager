"""
The boot backfill behind the AI pipeline's staleness columns
(app/database.py _backfill_ai_pipeline_fingerprints).

It is the one thing standing between this release and a flood: every
listing that existed before it has no ai_content_fp, and the event listener
stamps one on the next write to the row — after which the reader would read
it again (money) and the match engine would judge and announce it again
(Telegram). The backfill marks what each stage had already finished, for the
whole table, before the loops start. Runs on the suite's own database kind:
Postgres in the Postgres run (where a JSON column read through a raw query
decodes through the driver's codec), sqlite otherwise.
"""
import asyncio
import os
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.database as database
from app.ai.embeddings import EMBED_VERSION
from app.ai.listing_reader import PROMPT_VERSION
from app.database import Base
from app.models.app_setting import AppSetting
from app.models.property import Category, City, Property, content_fingerprint

PG = os.environ.get("DATABASE_URL", "").startswith("postgresql")
SCHEMA = "sf_aifp"


def _engine(tmp_path):
    if PG:
        return create_async_engine(os.environ["DATABASE_URL"], poolclass=NullPool,
                                   connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}})
    return create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/aifp.db", poolclass=NullPool)


def _row(i, **kw):
    return Property(id=i, tag_number=f"fp-{i}", divar_id=f"fp-{i}", url=f"https://divar.ir/v/fp-{i}",
                    title=f"آپارتمان {i}", city_name="ارومیه", area=90 + i, is_active=True, **kw)


def test_the_whole_table_is_marked_in_one_boot_by_what_each_stage_had_done(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "_AI_FP_BACKFILL_BATCH", 2)     # several batches, one call
    eng = _engine(tmp_path)
    now = datetime.now(timezone.utc)

    async def _go():
        if PG:
            async with eng.begin() as c:
                await c.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
                await c.execute(text(f"CREATE SCHEMA {SCHEMA}"))
        # Postgres holds properties to its foreign keys, so the whole schema;
        # sqlite cannot compile the Postgres UUID columns, so the few it needs
        import app.models  # noqa: F401 — every model on Base.metadata
        tables = None if PG else [t.__table__ for t in (City, Category, Property, AppSetting)]
        async with eng.begin() as c:
            await c.run_sync(lambda sc: Base.metadata.create_all(sc, tables=tables))
        maker = async_sessionmaker(eng, expire_on_commit=False)
        async with maker() as s:
            s.add_all([
                # read at this prompt version, embedded by the previous text
                _row(1, ai_read_at=now, ai_facts={"prompt_version": PROMPT_VERSION},
                     ai_embedded_at=now, ai_embed_version=EMBED_VERSION - 1),
                _row(2),                                                   # nothing done yet
                _row(3, ai_read_at=now, ai_facts={"prompt_version": PROMPT_VERSION - 1}),
                _row(4, ai_embedded_at=now, ai_embed_version=EMBED_VERSION),
                _row(5, ai_read_at=now, ai_facts={"prompt_version": PROMPT_VERSION}),
            ])
            s.add(AppSetting(key="match_engine_cursor", value="3"))
            await s.commit()
        # what a row from before this release looks like: the listener stamped
        # these on insert just now, the deployed table has none of them
        async with eng.begin() as c:
            await c.execute(text("UPDATE properties SET ai_content_fp = NULL, ai_read_fp = NULL, "
                                 "ai_embed_fp = NULL, ai_matched_at = NULL, ai_match_fp = NULL"))
        async with eng.begin() as c:
            await database._backfill_ai_pipeline_fingerprints(c)
        async with maker() as s:
            rows = {p.id: p for p in (await s.execute(select(Property))).scalars().all()}
        if PG:
            async with eng.begin() as c:
                await c.execute(text(f"DROP SCHEMA {SCHEMA} CASCADE"))
        return rows

    try:
        rows = asyncio.run(_go())
    finally:
        asyncio.run(eng.dispose())

    for p in rows.values():
        assert p.ai_content_fp == content_fingerprint(p), f"row {p.id} left without a fingerprint"
    one, two, three, four, five = (rows[i] for i in range(1, 6))
    # the reader: only what it read at the current prompt version is current
    assert one.ai_read_fp == one.ai_content_fp and five.ai_read_fp == five.ai_content_fp
    assert two.ai_read_fp is None and three.ai_read_fp is None
    # the embedder: a vector of the previous text is due again, a current one is not
    assert one.ai_embed_fp is None and four.ai_embed_fp == four.ai_content_fp
    # the engine: everything up to its old cursor was judged — never announced twice
    for p in (one, two, three):
        assert p.ai_matched_at is not None and p.ai_match_fp == p.ai_content_fp
    for p in (four, five):
        assert p.ai_matched_at is None and p.ai_match_fp is None
