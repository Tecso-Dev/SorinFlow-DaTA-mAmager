"""
A run whose collection Divar cut short ends «ناقص», and says why (#28).

Run 47, a buy-shop search with a deposit filter and 50 new asked for:
Divar said it had 105, page 2 of the search came back 400 «invalid filter
for shop-sell: credit», 24 candidates were collected, and the run finished
«تکمیل شده» with «یا دیوار آگهی دیگری ندارد یا فیلترها خیلی تنگ‌اند» —
the wrong reason on a run that was not complete.

These drive the whole run — start_scraping_job — with Divar's search API
scripted over httpx.MockTransport and no browser: every candidate is one
the database already holds (or, where the target matters, a stubbed detail
page), so what is under test is only what the collection reports and how
the run ends.
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_cut_short.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

import httpx  # noqa: E402
import pytest  # noqa: E402
from _scripted_run import (THE_END_SENTENCE, FakeDivar, _nothing, listings, page,  # noqa: E402
                           refused, scripted_run, tokens, walk)

from app.scraper.divar_scraper import DivarScraper  # noqa: E402
from app.services import divar_count as dc  # noqa: E402
from app.services import job_log  # noqa: E402

_REAL_CLIENT = httpx.AsyncClient


@pytest.fixture
def run(monkeypatch):
    return scripted_run(monkeypatch)


# ── the run 47 case ──────────────────────────────────────────────────────────

class TestPageTwoRefused:

    async def test_it_ends_partial_with_divar_s_words_and_what_to_do(self, run):
        job, log, _ = await run(
            [page(1, tokens("ga", 24)), refused(400, "invalid filter for shop-sell: credit")],
            category="buy-store", max_deposit=100_000_000)
        assert job.status == "partial", "a run cut short at page 2 said «تکمیل شده»"
        reason = job.finish_reason
        for must in ("صفحهٔ 2", "HTTP 400", "invalid filter for shop-sell: credit",
                     "24 نامزد", "105", "«ودیعه»", "«خرید مغازه»", "خالی کنید"):
            assert must in reason, f"{must!r} missing from {reason!r}"
        assert THE_END_SENTENCE not in reason
        assert job.can_resume is True
        # «105 / 105» over 24 candidates was the other half of the lie: «کل»
        # is the run's own pool of 24 now, and Divar's 105 is kept apart (#29).
        assert (job.scraped_items, job.total_items, job.divar_count) == (24, 24, 105)

    async def test_the_run_log_names_the_page_the_status_and_divar_s_message(self, run):
        _, log, _ = await run(
            [page(1, tokens("gb", 24)), refused(400, "invalid filter for shop-sell: credit")],
            category="buy-store", max_deposit=100_000_000)
        cut = [e for e in log if e.get("status") == 400]
        assert cut, f"no log line carries the refusal: {log.messages()}"
        line = cut[0]
        assert line["page"] == 2 and line["divar_message"] == "invalid filter for shop-sell: credit"
        assert "HTTP 400" in line["message"] and "ناقص" in line["message"]
        assert line["level"] == "error"
        assert not any("بیشتر از این در دیوار نبود" in m for m in log.messages()), \
            "the log still says Divar had nothing more"
        finish = log.stage(job_log.FINISH)[-1]
        assert "ناقص" in finish["message"] and finish["level"] == "warning"
        assert THE_END_SENTENCE not in finish["message"]

    async def test_a_429_ends_partial_and_says_to_wait(self, run):
        job, _, _ = await run([page(1, tokens("gc", 24)), refused(429, "rate limit exceeded")],
                              category="rent-apartment")
        assert job.status == "partial"
        assert "HTTP 429" in job.finish_reason and "صبر کنید" in job.finish_reason

    async def test_no_answer_on_page_two_ends_partial(self, run):
        job, log, _ = await run([page(1, tokens("gd", 24)), httpx.ConnectError("connection refused")],
                                category="rent-apartment")
        assert job.status == "partial" and "صفحهٔ 2" in job.finish_reason
        assert "«ادامه»" in job.finish_reason

    async def test_an_empty_page_that_promised_more_ends_partial(self, run):
        job, _, _ = await run([page(1, tokens("ge", 24)), page(2, [], next_page=True)],
                              category="rent-apartment")
        assert job.status == "partial"
        assert "صفحهٔ 2" in job.finish_reason and THE_END_SENTENCE not in job.finish_reason

    async def test_meeting_the_target_anyway_is_a_complete_run(self, run):
        """Asked for 3 new, got them from page 1: page 2's refusal cost
        nothing the person asked for — still in the log, not in the status."""
        job, log, _ = await run(
            [page(1, tokens("gf", 24)), refused(400, "invalid filter for shop-sell: credit")],
            category="buy-store", max_items=3, held=False, detail={"title": "مغازه"})
        assert (job.status, job.new_items) == ("completed", 3)
        assert any(e.get("status") == 400 for e in log)


class TestTheEndOfTheListIsStillComplete:

    async def test_divar_saying_no_next_page_is_complete(self, run):
        job, log, _ = await run([page(1, tokens("ha", 24)), page(2, tokens("hb", 10), next_page=False)],
                                category="buy-store")
        assert job.status == "completed"
        assert THE_END_SENTENCE in job.finish_reason, "this one really did reach the end"
        assert any("بیشتر از این در دیوار نبود" in m for m in log.messages())
        assert job.can_resume is False
        assert (job.scraped_items, job.total_items) == (34, 34) and job.progress == 100.0, \
            "a finished run is finished whatever Divar said it held"

    async def test_an_empty_last_page_is_complete(self, run):
        job, _, _ = await run([page(1, tokens("hc", 24)), page(2, [], next_page=False)],
                              category="buy-store")
        assert job.status == "completed" and THE_END_SENTENCE in job.finish_reason


class TestADateRun:

    async def test_a_refused_page_before_the_day_was_covered_is_partial(self, run):
        job, _, _ = await run(
            [page(1, tokens("ja", 24), cursor="2026-09-14T10:00:00Z"), refused(503)],
            category="rent-apartment", max_items=None, posted_date="2026-09-14")
        assert job.status == "partial" and "HTTP 503" in job.finish_reason
        assert "بیش از این آگهی نداشت" not in job.finish_reason, "the day was not covered"

    async def test_a_walk_past_the_day_is_complete(self, run):
        job, _, fake = await run(
            [page(1, tokens("jb", 24), cursor="2026-09-14T10:00:00Z"),
             page(2, tokens("jc", 24), cursor="2026-09-13T22:00:00Z"),      # 01:30 on the 14th
             page(3, tokens("jd", 24), cursor="2026-09-13T19:00:00Z")],     # 22:30 on the 13th
            category="rent-apartment", max_items=10, posted_date="2026-09-14")
        assert job.status == "completed"
        assert "بیش از این آگهی نداشت" in job.finish_reason
        assert sum(1 for b in fake.searches if "pagination_data" in b) == 2, \
            "01:30 Tehran on the 14th is the 14th: the walk must go on to page 3"


# ── the browser path, when the API gives nothing ─────────────────────────────

class TestTheFallback:

    async def test_an_empty_api_answer_walks_the_browser_and_says_why(self, run):
        calls = []
        job, log, _ = await run([refused(400, "invalid filter for shop-sell: credit")],
                                category="buy-store", max_deposit=100_000_000,
                                browser=walk(listings("ka", 30), ("exhausted", None), calls=calls))
        assert [c for c in calls if c[0] == "dom"], "an empty API answer must fall back to the browser walk"
        fell = [m for m in log.messages() if "به پیمایش مرورگر برمی‌گردیم" in m]
        assert fell and "HTTP 400" in fell[0] and "invalid filter" in fell[0]
        assert job.config["outcome"]["duplicate"] == 30, "the browser's listings are the ones walked"
        assert job.status == "completed", "the browser walk reached the end of the list"

    async def test_a_full_api_answer_never_opens_the_browser_walk(self, run):
        calls = []
        _, log, _ = await run([page(1, tokens("kb", 24), next_page=False)], category="buy-store",
                              browser=walk(listings("kz", 5), ("exhausted", None), calls=calls))
        assert calls == []
        assert any("بدون پیمایش مرورگر" in m for m in log.messages())

    async def test_a_browser_walk_stopped_at_its_own_cap_is_not_the_end(self, run):
        """The walk gathers at most DOM_COLLECT_CAP; past that only the replay
        can page further. With no request captured to replay, the pool stops
        at the cap — «ناقص», not «Divar had no more»."""
        calls = []
        job, _, _ = await run([page(1, [], next_page=False)], category="buy-store",
                              browser=walk(listings("kc", 40), ("target", None), calls=calls, cap=40))
        assert ("dom", 40) in calls
        assert job.status == "partial"
        assert THE_END_SENTENCE not in job.finish_reason

    async def test_a_replay_divar_refuses_is_named(self, run):
        calls = []
        job, _, _ = await run([page(1, [], next_page=False)], category="buy-store",
                              browser=walk(listings("kd", 40), ("target", None), calls=calls, cap=40,
                                           replayable=True, replay_status=429))
        assert job.status == "partial" and "HTTP 429" in job.finish_reason
        assert calls.count(("replay", 1)) == 1 and ("replay", 2) not in calls, \
            "a refused replay stops at once rather than knocking again"

    async def test_a_replay_that_reaches_divar_s_last_page_is_the_end(self, run):
        """Past the walk's cap the replay pages on; Divar saying there is no
        next page is the end of the list, and the run is complete."""
        calls = []
        job, _, _ = await run([page(1, [], next_page=False)], category="buy-store",
                              browser=walk(listings("kf", 40), ("target", None), calls=calls, cap=40,
                                           replayable=True,
                                           replay_pages=[(listings("kg", 24), True),
                                                         (listings("kh", 9), False)]))
        assert calls.count(("replay", 2)) == 1 and ("replay", 3) not in calls
        assert job.status == "completed" and job.config["outcome"]["duplicate"] == 73
        assert THE_END_SENTENCE in job.finish_reason

    async def test_a_replay_stuck_on_the_same_page_is_not_the_end(self, run):
        calls = []
        again = listings("ki", 24)
        job, _, _ = await run([page(1, [], next_page=False)], category="buy-store",
                              browser=walk(listings("ki", 40), ("target", None), calls=calls, cap=40,
                                           replayable=True, replay_pages=[(again, True), (again, True)]))
        assert job.status == "partial" and THE_END_SENTENCE not in job.finish_reason

    async def test_a_browser_walk_divar_refused_still_fails_the_run(self, run):
        """Unchanged: a walk Divar stopped dead is a failed run, not a short one."""
        job, _, _ = await run([page(1, [], next_page=False)], category="buy-store",
                              browser=walk(listings("ke", 3), ("refused", {"403": 4}), calls=[]))
        assert job.status == "failed" and "HTTP 403×4" in job.finish_reason


# ── the pieces, on their own ─────────────────────────────────────────────────

class TestTheAdvice:

    def test_a_named_filter_on_a_400(self):
        advice = dc.refusal_advice(400, "invalid filter for shop-sell: credit", "خرید مغازه")
        assert "«ودیعه»" in advice and "«خرید مغازه»" in advice and "خالی کنید" in advice

    def test_the_category_token_is_not_mistaken_for_the_rent_filter(self):
        advice = dc.refusal_advice(400, "invalid filter for shop-rent: price", "اجاره مغازه")
        assert "«قیمت»" in advice and "«اجاره»" not in advice

    def test_an_unnamed_400(self):
        assert "فیلترها را بررسی کنید" in dc.refusal_advice(400, "bad request")

    @pytest.mark.parametrize("status,words", [
        (429, "صبر کنید"), (403, "«ادامه»"), (401, "«ادامه»"), (500, "سرور دیوار"),
        (None, "«ادامه»")])
    def test_every_other_case_says_what_to_do(self, status, words):
        assert words in dc.refusal_advice(status)


class TestTheCollectorOnItsOwn:

    async def test_a_later_refusal_reaches_collect_stop(self, monkeypatch):
        monkeypatch.setattr(dc, "_city_cache", {})
        monkeypatch.setattr(dc, "_PAGE_PAUSE", 0)
        fake = FakeDivar([page(1, tokens("ma", 24)), refused(400, "invalid filter for shop-sell: credit")])

        class Client(_REAL_CLIENT):
            def __init__(self, *a, **k):
                k["transport"] = httpx.MockTransport(fake)
                super().__init__(*a, **k)
        monkeypatch.setattr(dc.httpx, "AsyncClient", Client)
        s = DivarScraper.__new__(DivarScraper)
        s._search_form, s._job_id_str = {}, None
        rows = await s._collect_listings_robust("urmia", "buy-store", 124)
        assert len(rows) == 24
        kind, detail = s._collect_stop
        assert kind == "partly-refused"
        assert (detail["page"], detail["status"]) == (2, 400)
        assert detail["divar_message"] == "invalid filter for shop-sell: credit"

    async def test_date_mode_uses_the_tehran_day_on_the_browser_path_too(self, monkeypatch):
        """The replay's cursor at 22:00 UTC is the next Tehran day: the walk
        for that day goes on instead of stopping."""
        from datetime import datetime, timezone
        s = DivarScraper.__new__(DivarScraper)
        s._search_form, s._job_id_str, s._search_req_template = None, None, {"post_data": "{}"}
        s.DOM_COLLECT_CAP = 1
        cursors = [int(datetime(2026, 9, 13, 22, 0, tzinfo=timezone.utc).timestamp() * 1e6),
                   int(datetime(2026, 9, 13, 19, 0, tzinfo=timezone.utc).timestamp() * 1e6)]
        served = []

        async def dom(city, cat, target):
            s._collect_stop, s._dom_cursor = ("target", None), None
            return [{"divar_id": "na0000x", "url": "u", "title": "t", "descriptions": []}]

        async def replay(city, category, page_num, last_post_date=None):
            served.append(page_num)
            s._replay_status = 200
            i = len(served) - 1
            return [{"divar_id": f"nb{i:04d}x", "url": "u", "title": "t", "descriptions": []}], cursors[i]
        s._collect_from_browser_dom, s._fetch_listings_direct_api = dom, replay
        monkeypatch.setattr("app.scraper.divar_scraper.asyncio.sleep", _nothing)
        rows = await s._collect_listings_robust("urmia", "rent-apartment", 400,
                                                until_day=date(2026, 9, 14))
        assert served == [1, 2], "a cursor at 01:30 on the 14th read as the 13th and stopped the walk"
        assert len(rows) == 3 and s._collect_stop == ("exhausted", None)
