"""
Whose Divar number is whose.

«i am sobhan and my number is 09058432452 so i should use that number … and
i can acces only to my account numbers and not other account.»

The column is nullable only because a column cannot be added NOT NULL to a
table that already has rows. The backfill gives every existing row an owner
in two passes — users.divar_phone first, since that is already the statement
«this number is mine», then the super admin for whatever predates anybody
saying so. Leaving those NULL would make them invisible to every list, and a
scraper pool that silently empties is worse than an attribution somebody can
correct.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_own.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = open(os.path.join(ROOT, "app/database.py"), encoding="utf-8").read()

from app.models.cookie import Cookie  # noqa: E402


def block(name):
    i = DB.index(f"async def {name}")
    return DB[i:DB.index("\nasync def ", i + 10)]


class TestTheColumn:
    def test_it_exists(self):
        assert "owner_user_id" in Cookie.__table__.c

    def test_it_points_at_a_user(self):
        fks = list(Cookie.__table__.c.owner_user_id.foreign_keys)
        assert fks and "users.id" in str(fks[0].target_fullname)

    def test_it_is_indexed(self):
        """Every list and the scraper's pool filter on it."""
        assert Cookie.__table__.c.owner_user_id.index is True

    def test_it_is_serialised(self):
        assert "owner_user_id" in Cookie().to_dict()


class TestTheMigration:
    def test_it_is_registered_before_the_backfill(self):
        assert DB.index("_migrate_cookie_owner,\n") < DB.index("_backfill_cookie_owner,\n")

    def test_it_adds_the_column_if_absent(self):
        assert "ADD COLUMN IF NOT EXISTS owner_user_id INTEGER" in block("_migrate_cookie_owner")

    def test_it_indexes_it(self):
        assert "ix_cookies_owner_user_id" in block("_migrate_cookie_owner")

    def test_the_foreign_key_survives_a_deleted_user(self):
        """ON DELETE SET NULL, not CASCADE — removing a user must not delete
        the Divar sessions the business runs on."""
        b = block("_migrate_cookie_owner")
        assert "ON DELETE SET NULL" in b
        assert "CASCADE" not in b

    def test_it_cannot_stop_the_pod_starting(self):
        assert "except Exception" in block("_migrate_cookie_owner")


class TestTheBackfill:
    def test_divar_phone_is_the_first_pass(self):
        b = block("_backfill_cookie_owner")
        assert b.index("u.divar_phone") < b.index("role IN ('root', 'super_admin')")

    def test_it_compares_digits_not_strings(self):
        """«۰۹۰۵…», «0905-…» and «0905…» are one number written three ways."""
        b = block("_backfill_cookie_owner")
        assert "regexp_replace" in b

    def test_the_rest_go_to_the_super_admin(self):
        assert "role IN ('root', 'super_admin')" in block("_backfill_cookie_owner")

    def test_it_picks_one_super_admin_deterministically(self):
        b = block("_backfill_cookie_owner")
        assert "ORDER BY id ASC LIMIT 1" in b

    def test_both_passes_only_touch_unowned_rows(self):
        """A row assigned once must never be reassigned by a later boot.

        Counted per UPDATE rather than over the whole function — the
        docstring names the guard too, and a character count that measures
        prose is how this test lied the first time it was written."""
        b = block("_backfill_cookie_owner")
        updates = [chunk for chunk in b.split("UPDATE ")[1:]]
        assert len(updates) == 2, f"{len(updates)} UPDATE statements, expected 2"
        for u in updates:
            assert "owner_user_id IS NULL" in u

    def test_it_does_nothing_when_there_is_no_super_admin(self):
        """A fresh database seeds users after migrations run; assigning to a
        user that does not exist yet would violate the foreign key."""
        assert "EXISTS (SELECT 1 FROM users" in block("_backfill_cookie_owner")

    def test_it_cannot_stop_the_pod_starting(self):
        assert "except Exception" in block("_backfill_cookie_owner")
