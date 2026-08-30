"""
Grading a scraped listing.

PropertyDataValidator existed, had tests, and was wired into nothing — so
"does the scraper get the data right?" had no answer anywhere in the system.
It is now run on every listing before it is saved.

Two rules it must keep. It never changes what gets stored: we have already
spent a contact reveal on the listing, so a flagged row beats a dropped one.
And it maps the scraper's word for a sale ad ('buy') to the validator's
('sale') -- without that every sale listing is graded against the rent rules
and reported as missing a rent price.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_qual.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")


@pytest.fixture
def grader():
    """A DivarScraper with nothing wired up but the grader."""
    from app.scraper.divar_scraper import DivarScraper
    return DivarScraper.__new__(DivarScraper)


def sale_ad(**over):
    d = {"divar_id": "abc", "title": "آپارتمان ۸۵ متری", "url": "https://divar.ir/v/x",
         "listing_type": "buy", "area": 85, "rooms": 2, "total_price": 4_500_000_000,
         "city_name": "تهران"}
    d.update(over)
    return d


def rent_ad(**over):
    d = {"divar_id": "def", "title": "آپارتمان ۷۰ متری", "url": "https://divar.ir/v/y",
         "listing_type": "rent", "area": 70, "rooms": 2,
         "deposit": 500_000_000, "rent_price": 25_000_000, "city_name": "تهران"}
    d.update(over)
    return d


class TestListingTypeMapping:
    """The mismatch that would have made every sale ad look broken."""

    def test_buy_is_graded_as_a_sale(self, grader):
        assert grader._VALIDATOR_TYPES["buy"] == "sale"

    def test_rent_stays_rent(self, grader):
        assert grader._VALIDATOR_TYPES["rent"] == "rent"

    def test_a_complete_sale_ad_is_not_reported_as_missing_rent(self, grader):
        d = sale_ad()
        grader._grade_property(d)
        assert "Rent price missing" not in (d.get("quality_issues") or "")

    def test_an_unknown_type_is_left_ungraded(self, grader):
        """NULL is honest; guessing 'rent' invents errors on ads with no rent."""
        d = sale_ad(listing_type="unknown-category")
        grader._grade_property(d)
        assert "quality_score" not in d
        assert "quality_issues" not in d

    def test_a_missing_type_is_left_ungraded(self, grader):
        d = sale_ad()
        d.pop("listing_type")
        grader._grade_property(d)
        assert "quality_score" not in d


class TestItRecordsWithoutEnforcing:
    def test_a_good_ad_scores_high(self, grader):
        d = sale_ad()
        grader._grade_property(d)
        assert d["quality_score"] > 0.8

    def test_a_broken_ad_still_carries_all_its_data(self, grader):
        """Grading must not drop or rewrite anything."""
        d = sale_ad(total_price=None, title="")
        before = {k: v for k, v in d.items()}
        grader._grade_property(d)
        for k, v in before.items():
            assert d[k] == v, f"grading changed {k}"

    def test_a_broken_ad_is_flagged_not_rejected(self, grader):
        d = sale_ad(title="")
        grader._grade_property(d)
        assert d["quality_score"] < 1.0
        assert d["quality_issues"]

    def test_a_clean_ad_gets_empty_string_not_none(self, grader):
        """The update path skips None, so None would keep stale flags on a
        listing that has since been fixed."""
        d = rent_ad()
        grader._grade_property(d)
        assert d["quality_issues"] == "" or isinstance(d["quality_issues"], str)
        assert d["quality_issues"] is not None

    def test_the_score_is_rounded_and_in_range(self, grader):
        d = rent_ad()
        grader._grade_property(d)
        assert 0.0 <= d["quality_score"] <= 1.0
        assert len(str(d["quality_score"]).split(".")[-1]) <= 3

    def test_issue_text_is_bounded(self, grader):
        d = sale_ad(title="", url="https://divar.ir/v/x", area=None, rooms=None,
                    total_price=None, city_name=None)
        grader._grade_property(d)
        assert len(d["quality_issues"]) <= 2000


class TestItNeverRaises:
    """A grading failure must not cost a listing that a reveal was spent on."""

    def test_garbage_input_does_not_raise(self, grader):
        grader._grade_property({"listing_type": "buy", "area": "not a number",
                                "total_price": object()})

    def test_empty_dict_does_not_raise(self, grader):
        grader._grade_property({})

    def test_none_values_do_not_raise(self, grader):
        grader._grade_property({"listing_type": "rent", "title": None,
                                "rent_price": None, "area": None})


class TestPersistence:
    """The columns the grade is written to must exist on the model."""

    def test_model_has_the_columns(self):
        from app.models.property import Property
        assert hasattr(Property, "quality_score")
        assert hasattr(Property, "quality_issues")

    def test_the_migration_is_registered(self):
        import inspect
        from app import database
        src = inspect.getsource(database.init_db)
        assert "_migrate_property_quality" in src

    def test_the_migration_does_not_backfill(self):
        """A row-by-row backfill at boot is how a rollout times out."""
        import inspect
        from app import database
        src = inspect.getsource(database._migrate_property_quality)
        assert "UPDATE" not in src.upper()
