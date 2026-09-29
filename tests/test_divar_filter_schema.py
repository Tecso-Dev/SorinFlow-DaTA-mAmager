"""
Every filter Divar has for a category is sent, and nothing else is (#27).

Divar's form differs per category. A filter the category does not have is not
ignored: Divar answers the first page and refuses the second with HTTP 400
(«invalid filter for apartment-sell: credit»), and runs 39, 41, 44, 45 and 47
each ended on 24 listings that way. Meanwhile rooms, amenities, building age
and the rest — which Divar applies itself — were left for the scraper to check
after opening every ad.

All against the committed schema (app/services/divar_filters.json), read off
Divar and published in the issue. Pure — no network, no database.
"""
from datetime import date, timedelta

import pytest

from app.config import CATEGORIES
from app.services import divar_count as dc
from app.services import divar_filters as df

SCHEMA = df.committed()
ALL_SLUGS = sorted(SCHEMA["categories"])

# The issue's table, one row per family it names.
BUY = ["buy-apartment", "buy-residential", "buy-store", "buy-old-house"]
RENT = ["rent-apartment", "rent-residential", "rent-store", "rent-office"]
SHORT = ["rent-temporary", "rent-temporary-villa", "rent-temporary-workspace"]
SERVICES = ["real-estate-services", "contribution-construction", "real-estate"]

# Every old field set at once — the widest config the form could ever save.
EVERYTHING = dict(
    min_price=1_000_000_000, max_price=9_000_000_000,
    min_deposit=100_000_000, max_deposit=900_000_000,
    min_rent=1_000_000, max_rent=30_000_000,
    min_price_per_meter=50_000_000, max_price_per_meter=150_000_000,
    min_area=60, max_area=140, min_rooms=2, max_rooms=3,
    has_images=True, has_elevator=True, has_parking=True,
    has_storage=True, has_balcony=True, advertiser_type="personal",
)

TODAY = date(2026, 9, 29)


def form_keys(slug, **kw):
    return set(dc.build_form_data(slug, today=TODAY, **kw)) - {"category"}


