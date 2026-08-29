"""
Deleting a Divar session has to delete both copies of it.

Every saved session lives twice: a row in `cookies`, and a
`cookies_<phone>.json` file on the data volume holding Divar's session
token in plain text. The panel's delete button dropped only the row, so a
session deleted for security reasons stayed readable on disk -- the file
being the half that actually leaks.

These read the source rather than importing it: app.api.routes pulls in the
whole scraper package, which needs OpenCV, and a guarantee about which lines
a handler contains should not depend on whether a vision library installed.
"""
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
AUTH_ROUTES = (ROOT / "app" / "api" / "routes" / "auth.py").read_text(encoding="utf-8")
SCRAPER_AUTH = (ROOT / "app" / "scraper" / "auth.py").read_text(encoding="utf-8")


def handler_source(src: str, decorator: str) -> str:
    """The body of one route handler, decorator to next decorator."""
    start = src.index(decorator)
    nxt = src.find("\n@router.", start + len(decorator))
    return src[start:nxt if nxt != -1 else len(src)]


DELETE_COOKIE = handler_source(AUTH_ROUTES, '@router.delete("/cookies/{cookie_id}")')


class TestDeleteRemovesBothCopies:
    def test_it_deletes_the_database_row(self):
        assert "db.delete(cookie)" in DELETE_COOKIE

    def test_it_deletes_the_file_on_disk(self):
        """The regression: dropping the row alone left the token readable."""
        assert "get_cookie_file_path" in DELETE_COOKIE, (
            "delete_cookie must locate the on-disk session file"
        )
        assert re.search(r"\.unlink\(\)|os\.remove\(", DELETE_COOKIE), (
            "delete_cookie must delete the on-disk session file, not just the row"
        )

    def test_a_missing_row_404s_before_touching_the_filesystem(self):
        assert DELETE_COOKIE.index("404") < DELETE_COOKIE.index("get_cookie_file_path")

    def test_a_failed_file_delete_is_reported_not_swallowed(self):
        """Someone deleting a session on purpose must not be told it worked
        while the token is still sitting on disk."""
        assert "file_removed" in DELETE_COOKIE

    def test_the_file_delete_cannot_abort_the_row_delete(self):
        """Order matters: an unwritable volume must not leave the row behind
        as well, or the panel shows a session nobody can remove."""
        assert "try:" in DELETE_COOKIE
        assert DELETE_COOKIE.index("except") < DELETE_COOKIE.index("db.delete(cookie)")


class TestLogoutPathKeepsRemovingTheFile:
    def test_invalidate_cookies_removes_the_file(self):
        i = SCRAPER_AUTH.index("async def invalidate_cookies")
        body = SCRAPER_AUTH[i:i + 1400]
        assert re.search(r"os\.remove\(|\.unlink\(", body)


class TestBothPathsAgreeOnTheFilename:
    """A second naming convention would silently orphan files."""

    def test_only_one_place_builds_the_path(self):
        assert SCRAPER_AUTH.count("def get_cookie_file_path") == 1

    def test_the_name_is_derived_from_the_phone_number(self):
        i = SCRAPER_AUTH.index("def get_cookie_file_path")
        assert 'f"cookies_{phone_number}.json"' in SCRAPER_AUTH[i:i + 300]

    def test_no_handler_hand_rolls_the_filename(self):
        """Mentioning the convention in a docstring is fine; rebuilding it is
        not -- a second f-string here drifts from the one in scraper/auth.py."""
        assert 'f"cookies_' not in DELETE_COOKIE
        assert "f'cookies_" not in DELETE_COOKIE


class TestFilenameSemantics:
    """What the convention has to guarantee, exercised for real."""

    @staticmethod
    def name_for(phone: str) -> str:
        return f"cookies_{phone}.json"

    def test_two_accounts_never_share_a_file(self):
        assert self.name_for("09111111111") != self.name_for("09122222222")

    def test_deleting_one_leaves_the_other(self, tmp_path):
        a = tmp_path / self.name_for("09111111111")
        b = tmp_path / self.name_for("09122222222")
        for f in (a, b):
            f.write_text(json.dumps({"cookies": [{"name": "token", "value": "s"}]}))
        a.unlink()
        assert not a.exists() and b.exists()

    def test_unlink_actually_removes_the_token(self, tmp_path):
        f = tmp_path / self.name_for("09123456789")
        f.write_text(json.dumps({"cookies": [{"name": "token", "value": "secret"}]}))
        f.unlink()
        assert not f.exists()
        assert "secret" not in "".join(p.read_text() for p in tmp_path.iterdir())
