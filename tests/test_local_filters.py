"""
The run's own filters, one listing that stays and one that goes for each.

Divar applies the same filters itself now (rooms, elevator, parking, storage,
balcony, price per metre, building age…), so the ones the scraper applies after
opening an ad are a safety net: they catch what Divar let through, and they are
also what decides whether a phone number is worth asking for. Until now the net
had two copies — `pre_contact_skip` before the reveal and a longer block inside
the scrape loop after it — and no test ever ran either of them: the test file
for the first one exercised a copy of the function pasted into the test.

The two disagreed, silently:

  * a deposit of 0 («مجانی» — a full-mortgage ad) was dropped by
    `pre_contact_skip` (0 < the minimum) and kept by the loop (0 is falsy), so
    the ad was NOT asked for a number and was saved anyway, without one,
    counted as a failure;
  * on a category the panel calls neither buy nor rent, `pre_contact_skip`
    still applied the sale price bands and the loop applied none.

They are one function now, `DivarScraper.local_filter_skip`, which both call.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_lf.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.scraper.divar_scraper import DivarScraper  # noqa: E402

skip = DivarScraper.local_filter_skip


def stays(detail, listing_type="buy", **filters):
    return skip(detail, listing_type, filters) is None


def goes(detail, listing_type="buy", **filters):
    return skip(detail, listing_type, filters) is not None


def why(detail, listing_type="buy", **filters):
    return skip(detail, listing_type, filters)


class TestNoFilters:
    def test_a_run_without_filters_keeps_everything(self):
        assert stays({"total_price": 5_000_000_000, "area": 90, "rooms": 2})
        assert stays({})

    def test_an_unfiltered_field_is_not_looked_at(self):
        assert stays({"advertiser_type": "agency", "has_parking": False})


class TestPrice:
    def test_a_sale_inside_the_band_stays(self):
        assert stays({"total_price": 5_000_000_000},
                     min_price=4_000_000_000, max_price=6_000_000_000)

    def test_a_sale_below_the_minimum_goes(self):
        assert why({"total_price": 3_000_000_000}, min_price=4_000_000_000) == \
            "price 3000000000 < min 4000000000"

    def test_a_sale_above_the_maximum_goes(self):
        assert why({"total_price": 7_000_000_000}, max_price=6_000_000_000) == \
            "price 7000000000 > max 6000000000"

    def test_the_price_falls_back_when_the_total_is_missing(self):
        assert goes({"price": 3_000_000_000}, min_price=4_000_000_000)
        assert stays({"price": 5_000_000_000}, min_price=4_000_000_000)

    def test_an_ad_that_does_not_state_a_price_stays(self):
        """Missing is not the same as failing."""
        assert stays({}, min_price=4_000_000_000, max_price=6_000_000_000)
        assert stays({"total_price": None}, min_price=4_000_000_000)

    def test_a_free_price_is_not_a_price(self):
        assert stays({"total_price": 0}, min_price=4_000_000_000)


class TestPricePerMetre:
    def test_inside_the_band_stays(self):
        assert stays({"price_per_meter": 50_000_000},
                     min_price_per_meter=40_000_000, max_price_per_meter=60_000_000)

    def test_below_the_minimum_goes(self):
        assert why({"price_per_meter": 30_000_000}, min_price_per_meter=40_000_000) == \
            "price/m² 30000000 < min 40000000"

    def test_above_the_maximum_goes(self):
        assert why({"price_per_meter": 80_000_000}, max_price_per_meter=60_000_000) == \
            "price/m² 80000000 > max 60000000"

    def test_an_ad_without_a_price_per_metre_stays(self):
        assert stays({"total_price": 5_000_000_000}, min_price_per_meter=40_000_000)

    def test_it_is_a_sale_filter_only(self):
        """A rental has no price per metre for it to judge."""
        assert stays({"price_per_meter": 30_000_000}, "rent", min_price_per_meter=40_000_000)


class TestDepositAndRent:
    def test_a_deposit_inside_the_band_stays(self):
        assert stays({"deposit": 250_000_000}, "rent",
                     min_deposit=200_000_000, max_deposit=300_000_000)

    def test_a_deposit_below_the_minimum_goes(self):
        assert why({"deposit": 50_000_000}, "rent", min_deposit=200_000_000) == \
            "deposit 50000000 < min 200000000"

    def test_a_deposit_above_the_maximum_goes(self):
        assert why({"deposit": 900_000_000}, "rent", max_deposit=300_000_000) == \
            "deposit 900000000 > max 300000000"

    def test_a_rent_inside_the_band_stays(self):
        assert stays({"rent_price": 8_000_000}, "rent", min_rent=5_000_000, max_rent=10_000_000)

    def test_a_rent_below_the_minimum_goes(self):
        assert why({"rent_price": 2_000_000}, "rent", min_rent=5_000_000) == \
            "rent 2000000 < min 5000000"

    def test_a_rent_above_the_maximum_goes(self):
        assert why({"rent_price": 30_000_000}, "rent", max_rent=10_000_000) == \
            "rent 30000000 > max 10000000"

    def test_an_ad_without_a_deposit_or_rent_stays(self):
        assert stays({}, "rent", min_deposit=200_000_000, max_rent=10_000_000)

    def test_a_free_deposit_is_a_value_and_below_a_minimum(self):
        """«ودیعه: مجانی» parses as 0. It is an answer, and 0 is below 200M —
        the pre-check dropped it, the loop let it through, and it was saved
        with no number because the reveal had been skipped."""
        assert goes({"deposit": 0}, "rent", min_deposit=200_000_000)

    def test_a_free_deposit_is_not_above_a_maximum(self):
        assert stays({"deposit": 0}, "rent", max_deposit=200_000_000)

    def test_a_full_mortgage_has_no_rent_and_is_below_a_minimum_rent(self):
        assert goes({"deposit": 900_000_000, "rent_price": 0}, "rent", min_rent=3_000_000)
        assert stays({"deposit": 900_000_000, "rent_price": 0}, "rent", max_rent=3_000_000)

    def test_a_rental_is_not_judged_on_a_sale_price(self):
        assert stays({"deposit": 50_000_000, "total_price": 1}, "rent",
                     min_price=5_000_000_000)

    def test_a_sale_is_not_judged_on_a_deposit(self):
        assert stays({"deposit": 50_000_000}, "buy", min_deposit=200_000_000)


class TestListingTypeRouting:
    """Which bands apply is the category's business, not the ad's."""

    @pytest.mark.parametrize("kind", ["unknown", "service", "sell", None, ""])
    def test_a_category_that_is_neither_buy_nor_rent_gets_no_price_bands(self, kind):
        assert stays({"total_price": 1, "deposit": 1, "rent_price": 1, "price_per_meter": 1},
                     kind, min_price=5_000_000_000, min_deposit=200_000_000,
                     min_rent=5_000_000, min_price_per_meter=40_000_000)

    def test_the_other_filters_still_apply_to_them(self):
        assert goes({"area": 40}, "unknown", min_area=80)


class TestArea:
    def test_inside_the_band_stays(self):
        assert stays({"area": 90}, min_area=80, max_area=100)

    def test_too_small_goes(self):
        assert why({"area": 60}, min_area=80) == "area 60 < min 80"

    def test_too_big_goes(self):
        assert why({"area": 300}, max_area=150) == "area 300 > max 150"

    def test_an_ad_that_does_not_state_an_area_stays(self):
        assert stays({}, min_area=80, max_area=100)


class TestRooms:
    def test_inside_the_band_stays(self):
        assert stays({"rooms": 2}, min_rooms=2, max_rooms=3)

    def test_too_few_goes(self):
        assert why({"rooms": 1}, min_rooms=2) == "rooms 1 < min 2"

    def test_too_many_goes(self):
        assert why({"rooms": 5}, max_rooms=3) == "rooms 5 > max 3"

    def test_no_rooms_at_all_is_a_value_and_below_a_minimum(self):
        """«بدون اتاق» is 0, and 0 is falsy — it must still be compared."""
        assert goes({"rooms": 0}, min_rooms=1)

    def test_no_rooms_at_all_is_not_above_a_maximum_of_two(self):
        assert stays({"rooms": 0}, max_rooms=2)

    def test_a_maximum_of_none_at_all_is_a_real_bound(self):
        """max_rooms=0 means «بدون اتاق only» — not «no limit»."""
        assert goes({"rooms": 2}, max_rooms=0)
        assert stays({"rooms": 0}, max_rooms=0)

    def test_an_ad_that_does_not_state_rooms_stays(self):
        assert stays({}, min_rooms=2, max_rooms=3)
        assert stays({"rooms": None}, min_rooms=2)


AMENITIES = ["has_elevator", "has_parking", "has_storage", "has_balcony"]


class TestAmenities:
    @pytest.mark.parametrize("key", AMENITIES)
    def test_required_and_present_stays(self, key):
        assert stays({key: True}, **{key: True})

    @pytest.mark.parametrize("key", AMENITIES)
    def test_required_and_absent_goes(self, key):
        assert why({key: False}, **{key: True}) == f"{key} required but not present"

    @pytest.mark.parametrize("key", AMENITIES)
    def test_required_and_never_mentioned_goes(self, key):
        """A page that says nothing about parking is not offering any."""
        assert goes({}, **{key: True})

    @pytest.mark.parametrize("key", AMENITIES)
    def test_refused_and_absent_stays(self, key):
        assert stays({key: False}, **{key: False})

    @pytest.mark.parametrize("key", AMENITIES)
    def test_refused_and_never_mentioned_stays(self, key):
        assert stays({}, **{key: False})

    @pytest.mark.parametrize("key", AMENITIES)
    def test_refused_and_present_goes(self, key):
        assert why({key: True}, **{key: False}) == f"{key} must be absent"

    @pytest.mark.parametrize("key", AMENITIES)
    def test_not_asked_about_is_not_checked(self, key):
        assert stays({key: False})

    def test_each_one_is_judged_alone(self):
        detail = {"has_elevator": True, "has_parking": True, "has_storage": False}
        assert stays(detail, has_elevator=True, has_parking=True)
        assert goes(detail, has_elevator=True, has_parking=True, has_storage=True)


class TestPhotos:
    def test_an_ad_with_a_photo_stays_when_photos_are_required(self):
        assert stays({"has_images": True}, has_images=True)

    def test_an_ad_with_photos_but_no_flag_stays(self):
        """The loop has always read the list when the flag was not set."""
        assert stays({"images": ["a.jpg"]}, has_images=True)

    def test_an_ad_without_one_goes_when_photos_are_required(self):
        assert why({}, has_images=True) == "has_images required but not present"

    def test_an_ad_with_one_goes_when_photos_are_refused(self):
        assert why({"has_images": True}, has_images=False) == "has_images must be absent"


class TestAdvertiserType:
    def test_a_personal_ad_stays_in_a_personal_run(self):
        assert stays({"advertiser_type": "personal"}, advertiser_type="personal")

    def test_an_agency_ad_goes_from_a_personal_run(self):
        assert why({"advertiser_type": "agency"}, advertiser_type="personal") == \
            "advertiser_type agency != personal"

    def test_an_ad_whose_type_could_not_be_read_goes(self):
        """Letting it through is what put agency ads in a «شخصی» run."""
        assert why({}, advertiser_type="personal") == \
            "advertiser_type unknown; personal filter active"

    def test_an_agency_run_keeps_agencies_and_drops_owners(self):
        assert stays({"advertiser_type": "agency"}, advertiser_type="agency")
        assert goes({"advertiser_type": "personal"}, advertiser_type="agency")

    def test_no_filter_means_the_type_does_not_matter(self):
        assert stays({"advertiser_type": "agency"})
        assert stays({})

    def test_an_agency_posing_as_personal_is_kept_here(self):
        """Divar said «شخصی»; the ad's words say agency. That is labelled, not
        dropped — the decision is the label's, in advertiser_signals."""
        assert stays({"advertiser_type": "personal", "agency_suspected": True},
                     advertiser_type="personal")


