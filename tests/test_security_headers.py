"""
Security headers (app/main.py's log_requests) and the CSP-Report-Only
endpoint (/api/public/csp-report).

Before this, the panel shipped no Content-Security-Policy at all and no HSTS
— a compromised third-party script (or a typo turning a self-hosted path
into an external one) had no ceiling and no way to be noticed from outside a
browser's own devtools.
"""
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_security_headers.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")


def _client():
    from starlette.testclient import TestClient
    import app.main as m
    return TestClient(m.app)


class TestHeadersOnAnyResponse:

    def test_the_usual_headers_are_present(self):
        r = _client().get("/health")
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["x-frame-options"] == "SAMEORIGIN"
        assert r.headers["referrer-policy"] == "strict-origin-when-cross-origin"

    def test_csp_report_only_is_present_and_reports_to_our_own_endpoint(self):
        r = _client().get("/health")
        csp = r.headers.get("content-security-policy-report-only")
        assert csp, "no Content-Security-Policy-Report-Only header at all"
        # Enforcing, not observing — must never be this header instead.
        assert "content-security-policy" not in {
            k.lower() for k in r.headers if k.lower() == "content-security-policy"
        }
        assert "report-uri /api/public/csp-report" in csp
        assert "default-src 'self'" in csp
        assert "object-src 'none'" in csp

    def test_hsts_is_absent_outside_production(self, monkeypatch):
        from app.config import get_settings
        settings = get_settings()
        monkeypatch.setattr(settings, "environment", "test")
        r = _client().get("/health")
        assert "strict-transport-security" not in r.headers

    def test_hsts_is_present_in_production(self, monkeypatch):
        from app.config import get_settings
        settings = get_settings()
        monkeypatch.setattr(settings, "environment", "production")
        r = _client().get("/health")
        assert r.headers["strict-transport-security"] == "max-age=31536000"


class TestCspReportEndpoint:

    def test_a_classic_report_uri_report_is_accepted(self):
        r = _client().post(
            "/api/public/csp-report",
            content=b'{"csp-report": {"document-uri": "https://sorinflow.com/dashboard/", '
                     b'"violated-directive": "script-src", "blocked-uri": "https://evil.example/x.js"}}',
            headers={"Content-Type": "application/csp-report"},
        )
        assert r.status_code == 204

    def test_the_new_panels_cookie_and_login_pass_the_api_key_gate(self, monkeypatch):
        """The new panel sends a session cookie and never the API key. With the
        key set (production), the gate answered every one of its requests —
        the login itself included — with 401 before any route saw it, while
        locally, with no key, everything worked."""
        from app.config import get_settings
        monkeypatch.setattr(get_settings(), "api_key", "a-key-the-browser-never-sends")
        gate = "Invalid or missing API key"
        c = _client()
        for path in ("/api/session/login", "/api/session/verify-totp",
                     "/api/session/verify-email", "/api/session/logout"):
            r = c.post(path, json={})
            assert r.json().get("detail") != gate, f"{path} stopped at the API-key gate"
        # a request carrying the session cookie reaches the real check
        # (get_current_user), which refuses this bogus token on its own terms
        # (/api/stats/overview, not /api/users/me: that one is public anyway)
        r = _client().get("/api/stats/overview", cookies={"sf_session": "not-a-real-token"})
        assert r.status_code == 401 and r.json().get("detail") != gate
        # the brand the login page and the landing page read before sign-in
        # (this file's sqlite has no app_settings table, so past the gate the
        # route fails — the point here is only that it got past the gate)
        from starlette.testclient import TestClient
        import app.main as m
        site = TestClient(m.app, raise_server_exceptions=False).get("/api/public/site")
        assert gate not in site.text
        # and a request with neither a Bearer, nor a cookie, nor the key is
        # still refused at the gate
        bare = _client().get("/api/stats/overview")
        assert bare.status_code == 401 and bare.json().get("detail") == gate

    def test_email_assets_pass_the_api_key_gate(self, monkeypatch):
        """A mail client fetching a hero image (app/services/email_templates.py)
        carries neither a bearer, nor a cookie, nor the API key — the same
        situation /images and /downloads are already exempt for."""
        from app.config import get_settings
        monkeypatch.setattr(get_settings(), "api_key", "a-key-the-browser-never-sends")
        r = _client().get("/email-assets/hero-auth.png")
        assert r.status_code != 401
        assert r.status_code != 404, "app/static/email_assets/hero-auth.png is missing"

    def test_a_report_is_accepted_when_the_api_key_gate_is_on(self, monkeypatch):
        """Production sets API_KEY, and the middleware then refuses every /api
        path it does not list as public — the browser sends no key with a CSP
        report, so it was a 401 on the live site and every report was lost."""
        from app.config import get_settings
        monkeypatch.setattr(get_settings(), "api_key", "a-key-the-browser-never-sends")
        r = _client().post(
            "/api/public/csp-report",
            content=b'{"csp-report": {"document-uri": "https://sorinflow.com/", '
                     b'"violated-directive": "img-src", "blocked-uri": "https://cdn.example/x.png"}}',
            headers={"Content-Type": "application/csp-report"},
        )
        assert r.status_code == 204, r.text

    def test_a_reporting_api_report_is_accepted(self):
        r = _client().post(
            "/api/public/csp-report",
            content=b'[{"type": "csp-violation", "url": "https://sorinflow.com/dashboard/", '
                     b'"body": {"documentURL": "https://sorinflow.com/dashboard/", '
                     b'"effectiveDirective": "img-src", "blockedURL": "https://evil.example/x.png"}}]',
            headers={"Content-Type": "application/reports+json"},
        )
        assert r.status_code == 204

    def test_garbage_body_is_still_204_never_500(self):
        r = _client().post("/api/public/csp-report", content=b"not json at all")
        assert r.status_code == 204

    def test_oversize_body_is_rejected(self):
        r = _client().post("/api/public/csp-report", content=b"x" * (9 * 1024))
        assert r.status_code == 413

    def test_rate_limit_stops_recording_past_the_budget(self, monkeypatch):
        """Same intent as tests/test_phones_and_cdn.py's test of
        client_errors.record()'s own budget: call the checked-in helper
        directly rather than firing PER_MINUTE+ real HTTP requests, and check
        its True/False return rather than a status code the public route
        deliberately never varies (a rate limit is not something to reveal to
        the caller hitting it).

        A fake Redis, not the real one behind app.database.get_redis(): that
        global is a process-wide singleton, and by the time the full suite
        reaches this file it is routinely bound to a different, already-closed
        event loop from an earlier test — a real, pre-existing gap tracked in
        docs/PROGRESS.md, not something this one test should paper over by
        reaching into app.database's internals."""
        import asyncio
        import app.database as db
        import app.main as m

        class _FakeRedis:
            def __init__(self):
                self.n = 0

            async def incr(self, key):
                self.n += 1
                return self.n

            async def expire(self, key, seconds):
                pass

        fake = _FakeRedis()

        async def _fake_get_redis():
            return fake

        monkeypatch.setattr(db, "get_redis", _fake_get_redis)

        async def go():
            ip = f"test-{uuid.uuid4().hex[:8]}"
            return [await m._csp_report_allowed(ip) for _ in range(m._CSP_REPORT_PER_MINUTE + 5)]

        results = asyncio.run(go())
        assert results.count(True) == m._CSP_REPORT_PER_MINUTE
        assert results.count(False) == 5
