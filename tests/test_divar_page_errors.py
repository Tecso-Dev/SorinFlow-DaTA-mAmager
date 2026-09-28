"""
A later page Divar refuses is not the end of Divar's list (#28).

Run 44 logged «[api] page 1: +24 → 24» and then «API-first: 24 listings» —
no error anywhere, though page 2 had come back 400. Run 47 asked for 50 new
shops; Divar said it had 105, 24 candidates were collected because page 2
was refused, and the run finished «تکمیل شده» with the finish line «یا دیوار
آگهی دیگری ندارد یا فیلترها خیلی تنگ‌اند» — the wrong reason, on a run that
was not complete.

fetch_listings returned the error as a sentence beside the listings, and the
collector threw the sentence away whenever it had listings in hand. And in
date mode the day of Divar's cursor was the UTC day, so a cursor at 01:30
Tehran time read as the day before.

Everything here goes through httpx.MockTransport: the real client, the real
request bodies and cursors, Divar's answers scripted per page.
"""
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_page_errors.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

import httpx  # noqa: E402
import pytest  # noqa: E402

from app.services import divar_count as dc  # noqa: E402
from app.scraper.divar_scraper import DivarScraper  # noqa: E402

_REAL_CLIENT = httpx.AsyncClient      # captured before any patching


def widget(token):
    return {"widget_type": "POST_ROW", "data": {
        "token": token, "title": f"آگهی آزمایشی {token}",
        "top_description_text": "۱۲۰ متر", "bottom_description_text": "در محلهٔ آزمایشی"}}


def page(n, tokens, *, next_page=True, cursor="2026-09-14T10:00:00Z", count=105):
    """Divar's answer for page n: the cursor it hands back names page n, so
    the request for page n+1 says which page it follows."""
    return {"list_widgets": [widget(t) for t in tokens],
            "pagination": {"has_next_page": next_page,
                           "data": {"page": n, "last_post_date": cursor}},
            "map_data": {"post_count": count}}


def tokens(prefix, n):
    return [f"{prefix}{i:04d}x" for i in range(n)]


class FakeDivar:
    """The two Divar endpoints fetch_listings talks to.

    `answers[k]` is the answer to the request for page k+1 — a payload dict,
    an httpx.Response for anything that is not a 200, or an exception to
    raise for no answer at all. A request carries the previous page's
    cursor, whose "page" says where it is.
    """

    def __init__(self, answers):
        self.answers = list(answers)
        self.searches = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/places/cities"):
            return httpx.Response(200, json={"cities": [
                {"id": 27, "slug": "urmia", "name": "ارومیه"}]})
        body = json.loads(request.content)
        self.searches.append(body)
        after = (body.get("pagination_data") or {}).get("page", 0)
        if after >= len(self.answers):
            return httpx.Response(500, json={"message": "the script ran out"})
        answer = self.answers[after]
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, httpx.Response) else httpx.Response(200, json=answer)


@pytest.fixture
def divar(monkeypatch):
    """Point divar_count's httpx at a scripted Divar, with an empty city
    cache so the city lookup goes over the same transport."""
    def make(answers):
        fake = FakeDivar(answers)

        class Client(_REAL_CLIENT):
            def __init__(self, *a, **kw):
                kw["transport"] = httpx.MockTransport(fake)
                super().__init__(*a, **kw)

        monkeypatch.setattr(dc.httpx, "AsyncClient", Client)
        monkeypatch.setattr(dc, "_city_cache", {})
        monkeypatch.setattr(dc, "_PAGE_PAUSE", 0)
        return fake
    return make


def refused(status, message=None, **headers):
    return httpx.Response(status, json={"message": message} if message else {}, headers=headers)


# ── the error on page N is kept, with what Divar said ────────────────────────

