"""
Reading a Divar search link back into the scrape form.

«یه سکشن اضافه کن که فیلتر رو تو دیوار بزنم، لینکشو کپی کنم و بدم به اسکرپر.»

The real link this was built against, copied from the browser bar:

    divar.ir/s/urmia/rent-apartment?bbox=44.64,37.13,45.62,37.71
        &business-type=personal&credit=790000000-810000000&map_bbox=…

Two of those carry over, two cannot. The two that cannot are reported by
name: a scrape that quietly ignores half of what you drew on the map is
worse than one that says it did.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_link.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.services.divar_link import parse_search_url  # noqa: E402

REAL = ("https://divar.ir/s/urmia/rent-apartment"
        "?bbox=44.6483536%2C37.1391678%2C45.6290474%2C37.7174263"
        "&business-type=personal&credit=790000000-810000000"
        "&map_bbox=44.648355%2C37.139168%2C45.62")


class TestTheLinkThatPromptedIt:
    def test_the_city_comes_through(self):
        got = parse_search_url(REAL)
        assert got["city"] == "urmia"
        assert got["city_name"] == "ارومیه"

    def test_the_category_comes_through(self):
        assert parse_search_url(REAL)["category"] == "rent-apartment"

    def test_the_deposit_band_comes_through(self):
        f = parse_search_url(REAL)["filters"]
        assert f["min_deposit"] == 790000000
        assert f["max_deposit"] == 810000000

    def test_the_advertiser_type_comes_through(self):
        assert parse_search_url(REAL)["filters"]["advertiser_type"] == "personal"

    def test_the_map_box_is_reported_as_not_carried_over(self):
        assert "محدودهٔ نقشه" in parse_search_url(REAL)["ignored"]

    def test_the_map_box_is_named_once_not_twice(self):
        """bbox and map_bbox are the same fact told twice."""
        assert parse_search_url(REAL)["ignored"].count("محدودهٔ نقشه") == 1

    def test_nothing_is_invented(self):
        f = parse_search_url(REAL)["filters"]
        assert "min_price" not in f and "min_area" not in f


class TestEveryRangeDivarWrites:
    def base(self, qs):
        return parse_search_url(f"https://divar.ir/s/tehran/buy-apartment?{qs}")["filters"]

    def test_price(self):
        f = self.base("price=1000000000-2000000000")
        assert (f["min_price"], f["max_price"]) == (1000000000, 2000000000)

    def test_rent(self):
        f = self.base("rent=5000000-9000000")
        assert (f["min_rent"], f["max_rent"]) == (5000000, 9000000)

    def test_area(self):
        f = self.base("size=80-120")
        assert (f["min_area"], f["max_area"]) == (80, 120)

    def test_rooms(self):
        f = self.base("rooms=2-3")
        assert (f["min_rooms"], f["max_rooms"]) == (2, 3)

    def test_an_open_top_end(self):
        f = self.base("size=80-")
        assert f["min_area"] == 80 and "max_area" not in f

    def test_an_open_bottom_end(self):
        f = self.base("size=-120")
        assert f["max_area"] == 120 and "min_area" not in f

    def test_an_agency_search(self):
        assert self.base("business-type=real-estate-business")["advertiser_type"] == "agency"

    def test_photos_only(self):
        assert self.base("has-photo=true")["has_images"] is True

    def test_photos_not_asked_for_is_absent_not_false(self):
        """has_images=False would mean «ads WITHOUT photos», which is not what
        an unticked box says."""
        assert "has_images" not in self.base("price=1-2")


class TestItIsTheInverseOfWhatWeSend:
    def test_a_round_trip_survives(self):
        from app.services.divar_count import build_search_query
        sent = build_search_query(
            advertiser_type="personal", has_images=True,
            min_price=None, max_price=None,
            min_deposit=790000000, max_deposit=810000000,
            min_rent=None, max_rent=None, min_area=80, max_area=120,
        )
        back = parse_search_url(f"https://divar.ir/s/urmia/rent-apartment?{sent}")
        assert back["filters"] == {
            "min_deposit": 790000000, "max_deposit": 810000000,
            "min_area": 80, "max_area": 120,
            "advertiser_type": "personal", "has_images": True,
        }
        assert back["ignored"] == []


class TestLinksItRefuses:
    def test_a_listing_link_is_sent_to_single_scrape(self):
        got = parse_search_url("https://divar.ir/v/abc/gaVSjr9i")
        assert got["error"] and "اسکرپ تکی" in got["error"]

    def test_another_site(self):
        assert "از دیوار نیست" in parse_search_url("https://sheypoor.com/s/urmia")["error"]

    def test_the_divar_home_page(self):
        assert parse_search_url("https://divar.ir/")["error"]

    def test_an_unknown_city(self):
        got = parse_search_url("https://divar.ir/s/atlantis/rent-apartment")
        assert got["error"] and "atlantis" in got["error"]

    def test_a_category_we_do_not_scrape(self):
        """Silently scraping the whole city instead would be worse."""
        got = parse_search_url("https://divar.ir/s/tehran/mobile-phones")
        assert got["error"] and "mobile-phones" in got["error"]

    def test_nothing_at_all(self):
        assert parse_search_url("")["error"]
        assert parse_search_url(None)["error"]


class TestWhatItToleratesFromACopyPaste:
    def test_a_missing_scheme(self):
        assert parse_search_url("divar.ir/s/urmia/rent-apartment")["city"] == "urmia"

    def test_www(self):
        assert parse_search_url("https://www.divar.ir/s/urmia")["city"] == "urmia"

    def test_surrounding_whitespace(self):
        assert parse_search_url("  https://divar.ir/s/urmia/rent-apartment \n")["city"] == "urmia"

    def test_a_city_with_no_category(self):
        got = parse_search_url("https://divar.ir/s/urmia")
        assert got["city"] == "urmia" and got["category"] is None and not got["error"]

    def test_a_district_path_segment_is_reported(self):
        got = parse_search_url("https://divar.ir/s/urmia/rent-apartment/valiasr")
        assert "منطقه/محله" in got["ignored"]

    def test_tracking_parameters_are_not_reported_as_lost_filters(self):
        got = parse_search_url(
            "https://divar.ir/s/urmia/rent-apartment?page=2&utm_source=telegram")
        assert got["ignored"] == []


class TestTheEndpoint:
    def test_it_is_routed(self):
        from app.api.routes import scraper as sr
        assert "/parse-link" in [r.path for r in sr.router.routes]

    def test_it_is_a_post_taking_the_url_in_the_body(self):
        from app.api.routes import scraper as sr
        route = [r for r in sr.router.routes if r.path == "/parse-link"][0]
        assert route.methods == {"POST"}
        assert "url" in sr.DivarLinkRequest.model_fields

    def test_a_bad_link_is_a_422_with_the_persian_reason(self):
        import inspect
        from app.api.routes import scraper as sr
        src = inspect.getsource(sr.parse_divar_link)
        assert "status_code=422" in src
        assert 'got["error"]' in src

    def test_it_does_not_start_a_scrape(self):
        """Filling the form is the point: a link that quietly became a run
        would hide whichever half of it did not carry over."""
        import inspect
        from app.api.routes import scraper as sr
        src = inspect.getsource(sr.parse_divar_link)
        assert "start_scraping" not in src and "BackgroundTasks" not in src


class TestThePanelSection:
    def _js(self):
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return open(os.path.join(root, "frontend/js/app.js"), encoding="utf-8").read()

    def _html(self):
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return open(os.path.join(root, "frontend/index.html"), encoding="utf-8").read()

    def test_there_is_somewhere_to_paste(self):
        assert 'id="scraper-link"' in self._html()

    def test_enter_submits_it(self):
        i = self._html().index('id="scraper-link"')
        assert "applyDivarLink()" in self._html()[i:i + 500]

    def test_it_fills_the_city_and_category(self):
        js = self._js()
        i = js.index("async function applyDivarLink()")
        block = js[i:i + 3000]
        assert "_setCityValue" in block and "scraper-category" in block

    def test_it_clears_the_old_bands_before_writing_the_new_ones(self):
        """A leftover from the previous link would silently narrow the scrape."""
        js = self._js()
        i = js.index("async function applyDivarLink()")
        block = js[i:i + 3000]
        assert "el.value = ''" in block
        assert block.index("el.value = ''") < block.index("d.filters?.[key]")

    def test_it_says_what_did_not_carry_over(self):
        js = self._js()
        i = js.index("async function applyDivarLink()")
        assert "منتقل نشدند" in js[i:i + 3000]

    def test_the_unreadable_link_message_is_shown_in_place(self):
        js = self._js()
        i = js.index("async function applyDivarLink()")
        assert "text-danger" in js[i:i + 3000]
