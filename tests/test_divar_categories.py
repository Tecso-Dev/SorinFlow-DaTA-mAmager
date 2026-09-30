"""
Divar's breadcrumb, read as a place in Divar's own category tree (#57).

These replace the keyword tests of the lists the scraper used to judge a
listing's category by (CATEGORY_URL_PATTERNS). What those tests protected
still holds, now as behaviour:

* an ad whose title and URL name no property type is not thrown away —
  «گلشهر ۲ تمام رهن», «۲۰۰ متر بر دانشکده» were seventeen real rentals once;
* a breadcrumb that is not real estate (a job, a car) is off every category,
  and so is one Divar files under a neighbouring family («اجاره مغازه» in a
  rent-office run);
* the spellings Divar varies between — a zero-width non-joiner in
  «کوتاه‌مدت», «اجارهٔ», Arabic «ي» and «ك», a comma with or without spaces —
  are one spelling;
* the verdict comes before the contact reveal, and names what Divar called
  the ad.

And what they could not: the breadcrumb leaves Divar prints for plots, offices,
workshops and short-term rentals, including the short menu names («زمین و
کلنگی», «ویلا و باغ», «دفتر کار و فضای آموزشی») and a neighbourhood or finer
sub-category after the category, are the run's own category.
"""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_dcat.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

import _divar_pages as dp  # noqa: E402

from app.config import CATEGORIES  # noqa: E402
from app.scraper import divar_categories as dcat  # noqa: E402
from app.services import divar_filters  # noqa: E402

pytestmark = pytest.mark.filterwarnings("ignore:The 'strip_cdata' option")

CITY = "ارومیه"


def keep(crumbs, target):
    return dcat.judge(crumbs, target)[0]


class TestTheTreeCoversWhatThePanelOffers:
    def test_every_category_the_panel_offers_is_in_it(self):
        assert set(CATEGORIES) <= set(dcat.TREE)

    def test_and_every_category_the_filter_schema_knows(self):
        cats = set(divar_filters.committed()["categories"])
        assert cats <= set(dcat.TREE), cats - set(dcat.TREE)

    def test_every_node_reaches_the_root(self):
        for slug in dcat.TREE:
            assert dcat.ancestors(slug)[-1] == dcat.ROOT, slug

    def test_each_category_s_own_names_place_it_there(self):
        for slug, (parent, names) in dcat.TREE.items():
            above = [dcat.TREE[a][1][0] for a in reversed(dcat.ancestors(parent))]
            for name in names:
                assert dcat.locate([CITY, *above, name]) == slug, (slug, name)