class TestALaterPageThatIsRefused:

    async def test_a_400_on_page_two_keeps_page_one_and_says_why(self, divar):
        divar([page(1, tokens("ga", 24)),
               refused(400, "invalid filter for shop-sell: credit")])
        report = dc.FeedReport()
        rows, err = await dc.fetch_listings("urmia", {}, target=124, report=report)
        assert len(rows) == 24, "page one's listings must survive page two's refusal"
        assert err and "400" in err and "invalid filter for shop-sell: credit" in err
        assert "2" in err, "the sentence names the page that was refused"
        assert (report.stop, report.page, report.status) == ("error", 2, 400)
        assert report.divar_message == "invalid filter for shop-sell: credit"

    async def test_a_429_is_named_as_a_429(self, divar):
        divar([page(1, tokens("gb", 24)), refused(429, "rate limit exceeded", **{"Retry-After": "60"})])
        report = dc.FeedReport()
        rows, err = await dc.fetch_listings("urmia", {}, target=124, report=report)
        assert len(rows) == 24 and "429" in err
        assert (report.stop, report.page, report.status) == ("error", 2, 429)
        assert report.divar_message == "rate limit exceeded"

    async def test_divar_s_nested_error_shape_is_read_too(self, divar):
        divar([page(1, tokens("gc", 24)),
               httpx.Response(400, json={"error": {"code": 3, "message": "bad filter"}})])
        report = dc.FeedReport()
        _, err = await dc.fetch_listings("urmia", {}, target=124, report=report)
        assert report.divar_message == "bad filter" and "bad filter" in err

    async def test_an_html_error_page_is_not_pasted_into_the_sentence(self, divar):
        divar([page(1, tokens("gd", 24)),
               httpx.Response(502, text="<html><body><h1>502 Bad Gateway</h1></body></html>")])
        report = dc.FeedReport()
        _, err = await dc.fetch_listings("urmia", {}, target=124, report=report)
        assert report.status == 502 and report.divar_message is None
        assert "<html" not in err and "502" in err

    async def test_no_answer_at_all_is_an_error_with_its_page(self, divar):
        divar([page(1, tokens("ge", 24)), httpx.ConnectError("connection refused")])
        report = dc.FeedReport()
        rows, err = await dc.fetch_listings("urmia", {}, target=124, report=report)
        assert len(rows) == 24 and err
        assert (report.stop, report.page, report.status) == ("error", 2, None)

    async def test_page_one_refused_is_still_an_error_with_nothing(self, divar):
        divar([refused(400, "invalid filter for shop-sell: credit")])
        report = dc.FeedReport()
        rows, err = await dc.fetch_listings("urmia", {}, target=124, report=report)
        assert rows == [] and "400" in err and (report.stop, report.page) == ("error", 1)


# ── how the walk ended, when nothing went wrong ─────────────────────────────

class TestTheWayItEndedIsNamed:

    async def test_divar_saying_there_is_no_next_page_is_the_end(self, divar):
        divar([page(1, tokens("ha", 24)), page(2, tokens("hb", 10), next_page=False)])
        report = dc.FeedReport()
        rows, err = await dc.fetch_listings("urmia", {}, target=124, report=report)
        assert err is None and len(rows) == 34 and report.stop == "end"

    async def test_an_empty_last_page_is_the_end(self, divar):
        divar([page(1, tokens("hc", 24)), page(2, [], next_page=False)])
        report = dc.FeedReport()
        rows, err = await dc.fetch_listings("urmia", {}, target=124, report=report)
        assert err is None and len(rows) == 24 and report.stop == "end"

    async def test_an_empty_page_that_promises_more_is_not_the_end(self, divar):
        """Divar said there is a next page and gave nothing on this one. The
        walk stops rather than spin, but that is not the bottom of the list."""
        divar([page(1, tokens("hd", 24)), page(2, [], next_page=True)])
        report = dc.FeedReport()
        rows, err = await dc.fetch_listings("urmia", {}, target=124, report=report)
        assert err is None and len(rows) == 24
        assert report.stop == "stuck" and report.page == 2

    async def test_reaching_the_target_says_so_and_keeps_the_way_on(self, divar):
        divar([page(1, tokens("he", 24)), page(2, tokens("hf", 24)), page(3, tokens("hg", 24))])
        report = dc.FeedReport()
        rows, _ = await dc.fetch_listings("urmia", {}, target=30, report=report)
        assert len(rows) == 30 and report.stop == "target"
        assert report.cursor == {"page": 2, "last_post_date": "2026-09-14T10:00:00Z"}, \
            "where the next page starts, so the pool can be topped up later"
        assert [r["divar_id"] for r in report.leftover] == tokens("hf", 24)[6:], \
            "the rows past the target are kept, not lost to the slice"

    async def test_the_page_cap_is_not_the_end(self, divar, monkeypatch):
        monkeypatch.setattr(dc, "_MAX_PAGES", 2)
        divar([page(1, tokens("hh", 24)), page(2, tokens("hi", 24)), page(3, tokens("hj", 24))])
        report = dc.FeedReport()
        rows, err = await dc.fetch_listings("urmia", {}, target=500, report=report)
        assert err is None and len(rows) == 48 and report.stop == "cap"

    async def test_the_old_two_value_shape_still_works_without_a_report(self, divar):
        divar([page(1, tokens("hk", 24)), refused(400, "nope")])
        rows, err = await dc.fetch_listings("urmia", {}, target=100)
        assert len(rows) == 24 and "400" in err


