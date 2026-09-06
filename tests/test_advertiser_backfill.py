"""
The listing that prompted this was scraped weeks ago.

The scraper labels what it saves from here on, but «املاک هستم» — filed by
Divar as شخصی — is already in the table, one of about twelve hundred. A
label that only applies to future rows leaves the panel quietly saying
nothing about the ones somebody is actually looking at.

The distinction that makes the backfill possible is NULL vs FALSE: «not
looked at» vs «looked at, private». A DEFAULT FALSE on the column would have
declared the whole archive private before anything read a word of it, and
there would then be no way to find the rows still owed a reading.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_abf.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = open(os.path.join(ROOT, "app/database.py"), encoding="utf-8").read()

import app.database as dbmod  # noqa: E402


def block(name, end):
    i = DB.index(f"async def {name}")
    return DB[i:DB.index(end, i)]


class TestTheColumnLeavesRoomForUnknown:
    def test_no_default_on_the_flag(self):
        b = block("_migrate_advertiser_signals", "_ADVERTISER_BACKFILL_BATCH")
        assert "ADD COLUMN IF NOT EXISTS agency_suspected BOOLEAN," in b
        assert "agency_suspected BOOLEAN DEFAULT" not in b

    def test_the_reason_is_written_down(self):
        b = block("_migrate_advertiser_signals", "_ADVERTISER_BACKFILL_BATCH")
        assert "NULL means" in b and "not looked at yet" in b


class TestTheBackfill:
    def test_it_exists_and_is_registered(self):
        assert hasattr(dbmod, "_backfill_advertiser_signals")
        assert "_backfill_advertiser_signals," in DB

    def test_it_runs_after_the_column_is_added(self):
        assert DB.index("_migrate_advertiser_signals,\n") < \
            DB.index("_backfill_advertiser_signals,\n")

    def test_it_only_looks_at_rows_never_read(self):
        b = block("_backfill_advertiser_signals", "\nasync def ")
        assert "WHERE agency_suspected IS NULL" in b

    def test_it_is_capped_per_boot(self):
        """A rollout must never wait on it."""
        b = block("_backfill_advertiser_signals", "\nasync def ")
        assert "LIMIT :n" in b
        assert dbmod._ADVERTISER_BACKFILL_BATCH > 0

    def test_it_writes_both_fields(self):
        b = block("_backfill_advertiser_signals", "\nasync def ")
        assert "SET agency_suspected = :s, agency_evidence = :e" in b

    def test_it_stores_false_rather_than_leaving_null(self):
        """Otherwise the same rows are re-read on every single boot."""
        b = block("_backfill_advertiser_signals", "\nasync def ")
        assert "looks, phrase = advertiser_signals.detect" in b
        assert '"s": looks' in b

    def test_it_uses_the_same_detector_the_scraper_does(self):
        """Two readings of the same words would drift apart."""
        b = block("_backfill_advertiser_signals", "\nasync def ")
        assert "from app.services import advertiser_signals" in b

    def test_it_touches_nothing_outside_the_database(self):
        """The image-hash backfill was kept out of boot because opening
        thousands of JPEGs is how a rollout times out. This one reads columns
        it already has."""
        b = block("_backfill_advertiser_signals", "\nasync def ")
        for forbidden in ("httpx", "requests", "open(", "Image", "page."):
            assert forbidden not in b, forbidden

    def test_an_empty_batch_returns_immediately(self):
        b = block("_backfill_advertiser_signals", "\nasync def ")
        assert "if not rows:" in b

    def test_it_cannot_stop_the_pod_starting(self):
        b = block("_backfill_advertiser_signals", "\nasync def ")
        assert "except Exception" in b

    def test_it_says_what_it_did(self):
        b = block("_backfill_advertiser_signals", "\nasync def ")
        assert "advertiser backfill:" in b


class TestItAgreesWithTheScraper:
    def test_the_same_text_gets_the_same_answer(self):
        from app.services import advertiser_signals as adv
        desc = "یه منزل شخصی طبقه دوم به متراژ۱۹۰متر\nاملاک هستم"
        looks, phrase = adv.detect(desc, None)
        annotated = adv.annotate({"description": desc})
        assert looks == annotated["agency_suspected"]
        assert phrase == annotated["agency_evidence"]
