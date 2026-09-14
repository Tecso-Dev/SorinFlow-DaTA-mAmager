"""
«زمان تا اولین آگهی ۲:۳۰ دقیقه طول کشید و خیلی زیاده. اینو حذف کن و مستقیم برو
اسکرپ کنه.»

That time was the browser walking the listing page: a real Chromium
scrolling the feed a batch at a time. The same feed is one POST per 24
listings to the search API the count already uses — public, sessionless —
and a whole city comes back in seconds. Proven against Divar itself before
this was written: 198 listings, 9 pages, 7.0s.

The browser walk is kept, as the fallback for the day the API's shape
changes underneath us. It is no longer the wait.
"""
import inspect
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_apic.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

import pytest  # noqa: E402
from app.services import divar_count as dc  # noqa: E402
from app.scraper.divar_scraper import DivarScraper  # noqa: E402


def widget(token, title="آگهی", when="۳ ساعت پیش"):
    return {"widget_type": "POST_ROW", "data": {
        "token": token, "title": title,
        "top_description_text": "ودیعه: ۵۰۰,۰۰۰,۰۰۰ تومان",
        "middle_description_text": "اجاره: ۱۰,۰۰۰,۰۰۰ تومان",
        "bottom_description_text": when}}


def page(tokens, *, next_page=True, last_post_date="2026-09-14T10:00:00Z", extra=()):
    return {
        "list_widgets": [widget(t) for t in tokens] + list(extra),
        "pagination": {"has_next_page": next_page,
                       "data": {"page": 1, "last_post_date": last_post_date} if next_page else None},
        "map_data": {"post_count": 999},
    }


class FakeResponse:
    def __init__(self, payload, status=200):
        self._p, self.status_code = payload, status
    def json(self):
        return self._p


class FakeClient:
    """Answers each POST with the next scripted page."""
    def __init__(self, pages):
        self.pages, self.bodies = list(pages), []
    async def __aenter__(self):
        return self
    async def __aexit__(self, *a):
        return False
    async def post(self, url, json=None, headers=None):
        import copy
        self.bodies.append(copy.deepcopy(json))   # httpx serialises at send time
        return FakeResponse(self.pages.pop(0)) if self.pages else FakeResponse({}, 500)


@pytest.fixture
def divar(monkeypatch):
    """Wire a scripted client in, and a city that resolves."""
    holder = {}
    def make(pages):
        c = FakeClient(pages)
        holder["c"] = c
        monkeypatch.setattr(dc.httpx, "AsyncClient", lambda **kw: c)
        return c
    async def _city(city, client):
        return 27 if city == "urmia" else None
    monkeypatch.setattr(dc, "resolve_city_id", _city)
    async def _nosleep(d):
        return None
    monkeypatch.setattr("asyncio.sleep", _nosleep)
    return make


class TestTheRowShape:
    def test_a_widget_becomes_the_listing_the_scraper_walks(self):
        row = dc._row_from_widget(widget("gaoeU7lD", "چهارخوابه راه جدا"))
        assert row == {"divar_id": "gaoeU7lD", "url": "https://divar.ir/v/gaoeU7lD",
                       "title": "چهارخوابه راه جدا",
                       "descriptions": ["ودیعه: ۵۰۰,۰۰۰,۰۰۰ تومان", "اجاره: ۱۰,۰۰۰,۰۰۰ تومان", "۳ ساعت پیش"]}

    def test_the_title_feeds_the_category_check(self):
        """The check judges on url + source title; a titleless row is what
        used to get dropped for having no name."""
        assert dc._row_from_widget(widget("t", "اجاره آپارتمان"))["title"] == "اجاره آپارتمان"

    def test_other_widgets_are_ignored(self):
        assert dc._row_from_widget({"widget_type": "BANNER", "data": {}}) is None

    def test_a_widget_without_a_token_is_ignored(self):
        assert dc._row_from_widget({"widget_type": "POST_ROW", "data": {"title": "x"}}) is None


