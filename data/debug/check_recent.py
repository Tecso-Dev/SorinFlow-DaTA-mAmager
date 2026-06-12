import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import NullPool
from sqlalchemy import select
from app.models.property import Property
from app.config import get_settings

settings = get_settings()

async def check():
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    async with async_sessionmaker(engine, class_=AsyncSession)() as session:
        result = await session.execute(
            select(Property).order_by(Property.id.desc()).limit(5)
        )
        props = result.scalars().all()
        for p in props:
            imgs = p.images or []
            main_count = sum(1 for u in imgs if 'webp_main' in u)
            post_count = sum(1 for u in imgs if 'webp_post' in u)
            thumb_count = sum(1 for u in imgs if 'webp_thumbnail' in u)
            print(f"\n[{p.tag_number}]")
            print(f"  desc: {repr((p.description or '')[:80])}")
            print(f"  phone: {p.phone_number}")
            print(f"  imgs: total={len(imgs)} webp_main={main_count} webp_post={post_count} thumb={thumb_count}")
            print(f"  elevator={p.has_elevator} parking={p.has_parking} storage={p.has_storage}")
    await engine.dispose()

asyncio.run(check())
