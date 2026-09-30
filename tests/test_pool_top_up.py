"""
A count run does not run out of candidates while Divar still has them (#30).

«Ran out of candidates» appeared twice in the log. The pool a count run
walks is twice the target plus a page, collected before the first listing
is opened — 124 for 50. Every filter Divar cannot apply itself (rooms, the
amenity boxes, price per metre, the advertiser check that drops «unknown»)
is applied after opening, and a listing earlier runs already saved counts
as «از قبل موجود» rather than new. In a big city both eat most of a pool
that size, and the run ended «آگهی بیشتری پیدا نشد … یا فیلترها خیلی
تنگ‌اند» with Divar holding thousands more.

Now the walk tops the pool up from where the search stopped when it
reaches the last candidate short of its target: until the target is met,
Divar's list really ends, or the per-run ceiling.

The first half of #30 — skipping an old bumped listing on its search card,
before opening it — is not here: nothing in the repository records what a
search card carries about its date, and a guess would risk dropping
listings of the very day asked for.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_top_up.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

import pytest  # noqa: E402
from _scripted_run import THE_END_SENTENCE, page, refused, scripted_run, tokens  # noqa: E402

from app.scraper.divar_scraper import DivarScraper  # noqa: E402


@pytest.fixture
def run(monkeypatch):
    return scripted_run(monkeypatch)


def lift_by_token(url):
    """Listings «so…» have no lift, «bg…» do: a «with a lift» run keeps only
    the second kind. Divar has no lift filter for «اجاره مسکونی» (#27), so
    the scraper checks it itself after opening each ad — the filter this
    file needs. (It used rooms on «اجاره آپارتمان», which Divar now
    narrows on itself.)"""
    return {"title": "آپارتمان آزمایشی", "has_elevator": "/bg" in url}


# Three pages of one-room flats, then three-room ones further down the feed.
SMALL_FIRST = [page(1, tokens("so", 24)), page(2, tokens("so", 24, 24)), page(3, tokens("so", 24, 48)),
               page(4, tokens("bg", 24)), page(5, tokens("bg", 24, 24)),
               page(6, tokens("bg", 24, 48), next_page=False)]


class TestThePoolIsToppedUp:

    async def test_a_pool_the_filters_ate_pages_on_until_the_target(self, run):
        job, log, divar = await run(SMALL_FIRST, category="rent-residential", max_items=10,
                                    has_elevator=True, held=False, detail=lift_by_token)
        assert (job.status, job.new_items) == ("completed", 10), \
            "a run for 10 ended short with 72 three-room flats still on Divar"
        assert job.finish_reason is None or THE_END_SENTENCE not in job.finish_reason
        assert any("نامزد دیگر از دیوار گرفته شد" in m for m in log.messages())
        assert divar.pages_after_the_first() == 4, \
            "pages 2-3 for the first pool, 4-5 for the top-up — not the whole feed"

    async def test_the_top_up_goes_no_further_than_divar_s_list(self, run):
        """Only five three-room flats exist: the run gets them, and this time
        «Divar has no more» is the truth."""
        feed = SMALL_FIRST[:3] + [page(4, tokens("bg", 5), next_page=False)]
        job, _, _ = await run(feed, category="rent-residential", max_items=10,
                              has_elevator=True, held=False, detail=lift_by_token)
        assert (job.status, job.new_items) == ("completed", 5)
        assert THE_END_SENTENCE in job.finish_reason

    async def test_listings_the_database_already_holds_do_not_end_the_run(self, run):
        """A city scraped every day: its first pages are all «از قبل موجود»."""
        feed = [page(1, tokens("dup", 24)), page(2, tokens("dup", 24, 24)), page(3, tokens("dup", 24, 48)),
                page(4, tokens("nw", 24)), page(5, tokens("nw", 24, 24), next_page=False)]
        job, _, _ = await run(feed, category="rent-apartment", max_items=10,
                              held=lambda divar_id: divar_id.startswith("dup"),
                              detail={"title": "آپارتمان"})
        assert (job.status, job.new_items) == ("completed", 10)
        # held and complete, so «تکراری» — not opened again, not «بروز» (#32)
        assert job.config["outcome"]["duplicate"] == 72, \
            "every held listing of the first three pages was walked"


class TestItStopsWhereItShould:

    async def test_a_refusal_while_topping_up_ends_partial_with_its_page(self, run):
        feed = SMALL_FIRST[:3] + [refused(429, "rate limit exceeded")]
        job, log, _ = await run(feed, category="rent-residential", max_items=10,
                                has_elevator=True, held=False, detail=lift_by_token)
        assert job.status == "partial"
        assert "صفحهٔ 4" in job.finish_reason and "HTTP 429" in job.finish_reason
        assert any(m.startswith("ادامهٔ جمع‌آوری ناقص ماند") for m in log.messages())

    async def test_the_pool_never_passes_the_ceiling(self, run, monkeypatch):
        monkeypatch.setattr(DivarScraper, "POOL_CEILING", 100)
        endless = [page(n + 1, tokens("so", 24, 24 * n)) for n in range(10)]
        job, _, divar = await run(endless, category="rent-residential", max_items=10,
                                  has_elevator=True, held=False, detail=lift_by_token)
        assert job.status == "partial" and job.new_items == 0
        assert "(100 نامزد)" in job.finish_reason, job.finish_reason
        assert THE_END_SENTENCE not in job.finish_reason
        assert divar.pages_after_the_first() <= 4

    async def test_a_date_run_is_not_topped_up(self, run):
        """Its pool is the day, walked to its end already."""
        feed = [page(1, tokens("dy", 24), cursor="2026-09-14T10:00:00Z"),
                page(2, tokens("dy", 24, 24), cursor="2026-09-13T19:00:00Z"),
                page(3, tokens("dy", 24, 48), cursor="2026-09-12T19:00:00Z")]
        job, _, divar = await run(feed, category="rent-apartment", max_items=5,
                                  posted_date="2026-09-14")
        assert job.status == "completed" and divar.pages_after_the_first() == 1

    async def test_an_explicit_list_is_not_topped_up(self, run):
        job, _, divar = await run([page(1, tokens("ex", 24))], category="اسکرپ تکی", max_items=3,
                                  held=False, detail={"title": "x", "rooms": 1}, min_rooms=3,
                                  urls=["https://divar.ir/v/exAAAA01"])
        assert divar.searches == [] and job.status == "completed"


class TestTheTopUpOnItsOwn:

    async def test_rows_past_the_target_are_walked_before_asking_divar_again(self):
        s = DivarScraper.__new__(DivarScraper)
        s._job_id_str = None
        left = [{"divar_id": t, "url": "u", "title": "t", "descriptions": []} for t in tokens("lf", 5)]
        s._feed_more = {"city": "urmia", "form": {}, "cursor": None, "page": 3, "leftover": left}
        pool, seen = [], set()
        assert await s._top_up_pool(pool, seen, 3) == 3
        assert [r["divar_id"] for r in pool] == tokens("lf", 3)
        assert await s._top_up_pool(pool, seen, 10) == 2
        assert s._feed_more is None, "no cursor and nothing left: the list is used up"
        assert await s._top_up_pool(pool, seen, 10) == 0

    async def test_nothing_to_top_up_from_is_a_no_op(self):
        s = DivarScraper.__new__(DivarScraper)
        assert await s._top_up_pool([], set(), 10) == 0


class TestWhatTheTopUpLeavesOnTheRow:
    """Found in the review of the merged fixes: the pool grew, «کل» did not."""

    async def test_the_pool_total_grows_with_the_top_up(self, run):
        """«کل» is the run's own pool (#29). With Divar's count present it
        stayed at the first pool while «بررسی» walked on: «82 / 60», a bar
        clamped full from the moment the top-up began."""
        job, _, _ = await run(SMALL_FIRST, category="rent-residential", max_items=10,
                              has_elevator=True, held=False, detail=lift_by_token)
        assert job.divar_count, "the case needs Divar's own count on the row"
        pool = job.config["outcome"]["pool"]
        assert pool > 72, "the top-up must actually have run"
        assert job.total_items == pool
        assert job.scraped_items <= job.total_items

    async def test_a_listing_a_top_up_page_brings_again_is_not_opened_twice(self, run):
        """Divar repeats an ad further down — a promoted one — and the top-up
        must not add it a second time: a second reveal on the owner's number
        and one more candidate than Divar has (#57). (It used to be tested with
        a numberless listing from an earlier run put first in the pool; the
        pool is Divar's list only now, #58.)"""
        again = tokens("so", 1)[0]          # page 1's first ad, again on page 4
        feed = SMALL_FIRST[:3] + [page(4, [again] + tokens("bg", 24))] + SMALL_FIRST[4:]
        opened = []

        def detail(url):
            opened.append(url)
            return lift_by_token(url)
        job, _, _ = await run(feed, category="rent-residential", max_items=10,
                              has_elevator=True, held=False, detail=detail)
        assert opened.count(f"https://divar.ir/v/{again}") == 1, opened[:5]
        assert len(opened) == len(set(opened))
