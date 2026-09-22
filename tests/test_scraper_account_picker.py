"""
Choosing which Divar number a run uses.

«user can choose for each cookie i want use and i can change manauaili in
session.» The scrape form said so in a comment — «Auto-use the active Divar
session — no manual phone selection needed» — and that was true right up
until somebody wanted to rest one number and prove another.

Automatic stays the default. The picker is for the days it is not right.
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_JS = open(os.path.join(ROOT, "frontend/js/app.js"), encoding="utf-8").read()
INDEX = open(os.path.join(ROOT, "frontend/index.html"), encoding="utf-8").read()


def block(start, length=2200):
    i = APP_JS.index(start)
    return APP_JS[i:i + length]


class TestThePicker:
    def test_it_exists_on_the_form(self):
        assert 'id="scraper-account"' in INDEX

    def test_automatic_is_the_first_choice(self):
        i = INDEX.index('id="scraper-account"')
        assert 'value="">خودکار' in INDEX[i:i + 400]

    def test_it_is_filled_when_the_scraper_section_opens(self):
        i = APP_JS.index("case 'scraper':")
        assert "loadScraperAccounts()" in APP_JS[i:i + 200]

    def test_it_is_refilled_after_a_session_changes(self):
        """A number just added should be choosable without a reload."""
        assert APP_JS.count("loadScraperAccounts()") >= 2


class TestWhatTheChoiceIsMadeOn:
    def test_the_list_shows_how_spent_each_number_is(self):
        assert "افشا" in block("async function loadScraperAccounts()")

    def test_the_api_returns_that_number(self):
        import inspect
        from app.api.routes import auth as auth_routes
        src = inspect.getsource(auth_routes.list_cookies)
        assert '"reveals"' in src and '"challenged_at"' in src

    def test_the_least_spent_is_listed_first(self):
        b = block("async function loadScraperAccounts()")
        assert "(a.reveals || 0) - (b.reveals || 0)" in b

    def test_an_invalid_session_cannot_be_chosen(self):
        b = block("async function loadScraperAccounts()")
        assert "disabled" in b

    def test_a_recently_challenged_number_says_so(self):
        assert "اخیراً کد خواسته" in block("async function loadScraperAccounts()")


class TestItReachesTheRun:
    def test_a_picked_number_wins_over_the_active_session(self):
        b = block("async function executeBulkScraping(")
        assert "scraper-account" in b
        assert "picked ? { phone_number: picked } : await _getActiveSession()" in b

    def test_automatic_still_falls_back_to_what_it_did_before(self):
        b = block("async function executeBulkScraping(")
        assert "_getActiveSession()" in b

    def test_it_is_sent_as_divar_phone(self):
        b = block("async function executeBulkScraping(", 3000)
        assert "body.divar_phone = session.phone_number" in b


class TestTheRotationWarning:
    """Pinning a number and then having rotation swap it out defeats the
    choice, so the form says so rather than silently doing it."""

    def test_it_warns_when_rotation_is_still_on(self):
        b = block("function onScraperAccountChange()")
        assert "چرخش شماره" in b

    def test_it_does_not_warn_when_rotation_is_off(self):
        b = block("function onScraperAccountChange()")
        assert "rotate === 0" in b
        assert "فقط از همین شماره استفاده می‌شود" in b

    def test_it_says_nothing_at_all_on_automatic(self):
        b = block("function onScraperAccountChange()")
        assert "if (!sel?.value)" in b


class TestItCannotOfferSomebodyElsesNumber:
    def test_it_reads_the_owner_scoped_listing(self):
        assert "apiCall('/auth/cookies?mine=1')" in block("async function loadScraperAccounts()")


class TestThePanelWillBeReloaded:
    def test_the_cache_buster_moved(self):
        m = re.search(r"js/app\.js\?v=([0-9a-z]+)", INDEX)
        assert m and m.group(1) != "20260913a"
