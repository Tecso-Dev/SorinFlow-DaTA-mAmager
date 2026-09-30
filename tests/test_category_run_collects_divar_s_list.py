"""
Every category the panel offers collects exactly what Divar lists (#57).

«اجارهٔ آپارتمان، مسکونی و خرید آپارتمان درست کار می‌کنند؛ خرید خانهٔ
کلنگی، اجارهٔ صنعتی و کشاورزی و خرید دفتر کار درست انجام نشد.»

A run searches Divar with the category's own token, so every candidate it
collects is one Divar filed in that category. The scraper then decided again
by our own words: a listing whose URL and title named none of the category's
keywords («کلنگی», «دفتر», «صنعتی»…) was judged by the last crumb of its
breadcrumb against the same keywords — and a breadcrumb that ends on a
neighbourhood, or names the sub-category the way Divar's menus do («ویلا و
باغ», «دفتر کار و فضای آموزشی»), was thrown away as «خارج از دسته‌بندی». The
apartment categories rarely got that far, because apartment titles carry
«آپارتمان», «خوابه», «طبقه»; plots, workshops and offices mostly do not.

Here the real start_scraping_job runs a category search against a scripted
Divar — its search API over httpx.MockTransport, its pages served from the
saved fixtures with the breadcrumb Divar prints — and only the reveal, the
database and the browser are replaced. For every category: the pool is
Divar's count, every listing is opened once and saved, and nothing is
dropped. Then, for the categories named in the issue, the listings a run may
leave out — one already held, one Divar deleted, one Divar files in another
category — each leave with that reason and nothing else.

Every token, title and number is made up.
"""
import json
import os
import re
import sys

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_crc.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

import _divar_pages as dp  # noqa: E402
from _fake_redis import patch_redis  # noqa: E402

from app.config import CATEGORIES  # noqa: E402
from app.scraper import otp_store  # noqa: E402
from app.services import divar_count as dc  # noqa: E402
from app.services import job_log, skipped_listings  # noqa: E402

pytestmark = pytest.mark.filterwarnings("ignore:The 'strip_cdata' option")

_REAL_CLIENT = httpx.AsyncClient

# One saved page per category, whose breadcrumb is replaced below.
PAGE_OF = {
    "buy-residential": "buy-residential-house-agency.html",
    "buy-apartment": "buy-apartment-owner.html",
    "buy-villa": "buy-villa-owner.html",
    "buy-old-house": "buy-old-house-plot-agency.html",
    "rent-residential": "rent-residential-apartment-owner.html",
    "rent-apartment": "rent-apartment-owner.html",
    "rent-villa": "rent-villa-owner.html",
    "buy-commercial-property": "buy-commercial-shop-owner.html",
    "buy-office": "buy-office-owner.html",
    "buy-store": "buy-store-owner.html",
    "buy-industrial-agricultural-property": "buy-industrial-workshop-owner.html",
    "rent-commercial-property": "rent-commercial-office-agency.html",
    "rent-office": "rent-office-owner.html",
    "rent-store": "rent-store-owner.html",
    "rent-industrial-agricultural-property": "rent-industrial-workshop-owner.html",
    "rent-temporary": "rent-temporary-villa-agency.html",
    "real-estate-services": "buy-apartment-owner.html",
}

CITY = "ارومیه"
PLACE = "شهرک نمونه"       # a neighbourhood crumb after the category

