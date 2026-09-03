"""
Valuing a listing against its district, and the price trail behind it.

The idea comes from Zillow and Redfin. The part worth copying is not the model
— it is that they publish an error rate, because a valuation without a stated
confidence is a guess wearing a suit. So most of these tests are about the
refusals: too few comparables, no area, an unnamed district, a parsed price
that cannot be real. Getting those wrong does not produce a slightly worse
number, it produces a confident wrong one, which is the only kind that costs
somebody money.
"""
import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_val.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.services import valuation as val  # noqa: E402


class P:
    """A stand-in for a Property row."""
    def __init__(self, **kw):
        for k in ("price_per_meter", "total_price", "price", "area",
                  "built_area", "city_name", "district", "listing_type",
                  "price_history"):
            setattr(self, k, None)
        for k, v in kw.items():
            setattr(self, k, v)


# ── price per metre ─────────────────────────────────────────────────────────

class TestPricePerMeter:
    def test_it_uses_the_stored_figure(self):
        assert val.price_per_meter(P(price_per_meter=80_000_000)) == 80_000_000

    def test_it_derives_one_when_divar_did_not_supply_it(self):
        assert val.price_per_meter(P(total_price=4_000_000_000, area=50)) == 80_000_000

    def test_no_area_means_no_answer(self):
        """Inventing one from the total would put a 500m villa in the same
        bucket as a 50m flat."""
        assert val.price_per_meter(P(total_price=4_000_000_000)) is None

    def test_zero_area_does_not_divide_by_zero(self):
        assert val.price_per_meter(P(total_price=1_000_000_000, area=0)) is None

    def test_an_absurd_stored_figure_is_refused(self):
        """A total price parsed into the per-metre column would poison the
        median for the whole district."""
        assert val.price_per_meter(P(price_per_meter=9_000_000_000_000)) is None

    def test_an_absurdly_small_figure_is_refused(self):
        assert val.price_per_meter(P(price_per_meter=500)) is None

    def test_a_bad_stored_figure_falls_back_to_deriving(self):
        p = P(price_per_meter=12, total_price=4_000_000_000, area=50)
        assert val.price_per_meter(p) == 80_000_000


# ── what counts as comparable ───────────────────────────────────────────────

class TestBucketKey:
    def test_city_district_and_deal_type(self):
        p = P(city_name="ارومیه", district="والفجر", listing_type="buy")
        assert val.bucket_key(p) == ("ارومیه", "والفجر", "buy")

    def test_no_district_means_not_comparable(self):
        """Falling back to the city average would attach a confident number to
        exactly the listings we know least about."""
        assert val.bucket_key(P(city_name="ارومیه")) is None

    def test_no_city_means_not_comparable(self):
        assert val.bucket_key(P(district="والفجر")) is None

    def test_whitespace_is_not_a_district(self):
        assert val.bucket_key(P(city_name="ارومیه", district="   ")) is None

    def test_a_missing_deal_type_still_buckets(self):
        p = P(city_name="ارومیه", district="والفجر")
        assert val.bucket_key(p)[2] == "unknown"


# ── the benchmark ───────────────────────────────────────────────────────────

class TestBenchmark:
    def test_too_few_comparables_is_no_answer_at_all(self):
        """Not a cautious answer — no answer. A median of four is decided by
        two listings."""
        assert val.benchmark([80_000_000] * 4) is None

    def test_exactly_at_the_threshold_answers(self):
        b = val.benchmark([80_000_000] * val.MIN_COMPARABLES)
        assert b is not None and b["sample"] == val.MIN_COMPARABLES

    def test_it_is_a_median_not_a_mean(self):
        """One villa at forty billion in a street of apartments drags a mean
        far enough to make every neighbour look cheap."""
        values = [50_000_000] * 9 + [4_000_000_000]
        b = val.benchmark(values)
        assert b["median_ppm"] == 50_000_000

    def test_junk_values_are_dropped_before_the_median(self):
        b = val.benchmark([80_000_000] * 8 + [5, 9_999_999_999_999])
        assert b["sample"] == 8

    def test_a_thin_sample_is_labelled_thin(self):
        assert val.benchmark([80_000_000] * 10)["confidence"] == "thin"

    def test_a_large_sample_is_labelled_good(self):
        b = val.benchmark([80_000_000] * val.STRONG_COMPARABLES)
        assert b["confidence"] == "good"

    def test_it_reports_the_spread_not_just_the_middle(self):
        b = val.benchmark(list(range(50_000_000, 50_000_000 + 100 * 1_000_000, 1_000_000)))
        assert b["low"] < b["median_ppm"] < b["high"]


# ── the verdict ─────────────────────────────────────────────────────────────

