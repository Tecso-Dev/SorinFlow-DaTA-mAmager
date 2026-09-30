"""
scripts/divar_live_check.py, against a scripted Divar.

The script is run where Divar answers, and what it writes is committed. So
before anyone runs it for real, this runs it here over httpx.MockTransport:
Divar's category pages, its search API (with the count, the cursor, the
refusal of a filter the category does not have) and its ad pages, the last
being the hand-built pages in tests/fixtures/divar with a phone number, an
e-mail and an agent's name put into them. It checks that every section
reports, that the pages it saves read as Divar's own data says and carry none
of what was put in, and that the search answer it saves has no real token.

Every name, token and number here is made up.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_dlc.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

import httpx  # noqa: E402
import pytest  # noqa: E402

import _divar_pages as dp  # noqa: E402
import divar_live_check as dlc  # noqa: E402
from app.services import divar_count as dc  # noqa: E402
from app.services import divar_filters as df  # noqa: E402

pytestmark = pytest.mark.filterwarnings("ignore:The 'strip_cdata' option")

_REAL_CLIENT = httpx.AsyncClient
PHONE = "۰۹۱۲۳۴۵۶۷۸۹"
EMAIL = "someone@example.com"
AGENT = "کاوه نمونه‌زاده"
FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
BY_TOKEN = {v: k for k, v in dc.CATEGORY_TOKENS.items()}
MANIFEST = dp.manifest()
KIND = {"personal": "personal", "real-estate-business": "agency"}
with open(os.path.join(dlc.ROOT, ".gitignore"), encoding="utf-8") as _fh:
    GITIGNORE = _fh.read()


def fa(n):
    return f"{n:,}".replace(",", "٬").translate(FA)


def fixture_for(slug, kind):
    return sorted(n for n, m in MANIFEST.items()
                  if m["category"] == slug and m["expect"].get("advertiser_type") == kind)[0]


def page_of(name):
    """A hand-built page with what must never reach the repository put in."""
    html = dp.load(name)
    extra = f"<p class='kt-description-row__text'>تماس: {PHONE} یا {EMAIL}</p><p>{AGENT}</p>"
    return html.replace("</main>", extra + "</main>")


def post_of(name):
    """Divar's own data for the page: its rows, flags and advertiser."""
    e = MANIFEST[name]["expect"]
    rows = []
    for key, title in (("area", "متراژ"), ("year_built", "ساخت"), ("rooms", "اتاق")):
        if e.get(key) is not None:
            rows.append({"title": title, "value": "بدون اتاق" if key == "rooms" and e[key] == 0
                         else str(e[key]).translate(FA)})
    for key, title in (("total_price", "قیمت کل"), ("deposit", "ودیعه"), ("rent_price", "اجارهٔ ماهانه")):
        if e.get(key) is not None:
            rows.append({"title": title, "value": "مجانی" if e[key] == 0 else f"{fa(e[key])} تومان"})
    flags = [{"title": title, "available": e[key]} for key, title in
             (("has_elevator", "آسانسور"), ("has_parking", "پارکینگ"), ("has_storage", "انباری"),
              ("has_balcony", "بالکن")) if e.get(key) is not None]
    return {"sections": [
        {"widgets": [{"widget_type": "GROUP_INFO_ROW", "data": {"items": rows}},
                     {"widget_type": "GROUP_FEATURE_ROW", "data": {"items": flags}}]},
        {"widgets": [{"widget_type": "BUSINESS_SECTION", "data": {"title": "املاک نمونه", "subtitle": AGENT}}]},
    ], "webengage": {"business_type": "personal" if e.get("advertiser_type") == "personal"
                     else "real-estate-business"}}


def category_page(slug):
    """Divar's form for `slug`, as the committed file has it, with the four
    choice filters' options that only a live read can bring."""
    entry = df.committed()["categories"][slug]
    widgets = []
    for f in entry["filters"]:
        w = {"widget_type": "FILTER", "cache_key": entry["token"], "title": f["title"],
             "field": {"key": f["key"], "type": f["type"]}}
        opts = f.get("options") or ([{"value": "v1", "title": "یک"}, {"value": "v2", "title": "دو"}]
                                    if f["type"] in ("repeated_string", "str") else None)
        if opts:
            w["data"] = {"options": opts}
        widgets.append(w)
    state = {"nb": {"filtersPage": {"widgetList": widgets}}}
    return ("<html><body><script>window.__PRELOADED_STATE__ = "
            + json.dumps(state, ensure_ascii=False) + ";</script></body></html>")


