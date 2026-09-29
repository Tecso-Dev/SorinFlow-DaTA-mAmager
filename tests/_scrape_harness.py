"""
A whole scrape run against the real database, with Divar replaced.

Not a test module (no test_ prefix), imported the way _fake_redis is. What is
fake: the browser. The collection hands back a fixed feed, a listing page is a
dict, and nothing is revealed, downloaded or slept on. What is real: the
run's own loop, its counters, property_exists and save_property on Postgres,
the skipped-listings rows and the job log. So a test here asserts what a run
actually wrote, not what the source happens to say.

Postgres only, like CI: scraping_jobs.job_id is a postgresql UUID and SQLite
cannot even build the schema. Every divar_id is random, so tests can share
one database without colliding on the unique index.
"""
import os
import sys
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

from app.scraper.divar_scraper import DivarScraper

CITY, CATEGORY = "urmia", "rent-apartment"

# Obviously fake numbers — the repository is public.
PHONE = "09120000{:03d}"

# Whose runs these are. Set per test (see fresh_owner) so that a run only
# retries the numberless listings its own test left behind — a run picks
# those up by city, category AND owner.
OWNER = None


def fresh_owner(monkeypatch) -> int:
    import random
    owner = random.randint(10 ** 6, 10 ** 9)
    monkeypatch.setattr(sys.modules[__name__], "OWNER", owner)
    return owner


def token() -> str:
    """A listing token in Divar's shape and nobody's listing."""
    return "fk" + uuid.uuid4().hex[:8]


def url_of(tok: str) -> str:
    return f"https://divar.ir/v/fake-listing/{tok}"


def page(tok: str, *, phone: Optional[str] = None, posted: Optional[datetime] = None,
         channel: Optional[str] = None, **extra) -> Dict[str, Any]:
    """What a listing page yields: the fields save_property stores."""
    d: Dict[str, Any] = {"divar_id": tok, "url": url_of(tok), "title": f"آگهی آزمایشی {tok}",
                         "posted_at": posted or datetime.utcnow() - timedelta(hours=1)}
    if phone:
        d["phone_number"], d["contact_channel"] = phone, "phone"
    elif channel:
        d["contact_channel"] = channel
    d.update(extra)
    return d


GONE = "gone"          # the page says Divar deleted the listing
BROKEN = "broken"      # the page would not open
RAISE = "raise"        # something threw while the listing was being processed


class FakeScraper(DivarScraper):
    """DivarScraper with the browser taken out.

    feed    the listings the collection phase returns, in order
    pages   divar_id → page() dict, GONE, BROKEN or False (off-category)
    """

    def __init__(self, session, *, feed: Iterable[str] = (), pages=None, on_open=None):
        super().__init__(db_session=session)
        self.feed = list(feed)
        self.pages = dict(pages or {})
        self.opened: List[str] = []      # every page the run loaded
        self.revealed: List[str] = []    # …and the ones it asked Divar for a number on
        # awaited with the token as each page is loaded — what the panel would
        # read about the run at that moment
        self.on_open = on_open

    async def initialize(self, *_a, **_k) -> bool:
        return True

    async def close(self):
        return None

    async def _persist_active_session(self) -> None:
        return None

    async def _recycle_browser(self, why: str) -> None:
        return None

    async def maybe_rotate_account(self) -> bool:
        return False

    async def _human_like_delay(self, *_a, **_k):
        return None

    async def download_images(self, *_a, **_k):
        return []

    async def _collect_listings_robust(self, city, category, target_count, until_day=None):
        self._collect_stop = ("end", None)
        return [{"divar_id": t, "url": url_of(t), "title": f"آگهی آزمایشی {t}"}
                for t in self.feed]

    async def scrape_property_detail(self, url, target_category=None, source_title=None,
                                     wants_contact=None):
        tok = self._token_from_url(url)
        self.opened.append(tok)
        if self.on_open is not None:
            await self.on_open(tok)
        self._last_detail_error = None
        got = self.pages.get(tok, BROKEN)
        if got == RAISE:
            raise RuntimeError("the page script threw")
        if got == GONE:
            self._last_detail_error = self.GONE_FROM_DIVAR
            return None
        if got == BROKEN:
            self._last_detail_error = "صفحه باز نشد"
            return None
        if got is False:
            self._last_category_drop = "آگهی دسته‌ای دیگر"
            return False
        detail = dict(got)
        if wants_contact is not None and wants_contact(detail):
            # The real scrape stops before the reveal: no number.
            detail.pop("phone_number", None)
            detail.pop("contact_channel", None)
            return detail
        self.revealed.append(tok)
        return detail


