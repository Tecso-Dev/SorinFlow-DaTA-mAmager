"""
A saved page for every category, read the way the scraper reads it.

Nothing tested the readers on anything but a five-row snippet of the sale
apartment page. The categories that went out with #27 — old houses, shops,
offices, industrial and agricultural land, short-term rentals — each put
different rows on the page (a land area but no metric area, no rooms, a rent
that is «مجانی», prices in compact words, an amenity table read positionally),
and none of it was ever run.

tests/fixtures/divar holds two pages for each category and expected.json says
what each must read as: price, price per metre, deposit, rent, areas, rooms,
the amenities the page states, who posted it and where. The pages are
HAND-BUILT from the structure the parsers read — there was no way to save a real
one from where they were written, and no phone number or name belongs in the
repository. When real captures replace them, expected.json is what to edit.

Each page goes through the real `DivarScraper.scrape_property_detail` (title,
description, rows, tables, chips, the breadcrumb, the advertiser check), with
only the browser replaced by tests/_divar_pages.FixturePage.
"""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_fx.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

import _divar_pages as dp  # noqa: E402
from bs4 import BeautifulSoup  # noqa: E402

from app.config import CATEGORIES  # noqa: E402
from app.scraper.parsers import extract_price_info, extract_property_details  # noqa: E402
from app.services import advertiser_signals  # noqa: E402

# lxml's HTMLParser warns about an option BeautifulSoup passes it; the scraper
# parses with lxml too, so the warning is the price of using the same parser.
pytestmark = pytest.mark.filterwarnings("ignore:The 'strip_cdata' option")

MANIFEST = dp.manifest()
NAMES = sorted(MANIFEST)

PRICE_KEYS = ("total_price", "price", "price_per_meter", "deposit", "rent_price")
MEASURE_KEYS = ("area", "land_area", "built_area", "rooms", "year_built", "floor", "total_floors")
AMENITY_KEYS = ("has_elevator", "has_parking", "has_storage", "has_balcony")
WHO_KEYS = ("advertiser_type", "agency_suspected", "agency_evidence", "seller_name")
WHERE_KEYS = ("city_name", "district")


async def read(monkeypatch, name, *, category=None, title=""):
    """The listing as the scraper reads it, stopping before the contact reveal
    (every fixture would answer «no number» anyway)."""
    info = MANIFEST[name]
    page = dp.FixturePage({info["token"]: dp.load(name)}, title=title)
    monkeypatch.setattr("app.scraper.divar_scraper.asyncio.sleep", dp.nothing)
    s = dp.make_scraper(page)
    got = await s.scrape_property_detail(
        dp.url_of(info["token"]), target_category=category or info["category"],
        wants_contact=lambda _pd: "stop before the reveal")
    return got, s


class TestTheLibrary:
    def test_every_page_has_an_entry_and_every_entry_a_page(self):
        pages = {f for f in os.listdir(dp.FIXTURE_DIR) if f.endswith(".html")}
        assert pages == set(MANIFEST)

    def test_every_category_has_at_least_two_pages(self):
        for slug, meta in CATEGORIES.items():
            if meta["type"] == "service":
                continue
            n = sum(1 for m in MANIFEST.values() if m["category"] == slug)
            assert n >= 2, f"{slug} has {n} page(s)"

    def test_both_kinds_are_covered_for_every_category(self):
        """Owner-posted and agency-posted, in every category."""
        for slug, meta in CATEGORIES.items():
            if meta["type"] == "service":
                continue
            kinds = {m["expect"].get("advertiser_type") for m in MANIFEST.values()
                     if m["category"] == slug}
            assert {"personal", "agency"} <= kinds, f"{slug}: {kinds}"

    def test_the_manifest_names_a_real_category_and_kind(self):
        for name, m in MANIFEST.items():
            assert m["category"] in CATEGORIES, name
            assert CATEGORIES[m["category"]]["type"] == m["kind"], name

    def test_no_page_carries_a_phone_number_an_email_or_an_address(self):
        """The repository is public."""
        bad = []
        for name in NAMES:
            html = dp.load(name)
            digits = html.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "0123456789" * 2))
            if re.search(r"(?<!\d)(?:\+?98|0098|0)?9\d{9}(?!\d)", digits.replace("-", "")):
                bad.append((name, "mobile number"))
            if re.search(r"(?<!\d)0\d{2}[-\s]?\d{8}(?!\d)", digits):
                bad.append((name, "landline"))
            if re.search(r"[\w.]+@[\w.]+\.\w+", html):
                bad.append((name, "e-mail"))
            if re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", html):
                bad.append((name, "IP address"))
            if "tel:" in html:
                bad.append((name, "tel: link"))
        assert not bad, bad

    def test_every_page_says_it_is_hand_built(self):
        for name in NAMES:
            assert "hand-built" in dp.load(name)[:600], name


