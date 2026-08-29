"""
حالت تعمیر — closing the site while it is updated.

Two things must hold or the feature turns into an outage: /health has to stay
open, because Kubernetes reads it and would kill the pod it thinks is unhealthy,
and the way back in — login, the status endpoint, the bypass link — must never
be behind the door it is meant to open.
"""
import pytest

from app.services.maintenance import (
    is_open_path, OPEN_PREFIXES, DEFAULT_MESSAGE, BYPASS_COOKIE,
    KEY_ENABLED, KEY_MESSAGE, KEY_BYPASS,
)


class TestOpenPaths:
    @pytest.mark.parametrize("path", [
        "/health",
        "/api/users/token",
        "/api/users/token/verify-totp",
        "/api/maintenance",
        "/maintenance-access",
        "/favicon.svg",
        "/favicon.ico",
    ])
    def test_stays_reachable_while_closed(self, path):
        assert is_open_path(path) is True

    def test_health_is_open(self):
        """Kubernetes reads it. Closing it turns a maintenance notice into a
        pod that keeps getting killed."""
        assert is_open_path("/health") is True

    def test_the_bypass_link_is_not_behind_its_own_door(self):
        assert is_open_path("/maintenance-access") is True

    def test_the_off_switch_is_always_reachable(self):
        """POST /api/maintenance is still super_admin-only, but it must never
        be unreachable, or the site could not be reopened from the panel."""
        assert is_open_path("/api/maintenance") is True

    @pytest.mark.parametrize("path", [
        "/", "/dashboard", "/dashboard/", "/dashboard/js/app.js",
        "/dashboard/css/style.css", "/api/crm/leads", "/api/scraper/start",
        "/api/properties", "/images/1/a.jpg",
    ])
    def test_everything_else_closes(self, path):
        assert is_open_path(path) is False

    def test_no_prefix_opens_the_whole_api(self):
        assert "/api" not in OPEN_PREFIXES
        assert "/" not in OPEN_PREFIXES

    def test_a_lookalike_path_does_not_slip_through(self):
        """Prefix matching must not be widened by accident."""
        assert is_open_path("/api/maintenance-secret-backdoor") is True   # same prefix
        assert is_open_path("/healthz-fake") is True                       # same prefix
        # …but nothing outside the listed prefixes
        assert is_open_path("/api/scraper/health") is False


class TestKeysAndDefaults:
    def test_message_default_is_the_requested_wording(self):
        assert DEFAULT_MESSAGE == "سایت در حال بروزرسانی می‌باشد"

    def test_settings_keys_are_distinct(self):
        assert len({KEY_ENABLED, KEY_MESSAGE, KEY_BYPASS}) == 3

    def test_cookie_name_is_namespaced(self):
        assert BYPASS_COOKIE.startswith("sf_")


class TestAccessDecision:
    """Mirror of _maintenance_allows: the three ways through a closed site."""

    @staticmethod
    def allows(path, *, enabled, cookie=None, bypass=None, role=None):
        if is_open_path(path):
            return True
        if not enabled:
            return True
        if bypass and cookie == bypass:
            return True
        return role == "super_admin"

    def test_open_site_lets_everyone_through(self):
        assert self.allows("/dashboard/", enabled=False) is True

    def test_closed_site_blocks_a_visitor(self):
        assert self.allows("/dashboard/", enabled=True) is False

    def test_closed_site_blocks_a_normal_user(self):
        assert self.allows("/api/crm/leads", enabled=True, role="user") is False

    def test_closed_site_blocks_an_admin_that_is_not_super(self):
        assert self.allows("/api/crm/leads", enabled=True, role="admin") is False

    def test_super_admin_gets_through(self):
        assert self.allows("/api/crm/leads", enabled=True, role="super_admin") is True

    def test_the_bypass_cookie_gets_through(self):
        assert self.allows("/dashboard/", enabled=True,
                           cookie="abc", bypass="abc") is True

    def test_a_stale_bypass_cookie_does_not(self):
        """Each closure mints a new token, so last month's link is dead."""
        assert self.allows("/dashboard/", enabled=True,
                           cookie="old", bypass="new") is False

    def test_an_empty_bypass_never_matches(self):
        assert self.allows("/dashboard/", enabled=True, cookie="", bypass=None) is False

    def test_health_survives_a_closed_site(self):
        assert self.allows("/health", enabled=True) is True


# ── the closed-site page ─────────────────────────────────────────────────────

