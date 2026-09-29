"""
A whole scrape run — start_scraping_job — against a scripted Divar.

Divar's search API answers over httpx.MockTransport (the real client, the
real request bodies and cursors), and nothing opens a browser: by default
every candidate is one the database already holds, so a run walks its pool
in milliseconds and what is under test is how the collection ended and how
the run reports it. `held=False` with a `detail` (a dict, or a function of
the listing's URL) makes every candidate a listing the run opens instead,
for the cases where meeting the target matters.

Not a conftest fixture: a test module takes it with

    @pytest.fixture
    def run(monkeypatch):
        return scripted_run(monkeypatch)

Every name, token and number here is made up.
"""
import json
import uuid

import httpx
from _fake_redis import patch_redis

from app.models.scraping_job import ScrapingJob
from app.scraper import otp_store
from app.scraper.divar_scraper import DivarScraper
from app.services import divar_count as dc
from app.services import job_log, skipped_listings

_REAL_CLIENT = httpx.AsyncClient      # captured before any test patches it

# The finish line's «Divar had no more» sentence: only for a list that ended.
THE_END_SENTENCE = "یا دیوار آگهی دیگری ندارد یا فیلترها خیلی تنگ‌اند"


def widget(token):
    return {"widget_type": "POST_ROW", "data": {
        "token": token, "title": f"آگهی آزمایشی {token}",
        "middle_description_text": "۲,۵۰۰,۰۰۰,۰۰۰ تومان"}}


def page(n, tokens, *, next_page=True, cursor="2026-09-14T10:00:00Z", count=105):
    """Divar's answer for page n. Its cursor names page n, so the request for
    the page after it says which page it follows."""
    return {"list_widgets": [widget(t) for t in tokens],
            "pagination": {"has_next_page": next_page,
                           "data": {"page": n, "last_post_date": cursor}},
            "map_data": {"post_count": count}}


def tokens(prefix, n, start=0):
    return [f"{prefix}{i:04d}x" for i in range(start, start + n)]


def listings(prefix, n):
    return [{"divar_id": t, "url": f"https://divar.ir/v/{t}", "title": "آگهی", "descriptions": []}
            for t in tokens(prefix, n)]


def refused(status, message=None):
    return httpx.Response(status, json={"message": message} if message else {})


class FakeDivar:
    """Divar's search API. `answers[k]` answers the request that follows page
    k (k=0: the first page — and the count, which asks the same question):
    a payload, an httpx.Response for anything but a 200, or an exception to
    raise for no answer at all."""

    def __init__(self, answers):
        self.answers, self.searches = list(answers), []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/places/cities"):
            return httpx.Response(200, json={"cities": [{"id": 27, "slug": "urmia", "name": "ارومیه"}]})
        body = json.loads(request.content)
        self.searches.append(body)
        after = (body.get("pagination_data") or {}).get("page", 0)
        if after >= len(self.answers):
            return httpx.Response(500, json={"message": "the script ran out"})
        answer = self.answers[after]
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, httpx.Response) else httpx.Response(200, json=answer)

    def pages_after_the_first(self):
        return sum(1 for b in self.searches if "pagination_data" in b)


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


class Log(list):
    """What the run wrote to its job log, one dict per event."""

    def messages(self):
        return [e["message"] for e in self]

    def stage(self, stage):
        return [e for e in self if e["stage"] == stage]


def walk(found, stop, *, calls, cap=None, replayable=False, replay_status=None,
         replay_pages=None):
    """For the browser path: a walk that gathers `found` and stops for
    `stop`, and a replay of Divar's search that serves `replay_pages` — each
    (listings, has_next_page) — and then brings nothing more, answering
    `replay_status`. Pass as run(..., browser=walk(...))."""
    pages = list(replay_pages or [])

    def install(s):
        async def dom(city, category, target):
            calls.append(("dom", target))
            s._collect_stop, s._dom_cursor = stop, None
            return list(found)

        async def replay(city, category, page_num, last_post_date=None):
            calls.append(("replay", page_num))
            if pages:
                rows, more = pages.pop(0)
                s._replay_status, s._replay_more = 200, more
                return list(rows), None
            s._replay_status, s._replay_more = replay_status, None
            return [], None
        s._collect_from_browser_dom, s._fetch_listings_direct_api = dom, replay
        if cap:
            s.DOM_COLLECT_CAP = cap
        if replayable:
            s._search_req_template = {"url": "https://api.divar.ir/v8/postlist/w/search",
                                      "post_data": "{}"}
    return install


def scripted_run(monkeypatch):
    """`await go(answers, **start_scraping_job kwargs)` → (job, log, divar)."""
    log = Log()

    async def record(_job_id, stage, message, *, level="info", **details):
        log.append({"stage": stage, "message": message, "level": level, **details})
        return True

    async def skipped(_job_id, **_row):
        return True

    monkeypatch.setattr(job_log, "record", record)
    monkeypatch.setattr(job_log, "prune", _nothing)
    monkeypatch.setattr(skipped_listings, "record", skipped)
    monkeypatch.setattr(skipped_listings, "prune", _nothing)
    patch_redis(monkeypatch, otp_store)
    monkeypatch.setattr(dc, "_city_cache", {})
    monkeypatch.setattr(dc, "_PAGE_PAUSE", 0)
    monkeypatch.setattr("app.scraper.divar_scraper.asyncio.sleep", _nothing)

    async def go(answers, *, held=True, detail=None, browser=None, **kw):
        fake = FakeDivar(answers)

        class Client(_REAL_CLIENT):
            def __init__(self, *a, **k):
                k["transport"] = httpx.MockTransport(fake)
                super().__init__(*a, **k)
        monkeypatch.setattr(dc.httpx, "AsyncClient", Client)

        job = ScrapingJob(job_id=uuid.uuid4(), status="pending", new_items=0, updated_items=0,
                          failed_items=0, scraped_items=0, total_items=0, scraped_pages=0,
                          config={"city": "urmia", "category": kw.get("category")})
        s = DivarScraper.__new__(DivarScraper)
        s.db_session = FakeSession(job)
        s.page, s.current_job, s.active_phone, s.images_dir = None, None, None, None
        s._search_req_template = None
        s._phone_required = False
        s.maybe_rotate_account = _nothing
        s._human_like_delay = _nothing
        s._recycle_browser = _nothing
        opened = []

        async def exists(divar_id):
            return held(divar_id) if callable(held) else held
        s.property_exists = exists

        async def open_page(url, **_kw):
            opened.append(url)
            page_says = detail(url) if callable(detail) else detail
            return dict(page_says or {}, url=url)
        s.scrape_property_detail = open_page

        async def save(property_data):
            s._last_save_created = True
            return object()
        s.save_property = save
        s.opened = opened

        if browser is not None:
            browser(s)

        kw.setdefault("max_items", 50)
        await s.start_scraping_job(city="urmia", download_images=False,
                                   job_id=str(job.job_id), **kw)
        return job, log, fake
    return go