@pytest.mark.parametrize("name", NAMES)
async def test_a_page_reads_as_its_manifest_says(monkeypatch, name):
    got, _ = await read(monkeypatch, name)
    assert isinstance(got, dict), f"the scrape returned {got!r}"
    advertiser_signals.annotate(got)
    expect = MANIFEST[name]["expect"]
    wrong = {k: {"expected": v, "read": got.get(k)} for k, v in expect.items() if got.get(k) != v}
    assert not wrong, wrong


@pytest.mark.parametrize("group", [
    ("prices", PRICE_KEYS), ("measures", MEASURE_KEYS), ("amenities", AMENITY_KEYS),
    ("advertiser", WHO_KEYS), ("place", WHERE_KEYS)], ids=lambda g: g[0])
def test_the_manifest_states_every_group_somewhere(group):
    """A group nothing asserts on is a group nothing tests."""
    _, keys = group
    stated = {k for m in MANIFEST.values() for k in m["expect"]}
    assert stated & set(keys)


class TestEveryCategoryIsKeptInItsOwnRun:
    """The page's own breadcrumb is what confirms the category, since the titles
    here name no property type on purpose."""

    @pytest.mark.parametrize("name", NAMES)
    async def test_it_is_not_counted_out_of_category(self, monkeypatch, name):
        got, s = await read(monkeypatch, name)
        assert got is not False, (
            f"counted «خارج از دسته‌بندی»: {getattr(s, '_last_category_drop', None)}")
        assert isinstance(got, dict)


class TestSimilarAdsAreNotThisAd:
    """Divar draws other people's ads under every listing. «۳ خوابه» and
    «سوئیت» in their titles are not this ad's rooms: a shop was read as having
    none (0) or two, and a min-rooms filter judged it on that."""

    @pytest.mark.parametrize("name", [n for n in NAMES if MANIFEST[n]["expect"].get("rooms", 1) is None])
    def test_a_page_with_no_rooms_row_has_no_rooms(self, name):
        soup = BeautifulSoup(dp.load(name), "lxml")
        h1 = soup.select_one("h1").get_text(strip=True)
        assert extract_property_details(soup, h1).get("rooms") is None

    def test_the_ad_s_own_description_still_counts(self):
        html = dp.load("buy-store-owner.html").replace(
            "مغازه سرقفلی‌دار.", "مغازه با یک اتاق خواب کارمند و انباری کوچک")
        soup = BeautifulSoup(html, "lxml")
        assert extract_property_details(soup, "مغازه").get("rooms") == 1

    def test_and_its_title(self):
        soup = BeautifulSoup(dp.load("buy-store-owner.html"), "lxml")
        assert extract_property_details(soup, "دفتر ۲ خوابه").get("rooms") == 2


class TestAnAmenityTableIsReadByPosition:
    """«آسانسور | پارکینگ | انباری» over «ندارد | دارد | دارد». The header says the
    word and the cell below it says whether the ad has one; reading the header
    alone reported an elevator on every page whose elevator column said no."""

    def test_the_cells_say_which_the_ad_has(self):
        soup = BeautifulSoup(dp.load("buy-apartment-owner.html"), "lxml")
        got = extract_property_details(soup, "")
        assert got["has_elevator"] is False
        assert got["has_parking"] is True
        assert got["has_storage"] is True

    def test_an_empty_cell_says_nothing(self):
        html = dp.load("buy-apartment-owner.html").replace(">ندارد</td>", "></td>")
        got = extract_property_details(BeautifulSoup(html, "lxml"), "")
        assert "has_elevator" not in got

    @pytest.mark.parametrize("no", ["ندارد", "ندارند", "خیر", "بدون آسانسور", "فاقد آسانسور"])
    def test_every_way_of_saying_no(self, no):
        html = dp.load("buy-apartment-owner.html").replace(">ندارد</td>", f">{no}</td>", 1)
        assert extract_property_details(BeautifulSoup(html, "lxml"), "")["has_elevator"] is False

    def test_a_header_alone_is_not_an_amenity(self):
        """A column heading names a feature; it does not say the ad has it."""
        html = ('<table class="kt-group-row"><thead><tr>'
                '<th class="kt-group-row-item kt-group-row-item__title">آسانسور</th>'
                '<th class="kt-group-row-item kt-group-row-item__title">پارکینگ</th>'
                '</tr></thead><tbody><tr><td class="kt-group-row-item">ندارد</td></tr>'
                '</tbody></table>')
        got = extract_property_details(BeautifulSoup(html, "lxml"), "")
        assert "has_elevator" not in got and "has_parking" not in got


