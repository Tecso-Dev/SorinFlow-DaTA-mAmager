"""
A whole run, planned against the category's own filters (#27, #33).

start_scraping_job against a scripted Divar (tests/_scripted_run.py): the
real search requests, the real job log, the real local filters. What is
checked is what Divar is asked, what the log says was left out, and that the
scraper does not redo — or skip — the filtering Divar did.

Every name, token and number here is made up.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_filter_run.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

import pytest  # noqa: E402
from _scripted_run import page, scripted_run, tokens  # noqa: E402

from app.services import divar_count as dc  # noqa: E402
from app.services import divar_filters as df  # noqa: E402


@pytest.fixture
def run(monkeypatch):
    df.forget()                  # the committed schema, whatever ran before
    yield scripted_run(monkeypatch)
    df.forget()


def forms(divar):
    return [b["search_data"]["form_data"]["data"] for b in divar.searches]


def yesterday() -> str:
    return (dc.tehran_today() - timedelta(days=1)).isoformat()


ONE_PAGE = [page(1, tokens("fl", 6), next_page=False)]


class TestWhatDivarIsAsked:

    async def test_a_sale_is_never_sent_a_deposit(self, run):
        """The bug: credit on apartment-sell, and Divar refused page 2."""
        _, log, divar = await run(ONE_PAGE, category="buy-apartment",
                                  min_deposit=100_000_000, max_deposit=500_000_000,
                                  max_rent=10_000_000)
        for form in forms(divar):
            assert "credit" not in form and "rent" not in form
        assert any("ودیعه برای دستهٔ «خرید آپارتمان» معنا ندارد و اعمال نشد" in m
                   for m in log.messages()), log.messages()

    async def test_every_filter_the_category_has_is_sent(self, run):
        _, log, divar = await run(ONE_PAGE, category="buy-apartment",
                                  min_rooms=2, max_rooms=3, has_elevator=True,
                                  has_storage=True, min_price_per_meter=40_000_000,
                                  divar_filters={"building-age": {"max": 5}, "deed_type": ["single_page"]})
        form = forms(divar)[0]
        assert form["rooms"] == {"repeated_string": {"value": ["دو", "سه"]}}
        assert form["elevator"] == {"boolean": {"value": True}}
        assert form["warehouse"] == {"boolean": {"value": True}}
        assert form["price_per_square"] == {"number_range": {"minimum": "40000000"}}
        assert form["building-age"] == {"number_range": {"maximum": "5"}}
        assert form["deed_type"] == {"repeated_string": {"value": ["single_page"]}}
        line = next(m for m in log.messages() if m.startswith("فیلترها به خود دیوار داده شد"))
        assert "elevator=true" in line and "building-age=-5" in line

    async def test_a_date_run_asks_for_recent_ads(self, run):
        _, log, divar = await run(ONE_PAGE, category="rent-apartment", posted_date=yesterday(),
                                  max_items=None)
        assert forms(divar)[0]["recent_ads"] == {"str": {"value": "3d"}}
        assert any("آگهی‌های اخیر: 3d" in m and "روز دقیق را اسکرپر خودش بررسی می‌کند" in m
                   for m in log.messages())

    async def test_the_count_asks_the_same_question_as_the_search(self, run):
        _, _, divar = await run(ONE_PAGE, category="buy-apartment", min_rooms=2, has_parking=True)
        sent = forms(divar)
        assert len(sent) >= 2, "the search and the count"
        assert all(f == sent[0] for f in sent)

    async def test_an_old_config_runs_as_before(self, run):
        """A saved run from before #27: only the old fields, no divar_filters."""
        _, _, divar = await run(ONE_PAGE, category="rent-residential", advertiser_type="personal",
                                has_images=True, max_deposit=100_000_000, min_area=80)
        assert forms(divar)[0] == {
            "category": {"str": {"value": "residential-rent"}},
            "business-type": {"repeated_string": {"value": ["personal"]}},
            "has-photo": {"boolean": {"value": True}},
            "credit": {"number_range": {"maximum": "100000000"}},
            "size": {"number_range": {"minimum": "80"}},
        }