# Breadcrumbs Divar prints for ads it files in each category: its long names,
# the short names its menus use, a finer sub-category, a neighbourhood after
# the category, and none at all (a page whose breadcrumb did not render).
BREADCRUMBS = {
    "buy-residential": [
        [CITY, "املاک", "فروش مسکونی", "فروش آپارتمان"],
        [CITY, "املاک", "فروش مسکونی", "زمین و کلنگی"],
        [CITY, "املاک", "فروش مسکونی", "خانه و ویلا", PLACE],
        [],
    ],
    "buy-apartment": [
        [CITY, "املاک", "فروش مسکونی", "فروش آپارتمان"],
        [CITY, "املاک", "فروش مسکونی", "آپارتمان", PLACE],
        [],
    ],
    "buy-villa": [
        [CITY, "املاک", "فروش مسکونی", "فروش خانه و ویلا"],
        [CITY, "املاک", "فروش مسکونی", "خانه و ویلا", PLACE],
        [],
    ],
    "buy-old-house": [
        [CITY, "املاک", "فروش مسکونی", "فروش زمین و کلنگی"],
        [CITY, "املاک", "فروش مسکونی", "زمین و کلنگی"],
        [CITY, "املاک", "فروش مسکونی", "زمین و کلنگی", PLACE],
        [CITY, "املاک", "فروش مسکونی", "فروش زمین و کلنگی", "زمین مسکونی"],
        [],
    ],
    "rent-residential": [
        [CITY, "املاک", "اجاره مسکونی", "اجاره آپارتمان"],
        [CITY, "املاک", "اجاره مسکونی", "خانه و ویلا", PLACE],
        [],
    ],
    "rent-apartment": [
        [CITY, "املاک", "اجاره مسکونی", "اجاره آپارتمان"],
        [CITY, "املاک", "اجارهٔ مسکونی", "آپارتمان", PLACE],
        [],
    ],
    "rent-villa": [
        [CITY, "املاک", "اجاره مسکونی", "اجاره خانه و ویلا"],
        [CITY, "املاک", "اجاره مسکونی", "خانه و ویلا", PLACE],
        [],
    ],
    "buy-commercial-property": [
        [CITY, "املاک", "فروش اداری و تجاری", "دفتر کار، اتاق اداری و مطب"],
        [CITY, "املاک", "فروش اداری و تجاری", "مغازه و غرفه", PLACE],
        [CITY, "املاک", "فروش اداری و تجاری", "صنعتی، کشاورزی و تجاری"],
        [],
    ],
    "buy-office": [
        [CITY, "املاک", "فروش اداری و تجاری", "فروش دفتر کار، اتاق اداری و مطب"],
        [CITY, "املاک", "فروش اداری و تجاری", "دفتر کار، اتاق اداری و مطب"],
        [CITY, "املاک", "فروش اداری و تجاری", "دفتر کار، اتاق اداری و مطب", PLACE],
        [CITY, "املاک", "فروش اداری و تجاری", "دفتر کار ، اتاق اداری و مطب", "مطب"],
        [],
    ],
    "buy-store": [
        [CITY, "املاک", "فروش اداری و تجاری", "فروش مغازه و غرفه"],
        [CITY, "املاک", "فروش اداری و تجاری", "مغازه و غرفه", PLACE],
        [],
    ],
    "buy-industrial-agricultural-property": [
        [CITY, "املاک", "فروش اداری و تجاری", "فروش صنعتی، کشاورزی و تجاری"],
        [CITY, "املاک", "فروش صنعتی، کشاورزی و تجاری", "فروش کارگاه، کارخانه و سوله"],
        [CITY, "املاک", "فروش اداری و تجاری", "صنعتی،‌ کشاورزی و تجاری", PLACE],
        [CITY, "املاک", "فروش صنعتی، کشاورزی و تجاری", "دامداری و مرغداری"],
        [],
    ],
    "rent-commercial-property": [
        [CITY, "املاک", "اجاره اداری و تجاری", "اجاره دفتر کار، اتاق اداری و مطب"],
        [CITY, "املاک", "اجاره اداری و تجاری", "مغازه و غرفه", PLACE],
        [CITY, "املاک", "اجاره اداری و تجاری", "صنعتی، کشاورزی و تجاری"],
        [],
    ],
    "rent-office": [
        [CITY, "املاک", "اجاره اداری و تجاری", "اجاره دفتر کار، اتاق اداری و مطب"],
        [CITY, "املاک", "اجاره اداری و تجاری", "دفتر کار، اتاق اداری و مطب", PLACE],
        [],
    ],
    "rent-store": [
        [CITY, "املاک", "اجاره اداری و تجاری", "اجاره مغازه و غرفه"],
        [CITY, "املاک", "اجاره اداری و تجاری", "مغازه و غرفه", PLACE],
        [],
    ],
    "rent-industrial-agricultural-property": [
        [CITY, "املاک", "اجاره اداری و تجاری", "اجاره صنعتی، کشاورزی و تجاری"],
        [CITY, "املاک", "اجاره صنعتی، کشاورزی و تجاری", "اجاره کارگاه، کارخانه و سوله"],
        [CITY, "املاک", "اجاره اداری و تجاری", "صنعتی، کشاورزی و تجاری", PLACE],
        [CITY, "املاک", "اجاره صنعتی، کشاورزی و تجاری", "گلخانه"],
        [],
    ],
    "rent-temporary": [
        [CITY, "املاک", "اجارهٔ کوتاه‌مدت", "اجارهٔ کوتاه‌مدت آپارتمان و سوئیت"],
        [CITY, "املاک", "اجارهٔ کوتاه‌مدت", "ویلا و باغ"],
        [CITY, "املاک", "اجاره کوتاه مدت", "دفتر کار و فضای آموزشی"],
        [CITY, "املاک", "اجارهٔ کوتاه‌مدت", "آپارتمان و سوئیت", PLACE],
        [],
    ],
    "real-estate-services": [
        [CITY, "املاک", "پروژه‌های ساخت و ساز", "مشارکت در ساخت"],
        [CITY, "املاک", "پروژه‌های ساخت و ساز", "پیش‌فروش"],
        [],
    ],
}