class TestALandAreaIsTheAreaWhenItIsTheOnlyOne:
    """An old house or a plot has «متراژ زمین» and no plain «متراژ», so `area`
    was empty and a min-area filter could not judge it."""

    def test_a_plot(self):
        soup = BeautifulSoup(dp.load("buy-old-house-plot-agency.html"), "lxml")
        got = extract_property_details(soup, "زمین مسکونی نبش کوچه")
        assert got["land_area"] == 200 and got["area"] == 200

    def test_a_house_with_both_keeps_them_apart(self):
        """Which of the two «متراژ» means is not the scraper's to guess."""
        soup = BeautifulSoup(dp.load("buy-villa-owner.html"), "lxml")
        got = extract_property_details(soup, "ویلا دو طبقه با استخر")
        assert got["land_area"] == 500 and got["built_area"] == 220
        assert got.get("area") is None

    def test_a_plain_area_is_never_replaced(self):
        soup = BeautifulSoup(dp.load("buy-store-owner.html"), "lxml")
        got = extract_property_details(soup, "مغازه")
        assert got["area"] == 18 and "land_area" not in got


class TestPricesAreReadFromEitherKindOfTable:
    """A group table may carry its title class on the <th> or on a span inside
    it. extract_property_details knew both; extract_price_info knew only the
    span, so a deposit and a rent sitting in a table were read by neither."""

    @staticmethod
    def _table(style):
        if style == "th":
            heads = ('<th class="kt-group-row-item kt-group-row-item__title">ودیعه</th>'
                     '<th class="kt-group-row-item kt-group-row-item__title">اجارهٔ ماهانه</th>')
            cells = ('<td class="kt-group-row-item kt-group-row-item__value">۲۰۰٬۰۰۰٬۰۰۰ تومان</td>'
                     '<td class="kt-group-row-item kt-group-row-item__value">۸٬۰۰۰٬۰۰۰ تومان</td>')
        else:
            heads = ('<th class="kt-group-row-item"><span class="kt-group-row-item__title">ودیعه</span></th>'
                     '<th class="kt-group-row-item"><span class="kt-group-row-item__title">اجارهٔ ماهانه</span></th>')
            cells = ('<td class="kt-group-row-item"><span class="kt-group-row-item__value">۲۰۰٬۰۰۰٬۰۰۰ تومان</span></td>'
                     '<td class="kt-group-row-item"><span class="kt-group-row-item__value">۸٬۰۰۰٬۰۰۰ تومان</span></td>')
        return ('<table class="kt-group-row"><thead><tr>' + heads + '</tr></thead>'
                '<tbody><tr>' + cells + '</tr></tbody></table>')

    @pytest.mark.parametrize("style", ["th", "span"])
    def test_a_deposit_and_a_rent_in_a_table(self, style):
        got = extract_price_info(BeautifulSoup(self._table(style), "lxml"))
        assert got["deposit"] == 200_000_000
        assert got["rent_price"] == 8_000_000


class TestTheFixturesAreReadAtTheParserLevelToo:
    """Without the browser, the page and the title: the two row readers alone."""

    @pytest.mark.parametrize("name", NAMES)
    def test_prices_and_measures(self, name):
        soup = BeautifulSoup(dp.load(name), "lxml")
        h1 = soup.select_one("h1").get_text(strip=True)
        got = {**extract_price_info(soup), **extract_property_details(soup, h1)}
        expect = MANIFEST[name]["expect"]
        keys = [k for k in expect if k in PRICE_KEYS + MEASURE_KEYS + AMENITY_KEYS]
        wrong = {k: {"expected": expect[k], "read": got.get(k)} for k in keys
                 if got.get(k) != expect[k]}
        assert not wrong, wrong