class TestPaging:
    @pytest.mark.asyncio
    async def test_it_pages_until_the_target(self, divar):
        c = divar([page(["a", "b"]), page(["c", "d"]), page(["e", "f"], next_page=False)])
        rows, err = await dc.fetch_listings("urmia", {}, target=3)
        assert err is None
        assert [r["divar_id"] for r in rows] == ["a", "b", "c"]

    @pytest.mark.asyncio
    async def test_the_cursor_is_sent_back(self, divar):
        c = divar([page(["a"]), page(["b"], next_page=False)])
        await dc.fetch_listings("urmia", {}, target=10)
        assert "pagination_data" not in c.bodies[0]
        assert c.bodies[1]["pagination_data"]["last_post_date"] == "2026-09-14T10:00:00Z"

    @pytest.mark.asyncio
    async def test_it_stops_when_divar_says_there_is_no_next_page(self, divar):
        c = divar([page(["a"], next_page=False)])
        rows, _ = await dc.fetch_listings("urmia", {}, target=100)
        assert len(rows) == 1 and len(c.bodies) == 1

    @pytest.mark.asyncio
    async def test_a_stuck_cursor_does_not_spin(self, divar):
        """Divar occasionally returns the same page again; that is the end,
        not an invitation to loop eighty times."""
        c = divar([page(["a"]), page(["a"]), page(["a"]), page(["a"])])
        rows, _ = await dc.fetch_listings("urmia", {}, target=100)
        assert len(rows) == 1 and len(c.bodies) == 2

    @pytest.mark.asyncio
    async def test_duplicates_across_pages_are_dropped(self, divar):
        divar([page(["a", "b"]), page(["b", "c"], next_page=False)])
        rows, _ = await dc.fetch_listings("urmia", {}, target=10)
        assert [r["divar_id"] for r in rows] == ["a", "b", "c"]

    @pytest.mark.asyncio
    async def test_progress_is_reported_per_page(self, divar):
        divar([page(["a"]), page(["b"], next_page=False)])
        seen = []
        async def on_page(p, fresh, total):
            seen.append((p, fresh, total))
        await dc.fetch_listings("urmia", {}, target=10, on_page=on_page)
        assert seen == [(1, 1, 1), (2, 1, 2)]


class TestDateMode:
    @pytest.mark.asyncio
    async def test_it_stops_once_the_cursor_passes_the_day(self, divar):
        c = divar([page(["a"], last_post_date="2026-09-14T10:00:00Z"),
                   page(["b"], last_post_date="2026-09-12T10:00:00Z"),
                   page(["c"], last_post_date="2026-09-11T10:00:00Z")])
        rows, _ = await dc.fetch_listings("urmia", {}, target=1, until_day=date(2026, 9, 13))
        assert [r["divar_id"] for r in rows] == ["a", "b"]
        assert len(c.bodies) == 2

    @pytest.mark.asyncio
    async def test_the_target_is_not_a_stop_in_date_mode(self, divar):
        """A day holds however many it holds."""
        divar([page(["a", "b", "c"]), page(["d"], next_page=False)])
        rows, _ = await dc.fetch_listings("urmia", {}, target=1, until_day=date(2020, 1, 1))
        assert len(rows) == 4


class TestFailure:
    @pytest.mark.asyncio
    async def test_an_unknown_city_is_a_sentence_not_an_exception(self, divar):
        divar([])
        rows, err = await dc.fetch_listings("atlantis", {}, target=5)
        assert rows == [] and "atlantis" in err

    @pytest.mark.asyncio
    async def test_a_server_error_returns_what_was_gathered_and_why(self, divar):
        divar([page(["a"])])          # the second POST gets a 500
        rows, err = await dc.fetch_listings("urmia", {}, target=10)
        assert [r["divar_id"] for r in rows] == ["a"]
        assert "500" in err


class TestTheScraperGoesToItFirst:
    SRC = inspect.getsource(DivarScraper._collect_listings_robust)

    def test_the_api_is_strategy_zero(self):
        assert self.SRC.index("_dc.fetch_listings(") < self.SRC.index("_collect_from_browser_dom(")

    def test_a_full_answer_skips_the_browser_walk(self):
        i = self.SRC.index("if listings:")
        block = self.SRC[i:self.SRC.index("logger.warning", i)]
        assert "return all_listings" in block

    def test_an_empty_answer_falls_through_with_the_reason(self):
        assert "falling back to the browser walk" in self.SRC
        assert "به پیمایش مرورگر برمی‌گردیم" in self.SRC

    def test_the_run_log_says_which_path_it_took(self):
        assert "بدون پیمایش مرورگر" in self.SRC

    def test_the_form_is_built_from_the_run_s_own_filters(self):
        src = inspect.getsource(DivarScraper.start_scraping_job)
        assert "self._search_form = _bfd(" in src
        assert "advertiser_type=advertiser_type" in src[src.index("self._search_form"):][:400]