# Leaves Divar prints for an ad it files in each category — the long names,
# the short menu names, a finer sub-category or a neighbourhood after them.
OWN = [
    ("buy-old-house", ["املاک", "فروش مسکونی", "فروش زمین و کلنگی"]),
    ("buy-old-house", ["املاک", "فروش مسکونی", "زمین و کلنگی"]),
    ("buy-old-house", ["املاک", "فروش مسکونی", "زمین و کلنگی", "شهرک نمونه"]),
    ("buy-old-house", ["املاک", "فروش مسکونی", "فروش زمین و کلنگی", "زمین مسکونی"]),
    ("buy-old-house", ["فروش زمین و کلنگی"]),
    ("buy-office", ["املاک", "فروش اداری و تجاری", "فروش دفتر کار، اتاق اداری و مطب"]),
    ("buy-office", ["املاک", "فروش اداری و تجاری", "دفتر کار، اتاق اداری و مطب"]),
    ("buy-office", ["املاک", "فروش اداری و تجاری", "دفتر کار ، اتاق اداری و مطب", "مطب"]),
    ("buy-office", ["املاک", "فروش اداری و تجاری", "دفتر کار,اتاق اداری و مطب"]),
    ("rent-office", ["املاک", "اجاره اداری و تجاری", "اجاره دفتر کار، اتاق اداری و مطب"]),
    ("rent-office", ["املاک", "اجارهٔ اداری و تجاری", "دفتر کار، اتاق اداری و مطب", "شهرک نمونه"]),
    ("buy-industrial-agricultural-property",
     ["املاک", "فروش اداری و تجاری", "فروش صنعتی، کشاورزی و تجاری"]),
    ("buy-industrial-agricultural-property",
     ["املاک", "فروش صنعتی، کشاورزی و تجاری", "فروش کارگاه، کارخانه و سوله"]),
    ("buy-industrial-agricultural-property",
     ["املاک", "فروش اداری و تجاری", "صنعتی،‌ کشاورزی و تجاری"]),
    ("rent-industrial-agricultural-property",
     ["املاک", "اجاره اداری و تجاری", "اجاره صنعتی، کشاورزی و تجاری"]),
    ("rent-industrial-agricultural-property",
     ["املاک", "اجاره صنعتی، کشاورزی و تجاری", "اجاره باغ، مزرعه و زمین کشاورزی"]),
    ("rent-industrial-agricultural-property",
     ["املاک", "اجاره اداری و تجاری", "صنعتی، کشاورزی و تجاری", "سوله و انبار"]),
    ("rent-industrial-agricultural-property", ["املاک", "اجاره صنعتی، کشاورزی و تجاری", "گلخانه"]),
    ("buy-store", ["املاک", "فروش اداری و تجاری", "فروش مغازه و غرفه"]),
    ("rent-store", ["املاک", "اجاره اداری و تجاری", "مغازه و غرفه", "شهرک نمونه"]),
    ("rent-temporary", ["املاک", "اجارهٔ کوتاه‌مدت", "اجارهٔ کوتاه‌مدت آپارتمان و سوئیت"]),
    ("rent-temporary", ["املاک", "اجارهٔ کوتاه‌مدت", "ویلا و باغ"]),
    ("rent-temporary", ["املاک", "اجاره کوتاه مدت", "دفتر کار و فضای آموزشی"]),
    ("rent-temporary", ["املاک", "اجاره كوتاه مدت"]),
    ("rent-temporary-villa", ["املاک", "اجارهٔ کوتاه‌مدت", "ویلا و باغ"]),
    ("buy-residential", ["املاک", "فروش مسکونی", "زمین و کلنگی"]),
    ("buy-residential", ["املاک", "فروش مسکونی", "خانه و ویلا"]),
    ("rent-residential", ["املاک", "اجاره مسکونی", "آپارتمان"]),
    ("rent-apartment", ["املاک", "اجاره مسکونی", "اجاره آپارتمان"]),
    ("rent-apartment", ["املاک", "اجاره مسکونی", "آپارتمان", "شهرک نمونه"]),
    ("rent-villa", ["املاک", "اجاره مسکونی", "خانه و ویلا"]),
    ("buy-commercial-property", ["املاک", "فروش اداری و تجاری", "صنعتی، کشاورزی و تجاری"]),
    ("rent-commercial-property", ["املاک", "اجاره اداری و تجاری", "مغازه و غرفه"]),
    ("real-estate-services", ["املاک", "پروژه‌های ساخت و ساز", "پیش‌فروش"]),
]


@pytest.mark.parametrize("target,crumbs", OWN, ids=lambda v: "›".join(v) if isinstance(v, list) else v)
def test_a_breadcrumb_divar_prints_for_the_category_keeps_it(target, crumbs):
    assert keep([CITY, *crumbs], target), dcat.judge([CITY, *crumbs], target)