SIZE = 26          # a full page of 24 and two on the next


def test_every_category_the_panel_offers_is_covered():
    assert set(BREADCRUMBS) == set(CATEGORIES) == set(PAGE_OF)


def with_breadcrumb(html, crumbs):
    """The page with Divar's breadcrumb replaced — or taken out."""
    nav = ('<nav class="kt-breadcrumbs"><ol>' + "".join(
        f'<li><a class="kt-breadcrumbs__action" href="#">{c}</a></li>' for c in crumbs)
        + "</ol></nav>") if crumbs else ""
    return re.sub(r'<nav class="kt-breadcrumbs">.*?</nav>', nav, html, flags=re.S)


def token(slug, n):
    return "t" + re.sub(r"[^a-z]", "", slug)[:10] + f"{n:03d}x"


class Page(dp.FixturePage):
    """A saved page per token; a token in `gone` answers 410, as Divar does."""

    def __init__(self, pages, gone=()):
        super().__init__(pages)
        self.gone = set(gone)

    async def goto(self, url, **kw):
        resp = await super().goto(url, **kw)
        return dp._Response(410) if self.opened[-1] in self.gone else resp


class Divar:
    """The search API: `pages` in order, each a list of tokens; the count."""

    def __init__(self, pages, count):
        self.pages, self.count, self.asked = pages, count, []

    def __call__(self, request):
        if request.url.path.endswith("/places/cities"):
            return httpx.Response(200, json={"cities": [{"id": 27, "slug": "urmia", "name": CITY}]})
        body = json.loads(request.content)
        self.asked.append(body)
        n = (body.get("pagination_data") or {}).get("page", 0)
        if n >= len(self.pages):
            return httpx.Response(500, json={"message": "the script ran out"})
        return httpx.Response(200, json={
            "list_widgets": [{"widget_type": "POST_ROW", "data": {
                "token": t, "title": f"آگهی آزمایشی {t}"}} for t in self.pages[n]],
            "pagination": {"has_next_page": n + 1 < len(self.pages),
                           "data": {"page": n + 1, "last_post_date": "2026-09-14T10:00:00Z"}},
            "map_data": {"post_count": self.count}})


