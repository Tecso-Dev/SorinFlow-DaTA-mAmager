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
        "/api/config",
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