def card(token, bottom):
    return {"widget_type": "POST_ROW", "data": {"token": token, "title": "آپارتمان", "bottom_description_text": bottom}}


class FakeDivar:
    def __init__(self, post_api=True):
        self.requests, self.post_api = [], post_api

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        if url.endswith("/places/cities"):
            return httpx.Response(200, json={"cities": [{"id": 27, "slug": "urmia", "name": "ارومیه"}]})
        m = re.match(r"https://divar\.ir/s/[^/]+/([^/?]+)$", url)
        if m:
            return httpx.Response(200, text=category_page(m.group(1)))
        m = re.match(r"https://divar\.ir/v/([^/?]+)$", url)
        if m:
            name = next(n for n, x in MANIFEST.items() if x["token"] == m.group(1))
            return httpx.Response(200, text=page_of(name))
        m = re.match(r"https://api\.divar\.ir/v8/posts-v2/web/([^/?]+)$", url)
        if m and not self.post_api:
            return httpx.Response(404, json={"message": "not found"})
        if m:
            name = next(n for n, x in MANIFEST.items() if x["token"] == m.group(1))
            return httpx.Response(200, json=post_of(name))
        if url == dc.SEARCH_URL:
            return self.search(json.loads(request.content))
        return httpx.Response(404)

    def search(self, body):
        form = body["search_data"]["form_data"]["data"]
        slug = BY_TOKEN[form["category"]["str"]["value"]]
        after = body.get("pagination_data")
        kind = (form.get("business-type") or {}).get("repeated_string", {}).get("value")
        if kind:
            name = fixture_for(slug, KIND[kind[0]])
            return self.page([MANIFEST[name]["token"]], more=False, count=1)
        if "recent_ads" in form:
            names = sorted(n for n, m in MANIFEST.items() if m["category"] == slug)
            return self.page([], cards=[card(MANIFEST[n]["token"], "نردبان شده در ارومیه" if i % 2 else "دقایقی پیش در ارومیه")
                                        for i, n in enumerate(names)], more=False, count=len(names))
        if "credit" in form and slug.startswith("buy-") and after:
            return httpx.Response(400, json={"message": f"invalid filter for {form['category']['str']['value']}: credit"})
        tokens = [f"x{slug[:3]}{i:04d}z" for i in range(30)]
        if after:
            return self.page(tokens[24:], more=False, count=30)
        return self.page(tokens[:24], more=True, count=30)

    @staticmethod
    def page(tokens, *, more, count, cards=None):
        widgets = cards or [card(t, "لحظاتی پیش") for t in tokens]
        return httpx.Response(200, json={
            "list_widgets": widgets, "map_data": {"post_count": count},
            "pagination": {"has_next_page": more, "data": {"page": 1, "last_post_date": "2026-09-29T10:00:00Z"}}})


@pytest.fixture
def divar(monkeypatch):
    fake = FakeDivar()
    transport = httpx.MockTransport(fake)

    def client(*a, **k):
        k["transport"] = transport
        return _REAL_CLIENT(*a, **k)

    monkeypatch.setattr(dc.httpx, "AsyncClient", client)
    monkeypatch.setattr(dc, "_city_cache", {})
    monkeypatch.setattr(dc, "_PAGE_PAUSE", 0)
    return fake


async def nothing(*_a, **_k):
    return None


@pytest.fixture
async def result(divar, tmp_path):
    report = await dlc.run(["--root", str(tmp_path), "--gap", "0"], sleep=nothing)
    return report, tmp_path


def written(root):
    return {p: p.read_text(encoding="utf-8") for p in root.rglob("*")
            if p.is_file() and ".divar_live" not in p.parts}


class TestEverySectionReports:
    async def test_the_report_has_every_section_and_no_section_stopped(self, result):
        report, root = result
        for name in dlc.SECTIONS:
            assert name in report and "problem" not in report[name], (name, report.get(name))
        assert (root / "docs" / "divar_live" / "report.md").exists()
        assert json.loads((root / "docs" / "divar_live" / "report.json").read_text())["meta"]["city"] == "urmia"

    async def test_the_forms_are_written_with_the_options_only_divar_has(self, result):
        report, root = result
        schema = json.loads((root / "app" / "services" / "divar_filters.json").read_text())
        assert schema["source"] == "divar"
        cooling = {f["key"]: f for f in schema["categories"]["buy-apartment"]["filters"]}["cooling_system"]
        assert [o["value"] for o in cooling["options"]] == ["v1", "v2"]
        assert report["filters"]["options_still_unknown"] == []
        assert report["filters"]["not_read"] == []

    async def test_the_second_page_is_asked_for_and_the_control_is_refused(self, result):
        report, _ = result
        for case in report["page2"]["cases"]:
            assert case["page1"] == 200 and case["page2"] == 200, case
        control = report["page2"]["control"]
        assert control["page2"] == 400 and "credit" in control["divar_said"]

    async def test_each_count_is_compared_with_the_whole_list(self, result):
        report, _ = result
        rows = report["counts"]["rows"]
        assert [r["category"] for r in rows][:3] == ["buy-old-house", "rent-industrial-agricultural-property", "buy-office"]
        assert all(r["verdict"] == "برابر" and r["walked"] == 30 == r["divar_count"] for r in rows), rows


