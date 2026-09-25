"""
A listing Divar has deleted is not a listing to scrape.

Job 076c865a, 2026-09-25, «اسکرپ تکی» of https://divar.ir/v/gaMCCCUG: Divar
answered with 410 Gone and put similar ads under the page. The run looked for
a contact button (there was none), counted a reveal, downloaded twenty photos
of those other ads, and filed the listing as «ذخیره نشد — عنوان نبود».

The page is now recognised as soon as it loads, by its status or, when there
is no status to read, by what it says. The run stops there: no contact, no
photos, no reveal counted, and the skipped list says «در دیوار حذف شده».

The pages below are Divar's own: the 410 body as served on 2026-09-25, cut
down to the rows that matter, and the sentence the browser shows once it has
rendered the similar ads.
"""
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_deleted.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

import pytest  # noqa: E402
from _fake_redis import patch_redis  # noqa: E402

from app.models.scraping_job import ScrapingJob  # noqa: E402
from app.scraper import divar_scraper as ds  # noqa: E402
from app.scraper import otp_store  # noqa: E402
from app.scraper.divar_scraper import DivarScraper  # noqa: E402
from app.services import job_log, skipped_listings  # noqa: E402

URL = "https://divar.ir/v/gaMCCCUG"

AS_SERVED = """<html><body><main>
<div class="kt-page-title"><div class="kt-page-title__texts">
<div class="kt-page-title__title kt-page-title__title--responsive-sized">این صفحه حذف شده یا وجود ندارد</div>
</div></div>
<div class="kt-base-row kt-description-row kt-description-row--padded"><div class="kt-base-row__start">
<p class="kt-description-row__text">این صفحه حذف شده یا وجود ندارد</p></div></div>
</main></body></html>"""

RENDERED = """<html><body><main>
<p class="kt-description-row__text kt-description-row__text--primary">در پایین، آگهی‌های مشابه با آگهی حذف شده را ببینید.</p>
<div class="kt-post-card"><picture><img src="https://s100.divarcdn.com/static/photo/someone-else.jpg"></picture></div>
</main></body></html>"""

LIVE = """<html><body><main>
<h1 class="kt-page-title__title kt-page-title__title--responsive-sized">آپارتمان ۸۵ متری نوساز</h1>
<p class="kt-description-row__text kt-description-row__text--primary">آگهی حذف شده بود، دوباره گذاشتم. ۸۵ متر، طبقهٔ دوم، آسانسور.</p>
</main></body></html>"""


# A live ad before React has drawn it: no h1 yet, and its own description,
# the same words as above, inside the inline state script.
STILL_RENDERING = """<html><head><script>window.__PRELOADED_STATE__ = {"post": {"description":
"آگهی حذف شده بود، دوباره گذاشتم. ۸۵ متر، طبقهٔ دوم."}}</script></head>
<body><div id="app"></div></body></html>"""

class FakeResponse:
    def __init__(self, status):
        self.status = status


class FakePage:
    """The calls the detail scrape makes, answered from one HTML string."""

    def __init__(self, status, html, lands_on=None):
        self.status, self.html, self.lands_on, self.url = status, html, lands_on, "about:blank"

    async def goto(self, url, **_):
        self.url = self.lands_on or url
        return FakeResponse(self.status) if self.status else None

    async def wait_for_selector(self, *_a, **_k):
        return None

    async def query_selector(self, selector):
        return object() if selector == "h1" and "<h1" in self.html else None

    async def content(self):
        return self.html

    async def evaluate(self, *_a, **_k):
        return None

    async def title(self):
        return ""


class FakeSession:
    """The run's own session: it hands back the job row and holds nothing else."""

    def __init__(self, job):
        self.job = job

    async def execute(self, stmt):
        row = self.job if "scraping_jobs" in str(stmt) else None

        class Result:
            def scalar_one_or_none(self):
                return row
        return Result()

    def add(self, _):
        pass

    async def commit(self):
        pass

    async def refresh(self, _):
        pass

    async def rollback(self):
        pass


async def _nothing(*_a, **_k):
    return None


@pytest.fixture
def spent(monkeypatch):
    """Everything a listing can cost: the contact reveal, the account's reveal
    budget, the photos. Each one writes down that it happened instead."""
    calls = []

    class NoContact:
        contact_channel, needs_identity = None, False

        def __init__(self, *_a, **_k):
            calls.append("contact")

        async def get_phone_number(self):
            return None

    monkeypatch.setattr(ds, "ContactExtractor", NoContact)
    monkeypatch.setattr("app.scraper.divar_scraper.asyncio.sleep", _nothing)
    return calls


