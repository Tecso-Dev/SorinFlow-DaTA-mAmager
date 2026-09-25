"""
«It errors on some phones.»

Three answers, each guarded here: the panel no longer depends on CDNs that
Iranian mobile carriers block (roadmap #5); errors that happen in a user's
browser are reported home with the browser's name, including the one where
app.js does not parse at all; and the phone CSS measured at 375/320px on
2026-09-18 stays in place.
"""
import os
import re
import sys
import asyncio
import json
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_phones.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

ROOT = Path(__file__).resolve().parent.parent
INDEX = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
CSS = (ROOT / "frontend/css/style.css").read_text(encoding="utf-8")


class TestNoCdn:

    def test_the_panel_loads_nothing_from_a_cdn(self):
        """jsdelivr and code.jquery.com are throttled or blocked on some
        Iranian mobile carriers; a panel that needs them is a blank page
        there — with «bootstrap is not defined» in a console nobody sees."""
        hosts = re.findall(r'(?:src|href)="(https?://[^"/]+)', INDEX)
        assert not any("jsdelivr" in h or "jquery.com" in h or "unpkg" in h or "cdnjs" in h for h in hosts), hosts

    def test_every_local_asset_exists_and_is_the_real_thing(self):
        for rel in re.findall(r'(?:src|href)="(vendor/[^"]+)"', INDEX):
            f = ROOT / "frontend" / rel
            assert f.exists(), rel
            assert f.stat().st_size > 5000, f"{rel} is suspiciously small"
        # the icon font the CSS points at is shipped too
        assert (ROOT / "frontend/vendor/bootstrap-icons/fonts/bootstrap-icons.woff2").exists()

    def test_the_versions_are_the_ones_the_page_used_to_load(self):
        v = ROOT / "frontend/vendor"
        assert "Bootstrap v5.3.2" in (v / "bootstrap.bundle.min.js").read_text(encoding="utf-8")[:200]
        assert "v3.7.1" in (v / "jquery-3.7.1.min.js").read_text(encoding="utf-8")[:100]
        assert "4.4.1" in (v / "chart.umd.min.js").read_text(encoding="utf-8")[:300]

    def test_the_image_ships_the_vendor_directory(self):
        ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
        # line by line: frontend-next/node_modules/ is rightly ignored and is
        # not the old panel's frontend/ (a plain substring check refused it)
        rules = [r.strip().rstrip("/") for r in ignore.splitlines()
                 if r.strip() and not r.lstrip().startswith("#")]
        for rule in rules:
            assert "vendor" not in rule, f".dockerignore drops the vendored files: {rule}"
            assert rule not in ("frontend", "frontend/*", "frontend/**") and not rule.startswith("frontend/"), \
                f".dockerignore drops the old panel: {rule}"


class TestErrorsComeHome:

    def test_the_hook_is_es5_and_first(self):
        """It must run on the browser that cannot parse app.js, and before
        the parse failure happens."""
        first_script = INDEX.index("<script")
        hook = INDEX[first_script:INDEX.index("</script>", first_script)]
        assert "window.onerror" in hook, "the reporter is not the first script on the page"
        for modern in ("=>", "const ", "let ", "`", "?.", "async "):
            assert modern not in hook, f"ES5 only: found {modern!r}"
        assert "/api/public/client-error" in hook and "sendBeacon" in hook
        assert "sent >= 5" in hook, "a broken page must not flood the server"

    def test_the_endpoint_is_public_and_the_listing_is_not(self):
        main = (ROOT / "app/main.py").read_text(encoding="utf-8")
        assert '"/api/public/client-error"' in main[main.index("public_paths = {"):main.index("is_dashboard =")]
        assert '@app.post("/api/public/client-error", status_code=204)' in main
        mon = (ROOT / "app/api/routes/monitoring.py").read_text(encoding="utf-8")
        assert '@router.get("/client-errors")' in mon

    def test_reports_are_shaped_cut_and_named(self):
        from app.services import client_errors as ce
        item = ce.shape({"message": "x" * 900, "line": "12", "col": "oops", "ua": "Mozilla/5.0 (Linux; Android 9; SM-J600F) AppleWebKit/537.36 Chrome/80.0.3987.99 Mobile Safari/537.36", "evil": "dropped"})
        assert len(item["message"]) == 500 and item["line"] == 12 and item["col"] == 0
        assert "evil" not in item and item["received_at"]
        assert ce.browser_of(item["ua"]) == "Chrome 80 · Android"
        assert ce.browser_of("Mozilla/5.0 (iPhone; CPU iPhone OS 15_6 like Mac OS X) AppleWebKit/605.1.15 Version/15.6 Mobile/15E148 Safari/604.1") == "Safari 15 · iPhone"
        assert ce.browser_of("Mozilla/5.0 (Linux; Android 13; SAMSUNG SM-A525F) AppleWebKit/537.36 SamsungBrowser/23.0 Chrome/115 Mobile Safari/537.36") == "Samsung 23 · Android"

    def test_a_flooding_address_is_cut_off_and_the_list_is_bounded(self, monkeypatch):
        import fakeredis.aioredis
        from app.services import client_errors as ce
        fake = fakeredis.aioredis.FakeRedis(decode_responses=True)

        async def _r():
            return fake
        monkeypatch.setattr(ce, "get_redis", _r)

        async def go():
            ok = [await ce.record({"message": f"e{i}", "ua": "x"}, "1.2.3.4") for i in range(25)]
            others = await ce.record({"message": "fine", "ua": "y"}, "5.6.7.8")
            rows = await ce.recent(100)
            return ok, others, rows
        ok, others, rows = asyncio.run(go())
        assert ok.count(True) == ce.PER_MINUTE and ok.count(False) == 5
        assert others is True
        assert len(rows) == ce.PER_MINUTE + 1 and rows[0]["message"] == "fine"
        assert all("browser" in r for r in rows)

    def test_the_monitoring_page_shows_them(self):
        assert 'id="mon-client-errors-card"' in INDEX
        js = (ROOT / "frontend/js/app.js").read_text(encoding="utf-8")
        assert "loadClientErrors();" in js.split("case 'monitoring':")[1][:120]


class TestPhones:
    """Measured at 375px and 320px on 2026-09-18: nothing overflowed, but
    the page was unusable on a phone for three other reasons."""

    def _phone(self):
        return CSS[CSS.index("/* ── Phones"):]

    def test_inputs_do_not_make_ios_zoom(self):
        blk = self._phone()
        assert "font-size: 16px !important" in blk
        assert ".form-control-sm, .form-select-sm" in blk

    def test_buttons_are_thumb_sized(self):
        assert "min-height: 42px" in self._phone()

    def test_the_crm_tabs_scroll_instead_of_stacking(self):
        blk = self._phone()
        assert "#crm-main-tabs {" in blk and "overflow-x: auto" in blk and "flex-wrap: nowrap !important" in blk

    def test_the_jobs_header_wraps(self):
        i = INDEX.index('id="jobs-filter-category"')
        opener = INDEX[INDEX.rindex("<div", 0, i):i]
        assert "flex-wrap" in opener
        assert "max-width:100%" in INDEX[i:i + 200]

    def test_charts_shrink_with_the_card(self):
        assert ".card canvas { max-width: 100% !important" in self._phone()

    def test_the_call_list_is_phone_first(self):
        blk = self._phone()
        assert ".cq-phone { display: flex" in blk and ".cq-actions .btn { flex: 1 1 46% }" in blk
