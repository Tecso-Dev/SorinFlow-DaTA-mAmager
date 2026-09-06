"""
The label has to survive the trip into the database.

Divar's declaration keeps its own column. These two sit beside it, because a
listing that says «املاک هستم» and arrives through a «شخصی» filter is a
disagreement, and overwriting Divar's answer would hide it rather than show
it.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_agf.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRAPER = open(os.path.join(ROOT, "app/scraper/divar_scraper.py"),
               encoding="utf-8-sig").read()
DB = open(os.path.join(ROOT, "app/database.py"), encoding="utf-8").read()

from app.models.property import Property  # noqa: E402


class TestTheColumns:
    def test_the_suspicion_is_stored(self):
        assert "agency_suspected" in Property.__table__.c

    def test_the_evidence_is_stored(self):
        """A flag nobody can check is a flag nobody will trust."""
        assert "agency_evidence" in Property.__table__.c

    def test_divars_own_answer_is_left_alone(self):
        assert "advertiser_type" in Property.__table__.c

    def test_the_suspicion_is_indexed(self):
        """The panel will filter on it."""
        assert Property.__table__.c.agency_suspected.index is True

    def test_it_defaults_to_not_suspected(self):
        assert Property.__table__.c.agency_suspected.default.arg is False


class TestItReachesTheApi:
    def test_both_fields_are_serialised(self):
        src = open(os.path.join(ROOT, "app/models/property.py"),
                   encoding="utf-8").read()
        assert '"agency_suspected": bool(self.agency_suspected)' in src
        assert '"agency_evidence": self.agency_evidence' in src

    def test_the_flag_is_a_real_boolean_not_none(self):
        """A null reads as «unknown» in JavaScript and renders as nothing."""
        p = Property()
        assert p.to_dict()["agency_suspected"] is False


class TestTheMigration:
    def test_it_exists(self):
        assert "async def _migrate_advertiser_signals" in DB

    def test_it_is_registered_in_the_run(self):
        assert "_migrate_advertiser_signals," in DB

    def test_it_adds_both_columns_if_absent(self):
        i = DB.index("async def _migrate_advertiser_signals")
        block = DB[i:DB.index("async def _migrate_filing", i)]
        assert "ADD COLUMN IF NOT EXISTS agency_suspected" in block
        assert "ADD COLUMN IF NOT EXISTS agency_evidence" in block

    def test_it_indexes_the_flag(self):
        i = DB.index("async def _migrate_advertiser_signals")
        block = DB[i:DB.index("async def _migrate_filing", i)]
        assert "CREATE INDEX IF NOT EXISTS ix_properties_agency_suspected" in block

    def test_it_cannot_stop_the_pod_starting(self):
        i = DB.index("async def _migrate_advertiser_signals")
        block = DB[i:DB.index("async def _migrate_filing", i)]
        assert "except Exception" in block

    def test_old_rows_land_unknown_rather_than_private(self):
        """NULL is «not looked at yet» and FALSE is «looked at, private». A
        DEFAULT FALSE would declare the whole archive private before anything
        read a word of it — and leave no way to find the rows still owed a
        reading, which is what the backfill selects on."""
        i = DB.index("async def _migrate_advertiser_signals")
        block = DB[i:DB.index("_ADVERTISER_BACKFILL_BATCH", i)]
        assert "agency_suspected BOOLEAN DEFAULT" not in block
        assert "not looked at yet" in block


class TestTheScraperLabelsEveryRecord:
    def _block(self):
        i = SCRAPER.index("advertiser_signals.annotate(property_data)")
        return SCRAPER[SCRAPER.rindex("# Who actually posted", 0, i):
                       SCRAPER.index("saved = await self.save_property", i)]

    def test_it_annotates(self):
        assert "advertiser_signals.annotate(property_data)" in SCRAPER

    def test_before_the_record_is_saved(self):
        assert SCRAPER.index("advertiser_signals.annotate(property_data)") < \
            SCRAPER.index("saved = await self.save_property")

    def test_the_disagreement_with_divar_is_logged(self):
        assert "disagrees_with_divar(property_data)" in self._block()

    def test_the_log_names_the_phrase(self):
        assert "agency_evidence" in self._block()