class TestTheOrderTheReasonsComeIn:
    """The bucket a dropped listing is tallied under is its FIRST reason."""

    def test_price_before_area(self):
        assert why({"total_price": 1_000_000_000, "area": 10},
                   min_price=4_000_000_000, min_area=80).startswith("price ")

    def test_area_before_rooms(self):
        assert why({"area": 10, "rooms": 0}, min_area=80, min_rooms=2).startswith("area ")

    def test_rooms_before_amenities(self):
        assert why({"rooms": 0}, min_rooms=2, has_parking=True).startswith("rooms ")

    def test_amenities_before_the_advertiser(self):
        assert why({"advertiser_type": "agency"}, has_parking=True,
                   advertiser_type="personal").startswith("has_parking ")


class TestEveryReasonCanBeNamed:
    """The tally prints each bucket through _FILTER_LABELS_FA. A reason whose
    first word is missing there prints an English field name at somebody who
    set «نوع آگهی‌دهنده»."""

    CASES = [
        ({"total_price": 1_000_000_000}, "buy", {"min_price": 4_000_000_000}),
        ({"price_per_meter": 1_000_000}, "buy", {"min_price_per_meter": 4_000_000}),
        ({"deposit": 1}, "rent", {"min_deposit": 200_000_000}),
        ({"rent_price": 1}, "rent", {"min_rent": 5_000_000}),
        ({"area": 10}, "buy", {"min_area": 80}),
        ({"rooms": 0}, "buy", {"min_rooms": 1}),
        ({}, "buy", {"has_images": True}),
        ({}, "buy", {"has_elevator": True}),
        ({}, "buy", {"has_parking": True}),
        ({}, "buy", {"has_storage": True}),
        ({}, "buy", {"has_balcony": True}),
        ({}, "buy", {"advertiser_type": "personal"}),
        ({"advertiser_type": "agency"}, "buy", {"advertiser_type": "personal"}),
    ]

    @pytest.mark.parametrize("detail,kind,filters", CASES)
    def test_the_bucket_has_a_persian_name(self, detail, kind, filters):
        reason = skip(detail, kind, filters)
        assert reason, "the case was meant to be dropped"
        assert reason.split()[0] in DivarScraper._FILTER_LABELS_FA