class Result:
    def __init__(self, job, log, skipped, stored, opened, divar):
        self.job, self.log, self.skipped = job, log, skipped
        self.stored, self.opened, self.divar = stored, opened, divar

    @property
    def saved(self):
        return [p["divar_id"] for p in self.stored]

    @property
    def dropped(self):
        return {r["divar_id"]: r["reason"] for r in self.skipped}

    def lines(self):
        return [e["message"] for e in self.log]


@pytest.fixture
def run(monkeypatch):
    async def nothing(*_a, **_k):
        return None

    monkeypatch.setattr(dc, "_city_cache", {})
    monkeypatch.setattr(dc, "_PAGE_PAUSE", 0)
    patch_redis(monkeypatch, otp_store)

    async def go(category, pages, count, html_of, *, held=(), gone=(), max_items=60):
        log, skipped, stored, spent = [], [], [], []

        async def event(_job_id, stage, message, level="info", **details):
            log.append({"stage": stage, "message": message, "level": level, **details})
            return True

        async def record(_job_id, **row):
            skipped.append(row)
            return True

        monkeypatch.setattr(job_log, "record", event)
        monkeypatch.setattr(job_log, "prune", nothing)
        monkeypatch.setattr(skipped_listings, "record", record)
        monkeypatch.setattr(skipped_listings, "prune", nothing)
        dp.patch_reveal(monkeypatch, spent)

        divar = Divar(pages, count)

        class Client(_REAL_CLIENT):
            def __init__(self, *a, **k):
                k["transport"] = httpx.MockTransport(divar)
                super().__init__(*a, **k)
        monkeypatch.setattr(dc.httpx, "AsyncClient", Client)

        tokens = sorted({t for p in pages for t in p})
        page = Page({t: html_of(t) for t in tokens}, gone=gone)
        job = dp.make_job()
        job.config = {"city": "urmia", "category": category}
        s = dp.make_scraper(page, spent)
        s.db_session = dp.FakeSession(job)
        s._recycle_browser = nothing
        s._search_req_template = None

        async def exists(divar_id):
            return divar_id in held
        s.property_exists = exists

        async def save(property_data):
            stored.append(dict(property_data))
            s._last_save_created = True
            return object()
        s.save_property = save

        await s.start_scraping_job(city="urmia", category=category, max_items=max_items,
                                   download_images=False, job_id=str(job.job_id))
        return Result(job, log, skipped, stored, page.opened, divar)
    return go


def listing_pages(slug, n=SIZE):
    """Divar's list for the category: n listings over pages of 24, each page
    carrying one breadcrumb shape in turn, and the first listing promoted
    again at the top of page two — the same ad, twice."""
    toks = [token(slug, i) for i in range(n)]
    pages = [toks[i:i + 24] for i in range(0, n, 24)]
    if len(pages) > 1:
        pages[1] = [toks[0]] + pages[1]
    shapes = BREADCRUMBS[slug]
    crumbs_of = {t: shapes[i % len(shapes)] for i, t in enumerate(toks)}
    html = dp.load(PAGE_OF[slug])
    return toks, pages, lambda t: with_breadcrumb(html, crumbs_of[t])


