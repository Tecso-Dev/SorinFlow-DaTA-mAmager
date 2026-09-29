"""
Deciding an ad's fate before asking Divar for its contact info.

Requesting contact info is the expensive step: it clicks «اطلاعات تماس»,
solves a captcha, and spends one of the account's requests — the budget Divar
counts before demanding a verification code. The scraper used to spend it on
every ad and then throw most of them away, which is why a filtered scrape was
slow and was asked to verify roughly every dozen listings.

This is the predicate that avoids that. It must never keep an ad the filters
would drop (that wastes the budget) and must never drop one they would keep
(that loses a listing) — so both directions are pinned here.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_pcf.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.scraper.divar_scraper import DivarScraper  # noqa: E402


# The real DivarScraper.pre_contact_skip. This file used to carry a copy of the
# function pasted in here, which meant every assertion below was about the copy
# and none of them could ever fail when the scraper changed.
def pre_contact_skip(detail, listing_type, f):
    return DivarScraper.__new__(DivarScraper).pre_contact_skip(detail, listing_type, f)


def asks(detail, listing_type="buy", **f):
    """True when the scraper would go on to request contact info."""
    return pre_contact_skip(detail, listing_type, f) is None


class TestNoFilters:
    def test_a_bare_job_asks_for_every_ad(self):
        assert asks({"total_price": 5_000_000_000}) is True

    def test_an_empty_ad_is_still_asked_for(self):
        assert asks({}) is True


class TestAdvertiserType:
    def test_a_personal_ad_survives_a_personal_filter(self):
        assert asks({"advertiser_type": "personal"}, advertiser_type="personal") is True

    def test_an_agency_ad_is_dropped_before_costing_a_request(self):
        """The dominant case: most Urmia ads are agency-posted."""
        assert asks({"advertiser_type": "agency"}, advertiser_type="personal") is False

    def test_an_unknown_type_is_dropped_before_costing_a_request(self):
        assert asks({}, advertiser_type="personal") is False

    def test_no_filter_means_the_type_does_not_matter(self):
        assert asks({"advertiser_type": "agency"}) is True

    def test_it_matches_the_loop_filter_exactly(self):
        """Both must agree, or the two would disagree about the same ad."""
        def loop_filter(actual, requested):
            if not requested:
                return False
            if not actual:
                return True
            return actual != requested
        for actual in ("personal", "agency", None):
            for requested in ("personal", "agency", None):
                mine = not asks({"advertiser_type": actual}, advertiser_type=requested)
                assert mine == loop_filter(actual, requested), (actual, requested)


class TestPriceBands:
    def test_a_sale_below_the_band(self):
        assert asks({"total_price": 2_000_000_000}, min_price=5_000_000_000) is False

    def test_a_sale_above_the_band(self):
        assert asks({"total_price": 20_000_000_000}, max_price=10_000_000_000) is False

    def test_a_sale_inside_the_band(self):
        assert asks({"total_price": 7_000_000_000},
                    min_price=5_000_000_000, max_price=10_000_000_000) is True

    def test_price_falls_back_when_total_price_is_missing(self):
        assert asks({"price": 2_000_000_000}, min_price=5_000_000_000) is False

    def test_an_unpriced_ad_is_not_dropped(self):
        """Missing is not the same as failing — the ad still gets its chance."""
        assert asks({}, min_price=5_000_000_000) is True

    def test_a_rental_is_judged_on_deposit_and_rent(self):
        assert asks({"deposit": 50_000_000}, "rent", min_deposit=200_000_000) is False
        assert asks({"rent_price": 9_000_000}, "rent", max_rent=5_000_000) is False
        assert asks({"deposit": 300_000_000, "rent_price": 3_000_000}, "rent",
                    min_deposit=200_000_000, max_rent=5_000_000) is True

    def test_a_sale_band_is_not_applied_to_a_rental(self):
        """A rental's deposit must not be compared against a sale price band."""
        assert asks({"deposit": 50_000_000}, "rent", min_price=5_000_000_000) is True


class TestAreaAndRooms:
    def test_too_small(self):
        assert asks({"area": 60}, min_area=100) is False

    def test_too_big(self):
        assert asks({"area": 300}, max_area=150) is False

    def test_inside(self):
        assert asks({"area": 120}, min_area=100, max_area=150) is True

    def test_rooms(self):
        assert asks({"rooms": 1}, min_rooms=2) is False
        assert asks({"rooms": 3}, min_rooms=2) is True

    def test_zero_rooms_is_a_value_not_a_blank(self):
        """«بدون اتاق» is 0, and 0 is falsy — it must still be compared."""
        assert asks({"rooms": 0}, min_rooms=1) is False
        assert asks({"rooms": 0}, max_rooms=2) is True

    def test_missing_measurements_do_not_drop_an_ad(self):
        assert asks({}, min_area=100, min_rooms=2) is True


class TestAmenities:
    def test_required_and_absent(self):
        assert asks({"has_parking": False}, has_parking=True) is False

    def test_required_and_present(self):
        assert asks({"has_parking": True}, has_parking=True) is True

    def test_not_requested_is_ignored(self):
        assert asks({"has_parking": False}) is True

    @pytest.mark.parametrize("key", ["has_elevator", "has_parking",
                                     "has_storage", "has_balcony", "has_images"])
    def test_each_one_is_checked(self, key):
        assert asks({key: False}, **{key: True}) is False
        assert asks({key: True}, **{key: True}) is True


class TestTheSavingItMakes:
    """The reason this exists: how many contact requests a filtered scrape
    spends, before and after."""

    POOL = (
        [{"advertiser_type": "agency"}] * 7 +          # dropped by the filter
        [{"advertiser_type": None}] * 2 +              # undetermined, also dropped
        [{"advertiser_type": "personal"}] * 3          # kept
    )

    def test_only_the_survivors_cost_a_request(self):
        asked = [ad for ad in self.POOL if asks(ad, advertiser_type="personal")]
        assert len(asked) == 3
        assert len(self.POOL) == 12

    def test_that_is_a_quarter_of_what_it_was(self):
        before = len(self.POOL)          # every ad used to cost a request
        after = sum(1 for ad in self.POOL if asks(ad, advertiser_type="personal"))
        assert after * 4 == before