# (the run's category, where Divar files the listing instead)
ELSEWHERE = [
    ("rent-store", ["املاک", "اجاره اداری و تجاری", "اجاره دفتر کار، اتاق اداری و مطب"]),
    ("buy-store", ["املاک", "فروش اداری و تجاری", "فروش دفتر کار، اتاق اداری و مطب"]),
    ("rent-office", ["املاک", "اجاره اداری و تجاری", "اجاره مغازه و غرفه"]),
    ("buy-office", ["املاک", "فروش اداری و تجاری", "مغازه و غرفه"]),
    ("buy-store", ["املاک", "اجاره اداری و تجاری", "اجاره مغازه و غرفه"]),
    ("buy-old-house", ["املاک", "فروش مسکونی", "فروش آپارتمان"]),
    ("buy-villa", ["املاک", "فروش مسکونی", "زمین و کلنگی"]),
    ("rent-villa", ["املاک", "اجاره مسکونی", "اجاره آپارتمان"]),
    ("rent-temporary", ["املاک", "اجاره اداری و تجاری", "اجاره مغازه و غرفه"]),
    ("rent-temporary", ["املاک", "فروش مسکونی", "فروش آپارتمان"]),
    ("buy-industrial-agricultural-property", ["املاک", "فروش اداری و تجاری", "فروش مغازه و غرفه"]),
    ("rent-industrial-agricultural-property",
     ["املاک", "اجاره اداری و تجاری", "اجاره دفتر کار، اتاق اداری و مطب"]),
    ("rent-apartment", ["املاک", "اجارهٔ کوتاه‌مدت", "آپارتمان و سوئیت"]),
    ("buy-apartment", ["املاک", "اجاره مسکونی", "آپارتمان"]),
]


@pytest.mark.parametrize("target,crumbs", ELSEWHERE, ids=lambda v: "›".join(v) if isinstance(v, list) else v)
def test_a_breadcrumb_divar_prints_for_another_category_is_off(target, crumbs):
    assert not keep([CITY, *crumbs], target)


NOT_REAL_ESTATE = [["استخدام و کاریابی", "کارگر ساده"], ["وسایل نقلیه", "خودرو", "سواری"],
                   ["کالای دیجیتال", "موبایل و تبلت"], ["خانه و آشپزخانه", "لوازم خانگی"]]


@pytest.mark.parametrize("slug", sorted(CATEGORIES))
@pytest.mark.parametrize("crumbs", NOT_REAL_ESTATE, ids=lambda v: v[-1])
def test_something_that_is_not_real_estate_is_off_everywhere(slug, crumbs):
    assert not keep([CITY, *crumbs], slug)


class TestWhatIsNotADenial:
    def test_no_breadcrumb_keeps_it(self):
        assert keep([], "buy-old-house")

    def test_only_the_root_keeps_it(self):
        assert keep([CITY, "املاک"], "buy-office")

    def test_a_breadcrumb_we_cannot_place_under_real_estate_keeps_it(self):
        assert keep([CITY, "املاک", "دسته‌ای که دیوار تازه ساخته"], "buy-office")

    def test_the_family_above_the_category_keeps_it(self):
        """Divar printed only the parent: not another category."""
        assert keep([CITY, "املاک", "فروش مسکونی"], "buy-old-house")

    def test_a_crumb_naming_nothing_does_not_move_the_place(self):
        assert dcat.locate([CITY, "املاک", "فروش مسکونی", "زمین و کلنگی", "ارومیه"]) == "buy-old-house"


class TestOneSpelling:
    @pytest.mark.parametrize("a,b", [
        ("اجارهٔ کوتاه‌مدت", "اجاره کوتاه مدت"),
        ("اجاره كوتاه مدت", "اجاره کوتاه مدت"),
        ("دفتر کار،اتاق اداری و مطب", "دفتر کار، اتاق اداری و مطب"),
        ("دفتر کار , اتاق اداری و مطب", "دفتر کار، اتاق اداری و مطب"),
        ("پیش‌فروش", "پیش فروش"),
        ("  فروش   زمین  و کلنگی ", "فروش زمین و کلنگی"),
        ("فروش زمين و کلنگي", "فروش زمین و کلنگی"),
    ])
    def test_the_same_name(self, a, b):
        assert dcat.normalize(a) == dcat.normalize(b)

    def test_different_words_stay_different(self):
        assert dcat.normalize("اجاره ویلا") != dcat.normalize("اجاره ویلا و باغ")


class TestTheKindComesFromWhereDivarFiledIt:
    @pytest.mark.parametrize("crumbs,kind", [
        (["املاک", "فروش مسکونی", "زمین و کلنگی"], "buy"),
        (["املاک", "اجاره اداری و تجاری", "صنعتی، کشاورزی و تجاری"], "rent"),
        (["املاک", "فروش اداری و تجاری", "دفتر کار، اتاق اداری و مطب"], "buy"),
        (["املاک", "اجارهٔ کوتاه‌مدت", "ویلا و باغ"], "rent"),
        (["املاک", "پروژه‌های ساخت و ساز", "مشارکت در ساخت"], None),
    ])
    def test_it(self, crumbs, kind):
        assert dcat.listing_type(dcat.locate([CITY, *crumbs])) == kind