class TestAssess:
    BENCH = {"median_ppm": 100_000_000, "sample": 40, "low": 80_000_000,
             "high": 120_000_000, "confidence": "good"}

    def test_a_cheap_listing_is_flagged_under(self):
        a = val.assess(P(price_per_meter=70_000_000), self.BENCH)
        assert a["verdict"] == "under" and a["delta_pct"] == -30.0

    def test_an_expensive_listing_is_flagged_over(self):
        a = val.assess(P(price_per_meter=140_000_000), self.BENCH)
        assert a["verdict"] == "over"

    def test_a_normal_listing_is_typical(self):
        a = val.assess(P(price_per_meter=103_000_000), self.BENCH)
        assert a["verdict"] == "typical"

    def test_just_inside_the_threshold_is_still_typical(self):
        """Flagging every property drowns the ones that matter."""
        ppm = int(100_000_000 * (1 - (val.NOTABLE_PCT - 1) / 100))
        assert val.assess(P(price_per_meter=ppm), self.BENCH)["verdict"] == "typical"

    def test_no_benchmark_is_unknown_not_typical(self):
        """«Priced normally» and «cannot say» are very different statements
        and only one of them is supported."""
        a = val.assess(P(price_per_meter=70_000_000), None)
        assert a["verdict"] == "unknown"
        assert a["reason"] == "not_enough_comparables"

    def test_no_price_per_meter_is_unknown(self):
        a = val.assess(P(), self.BENCH)
        assert a["verdict"] == "unknown"
        assert a["reason"] == "no_price_per_meter"

    def test_every_result_carries_a_verdict(self):
        for p, b in [(P(), None), (P(), self.BENCH),
                     (P(price_per_meter=70_000_000), None)]:
            assert "verdict" in val.assess(p, b)

    def test_the_sample_size_travels_with_the_claim(self):
        a = val.assess(P(price_per_meter=70_000_000), self.BENCH)
        assert a["sample"] == 40


# ── how it reads ────────────────────────────────────────────────────────────

class TestDescribe:
    def test_a_thin_sample_is_admitted_on_the_same_line(self):
        """A caveat one card away from the number it qualifies is a caveat
        nobody reads."""
        a = val.assess(P(price_per_meter=70_000_000),
                       {"median_ppm": 100_000_000, "sample": 9,
                        "low": 1, "high": 2, "confidence": "thin"})
        text = val.describe(a)
        assert "۹" in text or "9" in text
        assert "کمتر" in text

    def test_a_good_sample_does_not_nag(self):
        a = val.assess(P(price_per_meter=70_000_000),
                       {"median_ppm": 100_000_000, "sample": 60,
                        "low": 1, "high": 2, "confidence": "good"})
        assert "فقط" not in val.describe(a)

    def test_unknown_explains_which_kind_of_unknown(self):
        assert "متراژ" in val.describe(val.assess(P(), None))

    def test_over_reads_as_more_not_less(self):
        a = val.assess(P(price_per_meter=140_000_000),
                       {"median_ppm": 100_000_000, "sample": 60,
                        "low": 1, "high": 2, "confidence": "good"})
        assert "بیشتر" in val.describe(a)


# ── the price trail ─────────────────────────────────────────────────────────

class TestPriceMoves:
    def test_it_reads_a_recorded_move(self):
        p = P(price_history=[{"at": "2026-09-01T10:00:00",
                              "total_price": 900, "from": {"total_price": 1000}}])
        m = val.price_moves(p)
        assert len(m) == 1
        assert m[0]["direction"] == "down"
        assert m[0]["delta_pct"] == -10.0

    def test_newest_first(self):
        p = P(price_history=[
            {"at": "2026-01-01", "total_price": 900, "from": {"total_price": 1000}},
            {"at": "2026-06-01", "total_price": 800, "from": {"total_price": 900}}])
        assert val.price_moves(p)[0]["at"] == "2026-06-01"

    def test_an_empty_trail_is_an_empty_list(self):
        assert val.price_moves(P()) == []

    def test_a_malformed_entry_is_skipped_not_fatal(self):
        p = P(price_history=["nonsense", {"at": "x"}, None])
        assert val.price_moves(p) == []

    def test_a_rise_is_labelled_up(self):
        p = P(price_history=[{"at": "x", "total_price": 1200,
                              "from": {"total_price": 1000}}])
        assert val.price_moves(p)[0]["direction"] == "up"


class TestTheScraperRecordsBeforeItOverwrites:
    """The whole point: the capture has to run before the setattr loop, or
    there is nothing left to record."""

    def test_the_capture_precedes_the_overwrite(self):
        import inspect
        from app.scraper.divar_scraper import DivarScraper
        src = inspect.getsource(DivarScraper.save_property)
        assert src.index("_record_price_move") < src.index("setattr(existing, key, value)")

    def test_it_only_records_a_real_change(self):
        """A row on every scrape would add a thousand identical entries a day
        and bury the handful that mean something."""
        import inspect
        from app.scraper.divar_scraper import DivarScraper
        src = inspect.getsource(DivarScraper._record_price_move)
        assert "if not moved:" in src

    def test_the_trail_is_bounded(self):
        from app.scraper.divar_scraper import DivarScraper
        assert DivarScraper.PRICE_TRAIL_MAX > 0
        import inspect
        src = inspect.getsource(DivarScraper._record_price_move)
        assert "PRICE_TRAIL_MAX" in src

    def test_it_cannot_cost_us_the_listing(self):
        import inspect
        from app.scraper.divar_scraper import DivarScraper
        assert "except Exception" in inspect.getsource(DivarScraper._record_price_move)

    def test_a_deposit_shuffle_is_not_reported_as_a_price_cut(self):
        """previous_price drives the «قیمت کم شد» signal; a rental deposit
        moving while the rent holds is in the trail but is not that."""
        import inspect
        from app.scraper.divar_scraper import DivarScraper
        src = inspect.getsource(DivarScraper._record_price_move)
        i = src.index("previous_price")
        assert "headline" in src[max(0, i - 300):i + 100]

    def test_the_columns_exist_on_the_model(self):
        from app.models.property import Property
        for c in ("price_history", "previous_price", "price_changed_at"):
            assert hasattr(Property, c)

    def test_the_migration_is_registered(self):
        import inspect
        from app import database
        assert "_migrate_price_history" in inspect.getsource(database.init_db)