class TestTheScraperDoesNotRedoDivarsWork:

    async def test_rooms_divar_filtered_are_not_checked_again(self, run):
        """Divar narrowed buy-apartment to two or three rooms. A listing whose
        page reads five (a typo, a basement counted) is Divar's call."""
        job, _, _ = await run(ONE_PAGE, category="buy-apartment", max_items=3,
                              min_rooms=2, max_rooms=3, held=False,
                              detail={"title": "آپارتمان آزمایشی", "rooms": 5})
        assert job.new_items == 3

    async def test_a_filter_divar_lacks_for_the_category_is_still_checked(self, run):
        """rent-residential has no lift filter at Divar: the scraper checks it."""
        job, _, divar = await run(ONE_PAGE, category="rent-residential", max_items=3,
                                  has_elevator=True, held=False,
                                  detail={"title": "آپارتمان آزمایشی", "has_elevator": False})
        assert job.new_items == 0
        assert "elevator" not in forms(divar)[0]

    async def test_a_band_bistar_only_approximates_is_still_checked(self, run):
        job, _, _ = await run(ONE_PAGE, category="buy-apartment", max_items=3,
                              min_rooms=2, max_rooms=6, held=False,
                              detail={"title": "آپارتمان آزمایشی", "rooms": 8})
        assert job.new_items == 0

    async def test_an_explicit_list_is_checked_in_full(self, run):
        """Nothing was searched, so Divar filtered nothing, and no note is
        written about filters a search would have carried."""
        job, log, divar = await run([], category="buy-apartment", max_items=3,
                                    min_rooms=3, min_deposit=5, held=False,
                                    detail={"title": "آپارتمان آزمایشی", "rooms": 1},
                                    urls=["https://divar.ir/v/lsAAAA01"])
        assert divar.searches == [] and job.new_items == 0
        assert not any("اعمال نشد" in m for m in log.messages())


class TestAScheduleForYesterdayAsksForThreeDays:
    """#33: the relative date of a schedule also becomes recent_ads. The
    schedule goes through config_for_run → _launch_job (which stores the
    config on the row) → the queue's job_kwargs → run_scraping_job →
    start_scraping_job: the same path as «شروع», so the same plan."""

    async def test_the_scheduled_run_sends_recent_ads_3d(self, run):
        from app.models.scraping_job import ScrapingJob
        from app.schemas import ScrapingJobCreate
        from app.services import scrape_queue, scrape_scheduler

        saved = {"city": "urmia", "category": "rent-apartment", "posted_days_ago": 1,
                 "min_rooms": 2, "divar_filters": {"rebuilt": True}}
        now = datetime.now(timezone.utc)
        cfg = scrape_scheduler.config_for_run(saved, now=now)
        stored = {**ScrapingJobCreate(**cfg).model_dump(), "owner_user_id": 1}   # as _launch_job keeps it
        kwargs = scrape_queue.job_kwargs(ScrapingJob(job_id="00000000-0000-0000-0000-000000000033",
                                                     config=stored))
        assert kwargs["posted_date"] == (scrape_scheduler.tehran_day(now) - timedelta(days=1)).isoformat()
        assert kwargs["divar_filters"] == {"rebuilt": True}
        run_kw = {k: v for k, v in kwargs.items()
                  if k not in ("job_id", "db_url", "owner_user_id", "divar_phone", "city",
                               "download_images")}
        _, _, divar = await run(ONE_PAGE, **run_kw)
        form = forms(divar)[0]
        assert form["recent_ads"] == {"str": {"value": "3d"}}
        assert form["rebuilt"] == {"boolean": {"value": True}}
        assert form["rooms"] == {"repeated_string": {"value": ["دو", "سه", "چهار", "بیشتر"]}}