class TestTheSavedPages:
    async def test_an_owner_and_an_agency_page_for_every_category(self, result):
        _, root = result
        manifest = json.loads((root / "tests" / "fixtures" / "divar_live" / "expected.json").read_text())
        assert len(manifest) == 2 * len(dlc.panel_categories())
        for name, entry in manifest.items():
            assert (root / "tests" / "fixtures" / "divar_live" / name).exists()
            assert name == f"{entry['category']}-{entry['advertiser']}.html"

    async def test_each_page_reads_as_divars_own_data_says(self, result):
        """The cleaning leaves everything the scraper reads: no field differs
        from what the ad's data states, and each stays in its category."""
        report, root = result
        manifest = json.loads((root / "tests" / "fixtures" / "divar_live" / "expected.json").read_text())
        for name, entry in manifest.items():
            assert entry["expect"], name
            assert entry["mismatch"] == {}, (name, entry["mismatch"])
        assert all(r["kept"] and r["rendered_rows"] for r in report["pages"]["rows"])

    async def test_nothing_personal_and_no_real_token_is_written(self, result):
        _, root = result
        real_tokens = {m["token"] for m in MANIFEST.values()}
        files = written(root)
        assert files
        for path, text in files.items():
            assert PHONE not in text and EMAIL not in text and AGENT not in text, path
            if path.name not in ("expected.json", "report.json"):   # their numbers are prices
                assert dlc.privacy_problems(text) == [], path
            assert not any(t in text for t in real_tokens), path

    async def test_the_raw_answers_stay_in_the_ignored_folder(self, result):
        _, root = result
        raw = list((root / ".divar_live").iterdir())
        assert any(p.suffix == ".html" for p in raw)
        assert ".divar_live/" in GITIGNORE.split()


class TestTheCards:
    async def test_the_card_texts_are_set_against_the_ads_own_date(self, result):
        report, root = result
        cards = report["cards"]
        assert cards["recent_ads"] == "3d"
        assert cards["cards"] == len(cards["opened"]) == 4
        assert cards["card_keys"]["bottom_description_text"] == 4
        # the hand-built pages were all published on 1405/07/04
        assert all(o["posted_at"] for o in cards["opened"])
        assert sum(o["card_says_ladder"] for o in cards["opened"]) == 2
        saved = json.loads((root / "tests" / "fixtures" / "divar_live" / "search-buy-apartment-page1.json").read_text())
        assert [w["data"]["token"] for w in saved["list_widgets"]] == [f"lvcard{i:03d}" for i in range(4)]


class TestTheCleaner:
    def test_a_number_written_any_way_is_masked(self):
        for text in ("۰۹۱۲ ۳۴۵ ۶۷۸۹", "+98 912 345 6789", "041-33445566", "٠٩١٢٣٤٥٦٧٨٩", EMAIL):
            assert dlc.mask_text(f"تماس {text} بگیرید") == f"تماس {dlc.HIDDEN} بگیرید", text

    def test_prices_and_years_are_not(self):
        for text in ("۵٬۱۰۰٬۰۰۰٬۰۰۰ تومان", "ساخت ۱۴۰۲", "۸۵ متر", "طبقه ۲ از ۵"):
            assert dlc.mask_text(text) == text


class TestWithoutTheAdApi:
    async def test_the_pages_are_still_saved_and_the_report_says_what_was_missing(self, divar, tmp_path):
        divar.post_api = False
        report = await dlc.run(["--root", str(tmp_path), "--gap", "0", "--only", "pages"], sleep=nothing)
        rows = report["pages"]["rows"]
        assert rows and all("page's own state" in r["divar_data"] for r in rows)
        assert all(r["expect"] == {} and r["kept"] for r in rows)
        md = (tmp_path / "docs" / "divar_live" / "report.md").read_text(encoding="utf-8")
        assert "دادهٔ دیوار برای مقایسه نیامد" in md
        assert len(list((tmp_path / "tests" / "fixtures" / "divar_live").glob("*.html"))) == len(rows)