def pg_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    assert url.startswith("postgresql"), (
        "these tests run on Postgres, like CI (see CLAUDE.md): "
        "scraping_jobs.job_id is a postgresql UUID and SQLite cannot build the schema")
    return url


async def open_db():
    """(engine, sessionmaker) on the test database, with the schema and the
    city/category rows a run resolves its city_id and category_id from."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from app.database import Base, _seed_reference_data
    import app.models  # noqa: F401  (every table on the metadata)

    eng = create_async_engine(pg_url())
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _seed_reference_data(conn)
    # expire_on_commit=False, as run_scraping_job builds the run's session
    return eng, async_sessionmaker(eng, expire_on_commit=False)


async def new_job(maker, **config):
    """A pending job row, the way _launch_job leaves one."""
    from app.models.scraping_job import ScrapingJob
    cfg = {"city": CITY, "category": CATEGORY, "download_images": False,
           "owner_user_id": OWNER, **config}
    async with maker() as s:
        job = ScrapingJob(status="pending", config=cfg, created_at=datetime.now())
        s.add(job)
        await s.commit()
        return str(job.job_id)


async def stored(maker, tok: str, *, phone: Optional[str] = None,
                 channel: Optional[str] = None, city_name="ارومیه",
                 category_name="اجاره آپارتمان"):
    """A listing an earlier run already saved."""
    from app.models.property import Property
    async with maker() as s:
        s.add(Property(divar_id=tok, url=url_of(tok), title=f"آگهی آزمایشی {tok}",
                       tag_number=f"T-{tok}", phone_number=phone, contact_channel=channel,
                       city_name=city_name, category_name=category_name))
        await s.commit()


async def run(maker, job_id: str, scraper_kw: dict, **run_kw):
    """start_scraping_job on a fresh session, the way the worker runs it.
    Returns (job row as it ended, the fake scraper)."""
    from sqlalchemy import select
    from app.models.scraping_job import ScrapingJob
    async with maker() as session:
        s = FakeScraper(session, **scraper_kw)
        cfg = (await session.execute(select(ScrapingJob.config).where(
            ScrapingJob.job_id == uuid.UUID(job_id)))).scalar_one()
        kw = {"city": cfg.get("city", CITY), "category": cfg.get("category", CATEGORY),
              "max_items": cfg.get("max_items"), "download_images": False,
              "posted_date": cfg.get("posted_date"), "urls": cfg.get("urls")}
        kw.update(run_kw)
        await s.start_scraping_job(job_id=job_id, **kw)
    return await job_row(maker, job_id), s


async def job_row(maker, job_id: str):
    from sqlalchemy import select
    from app.models.scraping_job import ScrapingJob
    async with maker() as s:
        return (await s.execute(select(ScrapingJob).where(
            ScrapingJob.job_id == uuid.UUID(job_id)))).scalar_one()


async def property_row(maker, tok: str):
    from sqlalchemy import select
    from app.models.property import Property
    async with maker() as s:
        return (await s.execute(select(Property).where(Property.divar_id == tok))).scalar_one_or_none()


async def skipped_rows(maker, *, job_id: Optional[str] = None, divar_id: Optional[str] = None):
    from sqlalchemy import select
    from app.models.scraping_job import SkippedListing
    q = select(SkippedListing).order_by(SkippedListing.id)
    if job_id:
        q = q.where(SkippedListing.job_id == uuid.UUID(job_id))
    if divar_id:
        q = q.where(SkippedListing.divar_id == divar_id)
    async with maker() as s:
        return (await s.execute(q)).scalars().all()


async def log_lines(maker, job_id: str) -> List[str]:
    from app.services import job_log
    async with maker() as s:
        return [r.message for r in await job_log.events_for(s, uuid.UUID(job_id))]


def quiet(monkeypatch, divar_says: Optional[int] = None) -> int:
    """Nothing leaves the process: Divar's own count is a stub, the CRM
    pipeline does not notify anyone, and the OTP store is a fake Redis.
    Returns this test's owner id (see OWNER)."""
    from _fake_redis import patch_redis
    from app.scraper import otp_store
    from app.services import divar_count

    async def _count(_city, _form):
        return (divar_says, None) if divar_says is not None else (None, "stubbed")

    async def _no_pipeline(*_a, **_k):
        return None

    monkeypatch.setattr(divar_count, "fetch_post_count", _count)
    monkeypatch.setattr("app.crm.pipeline.process_new_property", _no_pipeline)
    patch_redis(monkeypatch, otp_store)
    return fresh_owner(monkeypatch)