@pytest.mark.parametrize("slug", sorted(CATEGORIES))
class TestEveryCategoryCollectsExactlyDivarsList:
    async def test_the_pool_is_divars_count(self, run, slug):
        toks, pages, html_of = listing_pages(slug)
        r = await run(slug, pages, len(toks), html_of)
        assert r.job.divar_count == len(toks)
        assert r.job.total_items == len(toks), "the pool is not Divar's list"
        assert any(f"دیوار می‌گوید {len(toks)} آگهی با این فیلترها دارد؛ {len(toks)} نامزد جمع شد"
                   == m for m in r.lines()), r.lines()

    async def test_every_listing_is_opened_once_and_saved(self, run, slug):
        toks, pages, html_of = listing_pages(slug)
        r = await run(slug, pages, len(toks), html_of)
        assert sorted(r.opened) == sorted(toks), "opened more or fewer than Divar listed"
        assert sorted(r.saved) == sorted(toks)
        assert r.dropped == {}, f"dropped without a reason Divar gave: {r.dropped}"
        assert r.job.status == "completed"
        assert (r.job.new_items, r.job.failed_items) == (len(toks), 0)

    async def test_the_account_adds_up_with_nothing_unexplained(self, run, slug):
        toks, pages, html_of = listing_pages(slug)
        r = await run(slug, pages, len(toks), html_of)
        tally = next(m for m in r.lines() if "نامزد —" in m)
        assert tally.startswith(f"{len(toks)} نامزد — {len(toks)} تازه"), tally
        assert "خارج از دسته" not in tally and "بی‌حساب" not in tally, tally

    async def test_each_is_saved_with_the_run_s_category_and_kind(self, run, slug):
        toks, pages, html_of = listing_pages(slug)
        r = await run(slug, pages, len(toks), html_of)
        kind = CATEGORIES[slug]["type"]
        for p in r.stored:
            assert p["category_name"] == CATEGORIES[slug]["name"], p["category_name"]
            if kind in ("buy", "rent"):
                assert p["listing_type"] == kind, (p["divar_id"], p["listing_type"])


# The categories the issue names, and one listing Divar files elsewhere for each.
ELSEWHERE = {
    "buy-old-house": [CITY, "املاک", "فروش مسکونی", "فروش آپارتمان"],
    "rent-industrial-agricultural-property": [CITY, "املاک", "اجاره اداری و تجاری", "اجاره مغازه و غرفه"],
    "buy-office": [CITY, "املاک", "فروش اداری و تجاری", "فروش مغازه و غرفه"],
    "buy-store": [CITY, "املاک", "اجاره اداری و تجاری", "اجاره مغازه و غرفه"],
    "rent-store": [CITY, "املاک", "فروش اداری و تجاری", "فروش مغازه و غرفه"],
    "rent-temporary": [CITY, "املاک", "اجاره مسکونی", "اجاره آپارتمان"],
    "buy-industrial-agricultural-property": [CITY, "استخدام و کاریابی", "کارگر ساده"],
}


@pytest.mark.parametrize("slug", sorted(ELSEWHERE))
class TestWhatIsLeftOutHasAReason:
    """One already held with its number, one Divar deleted, one Divar files in
    another category: each leaves with that reason, the rest are saved."""

    async def _run(self, run, slug):
        toks, pages, html_of = listing_pages(slug)
        held, gone, other = toks[3], toks[5], toks[7]
        foreign = with_breadcrumb(dp.load(PAGE_OF[slug]), ELSEWHERE[slug])
        r = await run(slug, pages, len(toks),
                      lambda t: foreign if t == other else html_of(t),
                      held={held}, gone={gone})
        return toks, (held, gone, other), r

    async def test_the_rest_are_saved(self, run, slug):
        toks, (held, gone, other), r = await self._run(run, slug)
        assert r.job.total_items == len(toks)
        assert sorted(r.saved) == sorted(set(toks) - {held, gone, other})

    async def test_each_one_left_out_says_why(self, run, slug):
        toks, (held, gone, other), r = await self._run(run, slug)
        assert r.dropped == {gone: "deleted", other: "category"}
        assert held not in r.opened, "a listing held with its number was opened again"
        tally = next(m for m in r.lines() if "نامزد —" in m)
        assert "1 تکراری" in tally and "1 در دیوار حذف شده" in tally, tally
        assert "بی‌حساب" not in tally, tally

    async def test_the_foreign_one_names_where_divar_files_it(self, run, slug):
        _, (_, _, other), r = await self._run(run, slug)
        (row,) = [x for x in r.skipped if x["divar_id"] == other]
        said = ELSEWHERE[slug][-1]
        assert said in (row.get("detail") or ""), row