# ── the verdict on a real page, through scrape_property_detail ──────────────

def with_breadcrumb(html, crumbs):
    nav = ('<nav class="kt-breadcrumbs"><ol>' + "".join(
        f'<li><a class="kt-breadcrumbs__action" href="#">{c}</a></li>' for c in crumbs)
        + "</ol></nav>") if crumbs else ""
    return re.sub(r'<nav class="kt-breadcrumbs">.*?</nav>', nav, html, flags=re.S)


async def open_page(monkeypatch, name, crumbs, target, *, title="گلشهر ۲ تمام رهن"):
    """One saved page opened by the real detail scrape for a `target` run, its
    breadcrumb replaced and its search-card title one naming no property type."""
    spent = []
    dp.patch_reveal(monkeypatch, spent)
    tok = dp.manifest()[name]["token"]
    page = dp.FixturePage({tok: with_breadcrumb(dp.load(name), crumbs)})
    s = dp.make_scraper(page, spent)
    got = await s.scrape_property_detail(dp.url_of(tok), target_category=target,
                                         source_title=title, wants_contact=None)
    return got, s, spent


class TestOnTheRealDetailScrape:
    async def test_a_rental_whose_words_name_no_type_is_kept(self, monkeypatch):
        got, _, spent = await open_page(
            monkeypatch, "rent-apartment-owner.html",
            [CITY, "املاک", "اجاره مسکونی", "آپارتمان", "شهرک نمونه"], "rent-apartment")
        assert isinstance(got, dict) and got["listing_type"] == "rent"
        assert any(e.startswith("contact:") for e in spent)

    async def test_a_plot_titled_land_is_kept_in_the_old_house_run(self, monkeypatch):
        got, _, _ = await open_page(
            monkeypatch, "buy-old-house-plot-agency.html",
            [CITY, "املاک", "فروش مسکونی", "زمین و کلنگی"], "buy-old-house",
            title="زمین ۲۰۰ متری نبش")
        assert isinstance(got, dict)
        assert got["category_name"] == "زمین و کلنگی" and got["listing_type"] == "buy"

    async def test_a_job_ad_is_dropped_before_any_reveal(self, monkeypatch):
        got, s, spent = await open_page(
            monkeypatch, "rent-office-owner.html",
            [CITY, "استخدام و کاریابی", "کارگر ساده"], "rent-office")
        assert got is False
        assert not any(e.startswith("contact:") for e in spent), "a reveal was spent on it"
        assert "کارگر ساده" in s._last_category_drop

    async def test_a_neighbouring_family_is_dropped_and_named(self, monkeypatch):
        got, s, spent = await open_page(
            monkeypatch, "rent-store-owner.html",
            [CITY, "املاک", "اجاره اداری و تجاری", "اجاره مغازه و غرفه"], "rent-office")
        assert got is False and spent == []
        assert s._last_category_drop.startswith("اجاره مغازه و غرفه — ")
        assert len(s._last_category_drop) <= 80

    async def test_the_page_title_is_not_asked(self, monkeypatch):
        """It was «سایت دیوار» at domcontentloaded more often than not; the
        breadcrumb is the answer, so the round trip is gone."""
        asked = []

        async def title():
            asked.append(1)
            return "سایت دیوار"
        spent = []
        dp.patch_reveal(monkeypatch, spent)
        name = "buy-office-owner.html"
        tok = dp.manifest()[name]["token"]
        page = dp.FixturePage({tok: dp.load(name)})
        page.title = title
        s = dp.make_scraper(page, spent)
        got = await s.scrape_property_detail(dp.url_of(tok), target_category="buy-office",
                                             source_title="۱۲۰ متر موقعیت عالی")
        assert isinstance(got, dict) and asked == []
