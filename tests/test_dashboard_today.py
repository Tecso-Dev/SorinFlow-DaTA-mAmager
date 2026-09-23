"""
The dashboard counted things and asked no question anyone opens it with.

Four totals, two charts, a health card: how many listings exist, how many carry
a number. Useful once a week. The question somebody actually opens the panel
with in the morning is «what needs me today» — and every part of that answer
already existed, three clicks inside the CRM, and the assistant in Telegram
could say it while the panel could not.
"""
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_today.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
JS = (ROOT / "frontend/js/app.js").read_text(encoding="utf-8")
CSS = (ROOT / "frontend/css/style.css").read_text(encoding="utf-8")
STATS = (ROOT / "app/api/routes/stats.py").read_text(encoding="utf-8")

DASH = HTML.split('id="section-dashboard"')[1].split('id="section-properties"')[0]


class TestTheEndpoint:

    def test_it_reuses_the_queue_the_assistant_already_reads(self):
        """Two copies of «due» is two definitions of it, and they drift."""
        body = STATS.split('@router.get("/today")')[1].split("@router.")[0]
        assert "tool_queue_status" in body
        assert "select(" not in body, "a second copy of the same five queries"

    def test_it_needs_a_logged_in_user(self):
        body = STATS.split('@router.get("/today")')[1].split("@router.")[0]
        assert "Depends(get_current_user)" in body

    def test_the_counts_are_the_ones_the_office_asks_about(self):
        from app.ai import assistant
        import inspect
        src = inspect.getsource(assistant.tool_queue_status)
        for key in ("calls_due", "matches_waiting", "price_drops_new", "listings_today"):
            assert f'"{key}"' in src

    def test_today_starts_at_tehran_midnight(self):
        from app.ai import assistant
        import inspect
        src = inspect.getsource(assistant.tool_queue_status)
        assert "TEHRAN" in src and "hour=0" in src


class TestWhatTheScreenShows:

    def test_the_waiting_work_sits_above_the_totals(self):
        """A count of everything ever scraped is not a reason to open the panel."""
        assert DASH.index('id="dash-today"') < DASH.index('id="stat-total-properties"')

    def test_every_card_goes_somewhere(self):
        row = DASH.split('class="today-grid"')[1].split("</section>")[0]
        assert row.count("onclick=") == 4, "a number with nowhere to go is a decoration"
        assert "goCrm('calls', 'matches-card')" in row and "goCrm('calls', 'drops-card')" in row

    def test_nothing_waiting_reads_as_calm_not_as_three_zeros(self):
        put = JS.split("async function _loadToday()")[1].split("\n}")[0]
        assert "classList.toggle('idle'" in put
        assert "چیزی معطل نمانده" in put
        assert ".todo.idle" in CSS

    def test_a_failed_read_says_so_instead_of_showing_stale_numbers(self):
        body = JS.split("async function _loadToday()")[1].split("\n}")[0]
        assert "خوانده نشد" in body

    def test_the_dashboard_loads_it(self):
        body = JS.split("async function loadDashboard()")[1].split("\n}")[0]
        assert "_loadToday()" in body

    def test_the_health_card_is_a_strip_now(self):
        """Four badges, three of them always green, do not need a card of their
        own between the reader and the rest of the page."""
        assert 'class="health-strip"' in DASH
        assert 'class="row g-3" id="system-health"' not in DASH
        for probe in ("health-db", "health-redis", "health-scraper", "health-cookie"):
            assert f'id="{probe}"' in DASH, f"{probe} was dropped, not moved"

    def test_it_fits_a_phone(self):
        assert re.search(r"@media \(max-width: 480px\) \{[^}]*\.today-grid \{ grid-template-columns: 1fr", CSS, re.S)