class TestThePreContactCheckIsTheSameFunction:
    """pre_contact_skip is what stops «اطلاعات تماس» being clicked. It is the
    date check followed by the very function the loop calls."""

    @staticmethod
    def pre(detail, listing_type="buy", **filters):
        return DivarScraper.__new__(DivarScraper).pre_contact_skip(detail, listing_type, filters)

    CASES = [
        ({"total_price": 3_000_000_000}, "buy", {"min_price": 4_000_000_000}),
        ({"total_price": 5_000_000_000}, "buy", {"min_price": 4_000_000_000}),
        ({"deposit": 0}, "rent", {"min_deposit": 200_000_000}),
        ({"deposit": 0}, "rent", {"max_deposit": 200_000_000}),
        ({"rooms": 0}, "buy", {"max_rooms": 0}),
        ({"total_price": 1}, "unknown", {"min_price": 5_000_000_000}),
        ({}, "buy", {"has_parking": True}),
        ({"advertiser_type": "agency"}, "buy", {"advertiser_type": "personal"}),
        ({"images": ["a.jpg"]}, "buy", {"has_images": True}),
    ]

    @pytest.mark.parametrize("detail,kind,filters", CASES)
    def test_it_gives_the_loop_s_answer(self, detail, kind, filters):
        assert self.pre(detail, kind, **filters) == skip(detail, kind, filters)

    def test_a_free_deposit_is_dropped_before_the_reveal_and_by_the_loop_alike(self):
        assert self.pre({"deposit": 0}, "rent", min_deposit=200_000_000)
        assert goes({"deposit": 0}, "rent", min_deposit=200_000_000)

    def test_an_unknown_category_is_not_given_the_sale_bands(self):
        assert self.pre({"total_price": 1}, "unknown", min_price=5_000_000_000) is None

    def test_the_date_still_comes_first(self):
        from datetime import date, datetime
        got = self.pre({"posted_at": datetime(2026, 9, 20, 9, 0), "total_price": 1},
                       "buy", target_day=date(2026, 9, 26), min_price=5_000_000_000)
        assert got and "is before" in got
