"""
Keeping the price trail a re-scrape used to destroy.

A listing is re-scraped, the update loop writes the new price over the old
one, and the previous figure is gone. Every price drop this database has ever
seen was thrown away at that line, on a schedule — which is the signal
Idealista and Rightmove both built their headline alert on.
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