def scraper(page, calls):
    s = DivarScraper.__new__(DivarScraper)
    s.page, s.current_job, s.active_phone = page, None, None
    s._reveals_since_rotation, s.images_dir = 0, None
    s._check_rate_limit = _nothing
    s._dwell_like_a_reader = _nothing
    s._space_out_reveal = _nothing

    async def charge():
        calls.append("reveal")
        return 1

    async def download(*_a, **_k):
        calls.append("photos")
        return []

    s._charge_reveal, s.download_images = charge, download
    return s


GONE = [
    pytest.param(410, AS_SERVED, id="410-as-served"),
    pytest.param(200, RENDERED, id="words-after-render"),
    pytest.param(None, AS_SERVED, id="no-response-to-read"),
    pytest.param(200, RENDERED.replace("حذف شده", "حذف\u200cشده"), id="half-space"),
]


class TestTheDetailScrapeStopsAtTheDoor:
    @pytest.mark.parametrize("status,html", GONE)
    async def test_it_is_named_and_nothing_is_spent(self, status, html, spent):
        s = scraper(FakePage(status, html), spent)
        got = await s.scrape_property_detail(URL, target_category="اسکرپ تکی")
        assert spent == [] and getattr(s, "_reveals_this_run", 0) == 0
        assert got is None
        assert s._last_detail_error == "در دیوار حذف شده"

    async def test_a_live_ad_that_mentions_a_deleted_one_is_still_scraped(self, spent):
        """Its description says «آگهی حذف شده», and it has its title: a listing."""
        s = scraper(FakePage(200, LIVE), spent)
        got = await s.scrape_property_detail(
            URL, target_category="اسکرپ تکی", wants_contact=lambda _pd: "stop before the reveal")
        assert got and got["title"] == "آپارتمان ۸۵ متری نوساز"
        assert s._last_detail_error is None

    async def test_words_a_person_cannot_see_do_not_count(self, spent):
        """A slow page with no h1 yet, whose inline state holds the ad's own
        «آگهی حذف شده بود…»: still a listing, not a deleted one."""
        s = scraper(FakePage(200, STILL_RENDERING), spent)
        got = await s.scrape_property_detail(
            URL, target_category="اسکرپ تکی", wants_contact=lambda _pd: "stop before the reveal")
        assert got is not None and s._last_detail_error is None


class TestTheRunFilesItUnderItsOwnName:
    @pytest.fixture
    def run(self, monkeypatch, spent):
        """A job of one, the way «اسکرپ تکی» starts it, over a fake page."""
        rows, lines = [], []

        async def record(_job_id, **row):
            rows.append(row)
            return True

        async def log(_job_id, _stage, message, **_kw):
            lines.append(message)

        monkeypatch.setattr(skipped_listings, "record", record)
        monkeypatch.setattr(skipped_listings, "prune", _nothing)
        monkeypatch.setattr(job_log, "record", log)
        monkeypatch.setattr(job_log, "prune", _nothing)
        patch_redis(monkeypatch, otp_store)

        async def go(page):
            job = ScrapingJob(job_id=uuid.uuid4(), status="pending", new_items=0, updated_items=0,
                              failed_items=0, scraped_items=0, total_items=0, scraped_pages=0)
            s = scraper(page, spent)
            s.db_session = FakeSession(job)
            s.maybe_rotate_account = _nothing
            s._human_like_delay = _nothing
            await s.start_scraping_job(city="—", category="اسکرپ تکی", max_items=1,
                                       job_id=str(job.job_id), urls=[URL])
            return job, rows, lines
        return go

    @pytest.mark.parametrize("status,html", GONE[:2])
    async def test_deleted_is_its_own_reason_not_a_failure(self, run, spent, status, html):
        job, rows, lines = await run(FakePage(status, html))
        assert spent == [], "a deleted listing cost a reveal or photos"
        assert job.status == "completed"
        assert (job.new_items, job.updated_items, job.failed_items) == (0, 0, 0)
        assert [(r["divar_id"], r["reason"]) for r in rows] == [("gaMCCCUG", "deleted")]
        assert "حذف شده" in rows[0]["detail"]
        assert not any("افشا" in m for m in lines), "the finish line counted a reveal"
        tally = next(m for m in lines if "نامزد —" in m)
        assert "1 در دیوار حذف شده" in tally
        assert "ناموفق" not in tally and "بی‌حساب" not in tally

    async def test_a_page_that_would_not_open_is_still_a_failure(self, run, spent):
        """The new branch takes only what Divar called gone."""
        job, rows, _ = await run(FakePage(200, "<html></html>", lands_on="https://divar.ir/"))
        assert job.failed_items == 1
        assert [(r["reason"], r["detail"]) for r in rows] == [("failed", "صفحه باز نشد")]
        assert spent == []

    def test_the_panel_has_a_label_for_it(self):
        """/jobs/{id}/skipped labels each bucket from this table."""
        assert DivarScraper._FILTER_LABELS_FA["deleted"] == "در دیوار حذف شده"