class TestMaintenancePage:
    """The gate function and the page it renders.

    _maintenance_allows was deleted once while the page template around it was
    being rewritten, and nothing failed: the middleware catches everything and
    fails open by design, so a closed site quietly served every request instead.
    That is the right failure mode and the reason it needs its own test.
    """

    @pytest.mark.parametrize("name", [
        "_maintenance_allows",       # decides who gets through
        "render_maintenance_page",   # builds the page
    ])
    def test_the_closed_site_path_is_intact(self, name):
        """Every piece the closed-site path needs.

        Named individually because the template is a huge string literal and
        rewriting it has twice now swallowed the function next to it — once
        _maintenance_allows (which fails open, so nothing broke visibly) and
        once render_maintenance_page (which 500s every request). A test that
        only knew about the first would have passed through the second.
        """
        import app.main as m
        assert hasattr(m, name), (
            f"{name} is missing — the closed-site path cannot work without it")

    def test_message_is_escaped_into_the_page(self):
        import app.main as m
        html = m.render_maintenance_page(message='<img src=x onerror=alert(1)>')
        assert "<img src=x" not in html
        assert "&lt;img" in html

    def test_contact_details_cannot_break_out_of_the_script(self):
        """Phone and email are operator-typed, so they are untrusted like any
        other input — they go in as JSON the script reads, never as markup."""
        import app.main as m
        from app.services.maintenance import State
        st = State(enabled=True, message="x",
                   phone='</script><script>alert(1)</script>',
                   email='a@b.com')
        html = m.render_maintenance_page(st)
        assert "</script><script>alert(1)" not in html

    def test_countdown_is_rendered_when_a_deadline_is_set(self):
        import app.main as m
        from app.services.maintenance import State
        from datetime import datetime, timedelta, timezone
        until = (datetime.now(timezone.utc) + timedelta(hours=72)).isoformat()
        html = m.render_maintenance_page(State(enabled=True, message="x", until=until))
        assert '"seconds_left":' in html.replace(" ", "")
        # three days, allowing for the seconds spent getting here
        import re
        secs = int(re.search(r'"seconds_left":\s*(\d+)', html).group(1))
        assert 71 * 3600 < secs <= 72 * 3600

    def test_no_deadline_means_no_countdown(self):
        import app.main as m
        from app.services.maintenance import State
        html = m.render_maintenance_page(State(enabled=True, message="x"))
        assert '"seconds_left": null' in html or '"seconds_left":null' in html

    @pytest.mark.parametrize("render,arg", [
        ("render_not_found", "/nope"),
        ("render_server_error", "abc123"),
    ])
    def test_no_public_page_makes_an_external_request(self, render, arg):
        """Two of these three exist because something is already broken, so a
        dependency that must load before the page renders is a way for the error
        page to fail too."""
        import re
        from app import error_pages
        html = getattr(error_pages, render)(arg)
        found = re.findall(r'(?:src|href)="(https?://[^"]+)"', html)
        assert not found, f"{render} loads an external resource: {found}"

    def test_the_maintenance_page_makes_no_external_request(self):
        import re
        import app.main as m
        html = m.render_maintenance_page(message="x")
        found = re.findall(r'(?:src|href)="(https?://[^"]+)"', html)
        assert not found, f"closed-site page loads an external resource: {found}"


class TestMaintenanceStateObject:
    def test_state_still_unpacks_as_the_old_three_tuple(self):
        """Existing callers do `enabled, message, bypass = ...`."""
        from app.services.maintenance import State
        enabled, message, bypass = State(enabled=True, message="m", bypass="b")
        assert (enabled, message, bypass) == (True, "m", "b")

    def test_seconds_left_never_goes_negative(self):
        from app.services.maintenance import State
        from datetime import datetime, timedelta, timezone
        past = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        assert State(until=past).seconds_left == 0

    def test_a_malformed_deadline_is_ignored_rather_than_raising(self):
        from app.services.maintenance import State
        assert State(until="not-a-date").seconds_left is None


class TestErrorPages:
    """404 and 500 as pages, not raw JSON.

    A visitor who mistyped a URL used to get {"detail":"Resource not found"} on
    a white page. API callers still need JSON, so the handlers negotiate.
    """

    def test_a_browser_gets_html_for_404(self):
        from fastapi.testclient import TestClient
        import app.main as m
        with TestClient(m.app, raise_server_exceptions=False) as c:
            r = c.get("/definitely-not-a-page", headers={"Accept": "text/html"})
        assert r.status_code == 404
        assert "text/html" in r.headers["content-type"]
        assert "۴۰۴" in r.text

    def test_an_api_caller_still_gets_json_for_404(self):
        """The dashboard and every script call /api — HTML there would break
        them, whatever the Accept header says."""
        from fastapi.testclient import TestClient
        import app.main as m
        with TestClient(m.app, raise_server_exceptions=False) as c:
            r = c.get("/api/not-a-route", headers={"Accept": "text/html"})
        assert r.status_code == 404
        assert r.headers["content-type"].startswith("application/json")
        assert r.json()["detail"]

    def test_the_500_page_carries_a_reference(self):
        """The same id is in the server log, which turns "the site broke" into
        a line somebody can find."""
        from app import error_pages
        html = error_pages.render_server_error("deadbeef")
        assert "deadbeef" in html and "۵۰۰" in html

    def test_the_500_reference_is_escaped(self):
        from app import error_pages
        html = error_pages.render_server_error("<script>alert(1)</script>")
        assert "<script>alert(1)</script>" not in html

    @pytest.mark.parametrize("render,arg", [
        ("render_not_found", "/x"), ("render_server_error", "ref1"),
    ])
    def test_error_pages_use_the_landing_palette(self, render, arg):
        """Same tokens as frontend/landing.html, so the three read as one
        product rather than three separate accidents."""
        from app import error_pages
        html = getattr(error_pages, render)(arg)
        for token in ("#030305", "--grad", "Vazirmatn", "backdrop-filter"):
            assert token in html, f"{render} is missing the landing token {token}"

    def test_all_three_pages_are_rtl_and_persian(self):
        from app import error_pages
        import app.main as m
        for html in (error_pages.render_not_found("/x"),
                     error_pages.render_server_error("r"),
                     m.render_maintenance_page(message="تست")):
            assert 'dir="rtl"' in html and 'lang="fa"' in html