class TestTheCommittedSchema:
    def test_every_slug_in_the_issue_is_there(self):
        for slug in ("real-estate", "contribution-construction", "pre-sell-home",
                     "rent-temporary-suite-apartment", "rent-temporary-villa",
                     "rent-temporary-workspace", *CATEGORIES):
            assert slug in SCHEMA["categories"], slug

    def test_every_category_has_the_four_common_filters(self):
        for slug in ALL_SLUGS:
            keys = set(df.filters_for(slug, SCHEMA))
            assert {"has-photo", "has-video", "business-type", "recent_ads"} <= keys, slug

    @pytest.mark.parametrize("slug,own", [
        ("buy-residential", {"price", "size", "price_per_square", "parking", "balcony", "warehouse"}),
        ("rent-residential", {"credit", "rent", "size", "rooms", "parking", "balcony", "warehouse"}),
        ("buy-old-house", {"price", "size", "price_per_square", "parking"}),
        ("buy-commercial-property", {"price", "size", "price_per_square", "bizzDeed"}),
        ("rent-store", {"credit", "rent", "size"}),
        ("rent-temporary", {"daily_rent", "person_capacity", "size", "rooms"}),
        ("real-estate-services", set()),
    ])
    def test_a_category_has_exactly_the_issues_filters(self, slug, own):
        common = {"has-photo", "has-video", "business-type", "recent_ads"}
        assert set(df.filters_for(slug, SCHEMA)) == own | common

    def test_tokens_agree_with_the_scrapers(self):
        for slug, token in dc.CATEGORY_TOKENS.items():
            assert SCHEMA["categories"][slug]["token"] == token, slug

    def test_every_filter_has_a_known_type_and_a_persian_title(self):
        for slug in ALL_SLUGS:
            for f in df.filters_for(slug, SCHEMA).values():
                assert f["type"] in df.TYPES, (slug, f)
                assert f["title"] and f["title"] != f["key"], (slug, f)

    def test_the_value_shapes_from_the_issue(self):
        rooms = df.filters_for("buy-apartment", SCHEMA)["rooms"]
        assert df.option_values(rooms) == ["بدون اتاق", "یک", "دو", "سه", "چهار", "بیشتر"]
        f = df.filters_for("buy-apartment", SCHEMA)
        assert df.option_values(f["deed_type"]) == ["single_page", "multi_page", "written_agreement", "other"]
        assert set(df.option_values(f["building_direction"])) == {"east", "west", "north", "south"}
        assert df.option_values(f["toilet"]) == ["squat", "seat", "squat_seat"]
        assert df.option_values(f["recent_ads"]) == ["3h", "12h", "1d", "3d", "7d"]
        assert df.option_values(f["business-type"]) == ["personal", "real-estate-business"]
        assert f["toilet"]["type"] == "str" and f["recent_ads"]["type"] == "str"

    def test_options_the_issue_does_not_list_are_marked_unknown(self):
        f = df.filters_for("buy-apartment", SCHEMA)
        for key in ("cooling_system", "heating_system", "floor_type", "warm_water_provider"):
            assert f[key]["options"] is None and not df.options_known(f[key])

    def test_money_has_a_unit(self):
        f = df.filters_for("rent-apartment", SCHEMA)
        assert f["credit"]["unit"] == "تومان" and f["size"]["unit"]

    def test_the_seed_script_reproduces_the_file(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("fdf", "scripts/fetch_divar_filters.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert mod.seed_schema() == SCHEMA


class TestNothingIrrelevantIsEverSent:
    @pytest.mark.parametrize("slug", ALL_SLUGS)
    def test_the_whole_old_form_on_any_category(self, slug):
        """Every old field set at once: only keys the category has go out."""
        sent = form_keys(slug, **EVERYTHING, posted_date=(TODAY - timedelta(days=1)).isoformat())
        assert sent <= set(df.filters_for(slug, SCHEMA)), sent - set(df.filters_for(slug, SCHEMA))

    @pytest.mark.parametrize("slug", ALL_SLUGS)
    def test_the_link_query_too(self, slug):
        q = dc.build_search_query(slug, today=TODAY, **EVERYTHING)
        keys = {part.split("=")[0] for part in q.split("&") if part}
        assert keys <= set(df.filters_for(slug, SCHEMA))

    def test_the_bug_itself_credit_on_a_sale(self):
        assert "credit" not in form_keys("buy-apartment", min_deposit=1, max_deposit=5)
        assert "rent" not in form_keys("buy-store", max_rent=5)

    def test_price_on_a_rental(self):
        assert "price" not in form_keys("rent-apartment", min_price=1)
        assert "price_per_square" not in form_keys("rent-apartment", max_price_per_meter=5)

    def test_a_divar_key_the_category_lacks_is_dropped(self):
        f = dc.build_form_data("rent-store", divar_filters={"building-age": {"max": 5}, "deed_type": ["single_page"]})
        assert "building-age" not in f and "deed_type" not in f


class TestEveryAcceptedFilterIsSent:
    @pytest.mark.parametrize("slug", BUY)
    def test_buy(self, slug):
        own = set(df.filters_for(slug, SCHEMA))
        sent = form_keys(slug, **EVERYTHING)
        for key in ("price", "size", "price_per_square", "business-type", "has-photo"):
            assert key in sent, (slug, key)
        for key, divar in (("has_parking", "parking"), ("has_balcony", "balcony"),
                           ("has_storage", "warehouse"), ("has_elevator", "elevator")):
            assert (divar in sent) == (divar in own), (slug, key)

    @pytest.mark.parametrize("slug", RENT)
    def test_rent(self, slug):
        sent = form_keys(slug, **EVERYTHING)
        assert {"credit", "rent", "size", "business-type", "has-photo"} <= sent
        assert ("rooms" in sent) == ("rooms" in df.filters_for(slug, SCHEMA))

    @pytest.mark.parametrize("slug", SHORT)
    def test_short_term(self, slug):
        sent = form_keys(slug, **EVERYTHING, divar_filters={
            "daily_rent": {"max": 2_000_000}, "person_capacity": {"min": 4}})
        assert {"daily_rent", "person_capacity", "size", "rooms"} <= sent
        assert not {"credit", "rent", "price"} & sent

    @pytest.mark.parametrize("slug", SERVICES)
    def test_services_take_only_the_common_four(self, slug):
        sent = form_keys(slug, **EVERYTHING)
        assert sent == {"business-type", "has-photo"}

    def test_the_value_shapes(self):
        f = dc.build_form_data("buy-apartment", today=TODAY, min_price=5, has_parking=True,
                               min_rooms=2, max_rooms=2, divar_filters={
                                   "building-age": {"max": 5}, "floor": {"min": 3},
                                   "deed_type": ["single_page"], "toilet": "seat",
                                   "rebuilt": True, "has-video": True})
        assert f["price"] == {"number_range": {"minimum": "5"}}
        assert f["building-age"] == {"number_range": {"maximum": "5"}}
        assert f["floor"] == {"number_range": {"minimum": "3"}}
        assert f["parking"] == {"boolean": {"value": True}}
        assert f["rebuilt"] == {"boolean": {"value": True}}
        assert f["has-video"] == {"boolean": {"value": True}}
        assert f["rooms"] == {"repeated_string": {"value": ["دو"]}}
        assert f["deed_type"] == {"repeated_string": {"value": ["single_page"]}}
        assert f["toilet"] == {"str": {"value": "seat"}}
        assert f["category"] == {"str": {"value": "apartment-sell"}}

    def test_an_option_divar_does_not_have_is_not_sent(self):
        p = dc.plan_filters("buy-apartment", today=TODAY,
                            divar_filters={"deed_type": ["single_page", "made-up"], "toilet": "gold"})
        assert p.form["deed_type"] == {"repeated_string": {"value": ["single_page"]}}
        assert "toilet" not in p.form
        assert any("سرویس بهداشتی" in n for n in p.notes)

    def test_a_filter_whose_options_are_unknown_is_carried_as_divar_wrote_it(self):
        """Only a Divar link carries one, and its value is Divar's own word."""
        f = dc.build_form_data("rent-apartment", divar_filters={"cooling_system": ["split"]})
        assert f["cooling_system"] == {"repeated_string": {"value": ["split"]}}

    def test_false_is_not_a_filter_divar_can_express(self):
        """«Without a lift» has no Divar switch; the scraper checks it itself."""
        p = dc.plan_filters("buy-apartment", has_elevator=False)
        assert "elevator" not in p.form
        assert "has_elevator" not in p.local_off


class TestRooms:
    @pytest.mark.parametrize("lo,hi,want", [
        (2, 3, ["دو", "سه"]),
        (0, 1, ["بدون اتاق", "یک"]),
        (None, 2, ["بدون اتاق", "یک", "دو"]),
        (3, None, ["سه", "چهار", "بیشتر"]),
        (5, None, ["بیشتر"]),
        (4, 4, ["چهار"]),
        (2, 7, ["دو", "سه", "چهار", "بیشتر"]),
    ])
    def test_a_band_becomes_divars_options(self, lo, hi, want):
        f = dc.build_form_data("buy-apartment", min_rooms=lo, max_rooms=hi)
        assert f["rooms"] == {"repeated_string": {"value": want}}

    def test_an_exact_band_is_not_checked_again(self):
        p = dc.plan_filters("buy-apartment", min_rooms=2, max_rooms=3)
        assert {"min_rooms", "max_rooms"} <= p.local_off

    def test_a_band_that_bistar_only_approximates_is_still_checked(self):
        """«بیشتر» is five and up: a band ending at 7 lets 8 through."""
        p = dc.plan_filters("buy-apartment", min_rooms=2, max_rooms=7)
        assert "rooms" in p.form
        assert "max_rooms" not in p.local_off

    def test_an_empty_band_sends_nothing(self):
        assert "rooms" not in dc.build_form_data("buy-apartment", min_rooms=4, max_rooms=2)

    def test_a_category_without_rooms_checks_them_itself_and_says_so(self):
        p = dc.plan_filters("buy-residential", min_rooms=2)
        assert "rooms" not in p.form
        assert "min_rooms" not in p.local_off
        assert "تعداد اتاق" in p.after_scrape


class TestRecentAds:
    @pytest.mark.parametrize("days_ago,want", [
        (0, "1d"), (1, "3d"), (2, "3d"), (3, "7d"), (6, "7d"), (7, None), (20, None),
    ])
    def test_a_publish_date(self, days_ago, want):
        day = (TODAY - timedelta(days=days_ago)).isoformat()
        assert dc.recent_ads_for(posted_date=day, today=TODAY) == want
        f = dc.build_form_data("buy-apartment", posted_date=day, today=TODAY)
        if want:
            assert f["recent_ads"] == {"str": {"value": want}}
        else:
            assert "recent_ads" not in f

    @pytest.mark.parametrize("hours,want", [
        (1, "3h"), (3, "3h"), (4, "12h"), (12, "12h"), (24, "1d"), (25, "3d"),
        (72, "3d"), (100, "7d"), (168, "7d"), (169, None),
    ])
    def test_a_maximum_age(self, hours, want):
        assert dc.recent_ads_for(max_age_hours=hours) == want

    def test_the_date_wins_over_the_age(self):
        day = (TODAY - timedelta(days=1)).isoformat()
        assert dc.recent_ads_for(posted_date=day, max_age_hours=3, today=TODAY) == "3d"

    def test_the_date_wins_over_a_recent_ads_picked_by_hand(self):
        """A hand-picked «۳ ساعت» on a run for yesterday would empty it."""
        day = (TODAY - timedelta(days=1)).isoformat()
        f = dc.build_form_data("buy-apartment", posted_date=day, today=TODAY,
                               divar_filters={"recent_ads": "3h"})
        assert f["recent_ads"] == {"str": {"value": "3d"}}

    def test_without_a_date_a_hand_picked_one_is_sent(self):
        f = dc.build_form_data("buy-apartment", divar_filters={"recent_ads": "12h"})
        assert f["recent_ads"] == {"str": {"value": "12h"}}

    def test_today_is_a_tehran_day(self):
        """20:45 UTC on the 28th is already the 29th in Tehran."""
        from datetime import datetime, timezone
        late = datetime(2026, 9, 28, 20, 45, tzinfo=timezone.utc)
        assert dc.tehran_today(late) == date(2026, 9, 29)

    def test_the_link_query_carries_it(self):
        day = TODAY.isoformat()
        assert "recent_ads=1d" in dc.build_search_query("rent-apartment", posted_date=day, today=TODAY)

    def test_the_date_is_still_checked_by_the_scraper(self):
        """recent_ads is bump time, not publish time: the day check stays."""
        p = dc.plan_filters("buy-apartment", posted_date=TODAY.isoformat(), today=TODAY)
        assert "posted_date" not in p.local_off and "target_day" not in p.local_off


class TestAnOldConfigStillWorks:
    def test_the_six_fields_divar_took_before_come_out_the_same(self):
        f = dc.build_form_data("rent-residential", advertiser_type="personal",
                               has_images=True, max_deposit=100_000_000, min_area=80)
        assert f == {
            "category": {"str": {"value": "residential-rent"}},
            "business-type": {"repeated_string": {"value": ["personal"]}},
            "has-photo": {"boolean": {"value": True}},
            "credit": {"number_range": {"maximum": "100000000"}},
            "size": {"number_range": {"minimum": "80"}},
        }

    def test_the_amenities_map_onto_divars_names(self):
        f = dc.build_form_data("buy-apartment", has_elevator=True, has_parking=True,
                               has_storage=True, has_balcony=True)
        for key in ("elevator", "parking", "warehouse", "balcony"):
            assert f[key] == {"boolean": {"value": True}}

    def test_price_per_meter_maps_to_price_per_square(self):
        f = dc.build_form_data("buy-apartment", min_price_per_meter=10, max_price_per_meter=20)
        assert f["price_per_square"] == {"number_range": {"minimum": "10", "maximum": "20"}}

    def test_what_divar_applied_is_not_checked_again(self):
        p = dc.plan_filters("buy-apartment", **EVERYTHING)
        for name in ("min_price", "max_price", "min_area", "max_area", "has_elevator",
                     "has_parking", "has_storage", "has_balcony", "has_images",
                     "advertiser_type", "min_price_per_meter", "max_price_per_meter"):
            assert name in p.local_off, name

    def test_what_divar_did_not_apply_is(self):
        p = dc.plan_filters("rent-residential", has_elevator=True)
        assert "elevator" not in p.form and "has_elevator" not in p.local_off
        assert "آسانسور" in p.after_scrape


class TestItIsSaidInPlainPersian:
    def test_a_deposit_on_a_sale(self):
        p = dc.plan_filters("buy-apartment", min_deposit=1)
        assert any("ودیعه" in n and "معنا ندارد و اعمال نشد" in n and "خرید آپارتمان" in n
                   for n in p.notes), p.notes
        assert "ودیعه" in p.not_applied
        assert {"min_deposit", "max_deposit"} <= p.local_off

    def test_a_filter_the_scraper_checks_itself(self):
        p = dc.plan_filters("rent-residential", has_elevator=True)
        assert any("آسانسور" in n and "بعد از باز کردن" in n for n in p.notes), p.notes

    def test_a_divar_filter_the_category_lacks(self):
        p = dc.plan_filters("rent-store", divar_filters={"deed_type": ["single_page"]})
        assert any("نوع سند" in n and "اجاره مغازه" in n for n in p.notes), p.notes

    def test_nothing_to_say_when_everything_applies(self):
        assert dc.plan_filters("buy-apartment", min_price=1, has_parking=True).notes == []

    def test_a_short_term_rental_does_not_take_a_monthly_deposit(self):
        p = dc.plan_filters("rent-temporary", max_deposit=5)
        assert "credit" not in p.form
        assert any("معنا ندارد" in n for n in p.notes)
        assert "max_deposit" in p.local_off


class TestUnsupportedFilters:
    def test_names_what_the_category_leaves_to_the_scraper(self):
        out = dc.unsupported_filters("buy-residential", min_rooms=2, has_elevator=True,
                                     max_price_per_meter=9)
        assert "تعداد اتاق" in out and "آسانسور" in out
        assert "قیمت هر متر" not in out          # buy-residential has price_per_square

    def test_nothing_when_none_are_set(self):
        assert dc.unsupported_filters("buy-residential", min_rooms=None, has_parking=False) == []


class TestARefusalNamesAnyFilter:
    """A 400 now can name any filter Divar has, not only the six sent before."""

    def test_rooms(self):
        advice = dc.refusal_advice(400, "invalid filter for shop-rent: rooms", "اجاره مغازه")
        assert "«تعداد اتاق»" in advice and "اجاره مغازه" in advice

    def test_a_category_token_is_still_not_mistaken_for_a_filter(self):
        advice = dc.refusal_advice(400, "invalid filter for shop-rent: price")
        assert "«قیمت»" in advice
