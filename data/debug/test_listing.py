import asyncio, sys
from app.scraper.divar_scraper import DivarScraper
from app.config import get_settings
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import NullPool
from bs4 import BeautifulSoup

settings = get_settings()

async def test():
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    async with async_sessionmaker(engine, class_=AsyncSession)() as session:
        scraper = DivarScraper(db_session=session, headless=True)
        ok = await scraper.initialize()
        print('init:', ok)
        await scraper.page.goto('https://divar.ir/s/urmia/buy-residential', wait_until='domcontentloaded', timeout=30000)
        await asyncio.sleep(5)
        await scraper.page.screenshot(path='/app/data/debug/listing_test.png')
        url = scraper.page.url
        title = await scraper.page.title()
        content = await scraper.page.content()
        soup = BeautifulSoup(content, 'lxml')
        print('url:', url)
        print('title:', title)
        print('links /v/:', len(soup.select('a[href*="/v/"]')))
        print('cards:', len(soup.select('a.kt-post-card__action')))
        # Print first 500 chars of body text
        body = soup.find('body')
        if body:
            print('body snippet:', body.get_text()[:300])
        await scraper.close()
    await engine.dispose()

asyncio.run(test())
