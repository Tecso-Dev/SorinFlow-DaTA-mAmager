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
        result = await session.execute(select(Property).where(Property.tag_number == 'SF-20260609114043-6DEB47'))
        p = result.scalar_one_or_none()
        if p:
            print('DESC:', repr((p.description or '')[:300]))
            print('IMAGES:')
            for u in (p.images or []):
                print(' ', u[-80:])
    await engine.dispose()

asyncio.run(check())