# ── the day of the cursor is Tehran's day ───────────────────────────────────

UTC = timezone.utc


class TestTheCursorDayIsTehrans:

    def test_22_00_utc_is_already_the_next_day_in_tehran(self):
        assert dc._cursor_day({"data": {"last_post_date": "2026-09-13T22:00:00Z"}}) == date(2026, 9, 14)

    def test_20_29_utc_is_still_the_same_day(self):
        assert dc._cursor_day({"data": {"last_post_date": "2026-09-13T20:29:00Z"}}) == date(2026, 9, 13)

    def test_20_30_utc_is_tehran_midnight(self):
        assert dc._cursor_day({"data": {"last_post_date": "2026-09-13T20:30:00Z"}}) == date(2026, 9, 14)

    @pytest.mark.parametrize("raw", [
        "2026-09-13T22:00:00.123456789Z",     # nanoseconds: Python 3.10 cannot parse these itself
        "2026-09-13T22:00:00.5Z",
        "2026-09-14T01:30:00+03:30",
        "2026-09-13T22:00:00",                 # no zone: Divar's stamps are UTC
    ])
    def test_every_textual_shape(self, raw):
        assert dc._cursor_day({"data": {"last_post_date": raw}}) == date(2026, 9, 14)

    @pytest.mark.parametrize("scale", [1, 1_000, 1_000_000])
    def test_an_epoch_number_in_any_unit(self, scale):
        ts = int(datetime(2026, 9, 13, 22, 0, tzinfo=UTC).timestamp()) * scale
        assert dc._cursor_day({"data": {"last_post_date": ts}}) == date(2026, 9, 14)
        assert dc._cursor_day({"data": {"last_post_date": str(ts)}}) == date(2026, 9, 14)

    @pytest.mark.parametrize("raw", [None, "", -1, 0, "not a date", True])
    def test_nothing_usable_is_none(self, raw):
        assert dc._cursor_day({"data": {"last_post_date": raw}}) is None

    def test_no_pagination_at_all(self):
        assert dc._cursor_day(None) is None and dc._cursor_day({}) is None

    async def test_a_date_run_does_not_stop_at_utc_midnight(self, divar):
        """The feed at 01:30 Tehran on the 14th is still the 14th: stopping
        there dropped every listing posted between midnight and 03:30."""
        fake = divar([
            page(1, ["da0001x"], cursor="2026-09-14T10:00:00Z"),
            page(2, ["da0002x"], cursor="2026-09-13T22:00:00Z"),   # 01:30 on the 14th
            page(3, ["da0003x"], cursor="2026-09-13T19:00:00Z"),   # 22:30 on the 13th
            page(4, ["da0004x"], cursor="2026-09-12T19:00:00Z"),
        ])
        report = dc.FeedReport()
        rows, err = await dc.fetch_listings("urmia", {}, target=1, until_day=date(2026, 9, 14),
                                            report=report)
        assert err is None
        assert [r["divar_id"] for r in rows] == ["da0001x", "da0002x", "da0003x"]
        assert len(fake.searches) == 3 and report.stop == "day"

    def test_the_browser_path_s_cursor_is_tehran_time_too(self):
        ts = int(datetime(2026, 9, 13, 22, 0, tzinfo=UTC).timestamp())
        for scale in (1, 1_000, 1_000_000):
            moment = DivarScraper._cursor_to_datetime(ts * scale)
            assert moment.date() == date(2026, 9, 14)
            assert moment.utcoffset() == timedelta(hours=3, minutes=30)

    def test_the_browser_path_rejects_what_is_not_a_cursor(self):
        assert DivarScraper._cursor_to_datetime(None) is None
        assert DivarScraper._cursor_to_datetime(-1) is None
        assert DivarScraper._cursor_to_datetime(0) is None
