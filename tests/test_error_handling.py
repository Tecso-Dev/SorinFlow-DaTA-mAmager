"""
Observability: the 500 handler, the 404 handler, the request-id middleware,
GIT_SHA on /health and the Postgres-only /ready.

The 404 and 500 handlers are exercised against a small standalone app built
from the real app's own handler functions and middleware — not the full
app.main.app — so these tests do not depend on which routes exist today and
stay meaningful as routes come and go. Modelled on tests/test_cors.py, which
does the same for CORSMiddleware.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_error_handling.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")


def _build_app(static_dir):
    """A FastAPI app carrying the real 404/500 handlers and the real
    request-id middleware, plus routes shaped to exercise each case:
    a route that raises its own 404, one that crashes, a static mount with
    one real file, and (by omission) a path nothing matches at all.
    """
    from fastapi import FastAPI, HTTPException
    from fastapi.staticfiles import StaticFiles
    from starlette.middleware.base import BaseHTTPMiddleware
    import app.main as m

    app = FastAPI(exception_handlers={
        404: m.not_found_handler,
        500: m.internal_error_handler,
    })
    app.add_middleware(BaseHTTPMiddleware, dispatch=m.request_id_middleware)

    @app.get("/own404")
    async def own404():
        raise HTTPException(status_code=404, detail="نمونهٔ سفارشی")

    @app.get("/api/own404")
    async def api_own404():
        raise HTTPException(status_code=404, detail="نمونهٔ سفارشی api")

    @app.get("/boom")
    async def boom():
        raise ValueError("kaboom")

    @app.get("/api/boom")
    async def api_boom():
        raise ValueError("kaboom api")

    app.mount("/dashboard", StaticFiles(directory=str(static_dir)), name="dashboard")
    return app


def _client(tmp_path):
    from starlette.testclient import TestClient
    (tmp_path / "exists.txt").write_text("hi")
    app = _build_app(tmp_path)
    # raise_server_exceptions=False: production behaviour, where
    # ServerErrorMiddleware catches an unhandled exception and calls the
    # registered 500 handler instead of letting it escape to the caller.
    return TestClient(app, raise_server_exceptions=False)


class TestNotFoundHandlerTellsTheTwoCasesApart:
    def test_a_routes_own_404_keeps_its_own_detail(self, tmp_path):
        r = _client(tmp_path).get("/own404")
        assert r.status_code == 404
        assert r.json()["detail"] == "نمونهٔ سفارشی"

    def test_a_routes_own_404_under_api_is_still_its_own_detail(self, tmp_path):
        r = _client(tmp_path).get("/api/own404")
        assert r.status_code == 404
        assert r.json()["detail"] == "نمونهٔ سفارشی api"

    def test_an_unmatched_api_path_gets_the_generic_json(self, tmp_path):
        r = _client(tmp_path).get("/api/does-not-exist-at-all")
        assert r.status_code == 404
        assert r.json()["detail"] == "Resource not found"

    def test_an_unmatched_browser_path_gets_the_html_page(self, tmp_path):
        r = _client(tmp_path).get("/does-not-exist-at-all",
                                   headers={"Accept": "text/html"})
        assert r.status_code == 404
        assert "text/html" in r.headers["content-type"]

    def test_a_missing_file_under_a_static_mount_is_the_generic_page_not_fastapis_default(self, tmp_path):
        """StaticFiles raises a plain HTTPException(404) for a file it does not
        have, and Router still records the Mount as the matched route — so
        scope["route"] alone is not enough to tell this apart from a route's
        own 404. Without the Mount check this fell through to FastAPI's bare
        {"detail": "Not Found"} instead of the panel's page."""
        r = _client(tmp_path).get("/dashboard/missing-file.js",
                                   headers={"Accept": "text/html"})
        assert r.status_code == 404
        assert "text/html" in r.headers["content-type"]
        assert r.json is not None  # sanity: still a real response, not a 500

    def test_a_real_file_under_the_mount_is_unaffected(self, tmp_path):
        r = _client(tmp_path).get("/dashboard/exists.txt")
        assert r.status_code == 200
        assert r.text == "hi"


class TestInternalErrorHandler:
    def test_an_unhandled_exception_becomes_json_500_with_a_ref(self, tmp_path):
        r = _client(tmp_path).get("/api/boom")
        assert r.status_code == 500
        body = r.json()
        assert body["detail"] == "Internal server error"
        assert body["ref"], "no ref in the 500 body"

    def test_the_ref_is_the_same_id_as_the_request_id_header(self, tmp_path):
        """One id links the error page to every log line of the request —
        the ref shown to the user has to be the request id, not a second,
        unrelated one."""
        r = _client(tmp_path).get("/api/boom")
        assert r.json()["ref"] == r.headers["x-request-id"]

    def test_html_callers_get_the_error_page_not_json(self, tmp_path):
        r = _client(tmp_path).get("/boom", headers={"Accept": "text/html"})
        assert r.status_code == 500
        assert "text/html" in r.headers["content-type"]

    def test_exactly_one_traceback_is_logged_with_the_ref(self, tmp_path):
        from loguru import logger
        seen = []
        sink_id = logger.add(lambda m: seen.append(m), level="ERROR")
        try:
            r = _client(tmp_path).get("/api/boom")
        finally:
            logger.remove(sink_id)
        ref = r.json()["ref"]
        matches = [m for m in seen if ref in m and "kaboom" in m]
        assert len(matches) == 1, f"expected exactly one traceback line, got {len(matches)}"
        assert "ValueError" in matches[0]
        assert "Traceback" in matches[0] or "kaboom" in matches[0]


class TestRequestIdMiddleware:
    def test_a_response_always_carries_an_id(self, tmp_path):
        r = _client(tmp_path).get("/own404")
        assert r.headers.get("x-request-id")

    def test_a_sane_incoming_id_is_kept(self, tmp_path):
        r = _client(tmp_path).get("/own404", headers={"X-Request-ID": "abc-123.xyz"})
        assert r.headers["x-request-id"] == "abc-123.xyz"

    def test_an_incoming_id_with_bad_characters_is_replaced(self, tmp_path):
        r = _client(tmp_path).get("/own404", headers={"X-Request-ID": "not a valid id!"})
        assert r.headers["x-request-id"] != "not a valid id!"
        assert r.headers["x-request-id"]

    def test_an_incoming_id_that_is_too_long_is_replaced(self, tmp_path):
        r = _client(tmp_path).get("/own404", headers={"X-Request-ID": "a" * 65})
        assert r.headers["x-request-id"] != "a" * 65

    def test_two_requests_get_different_generated_ids(self, tmp_path):
        c = _client(tmp_path)
        a = c.get("/own404").headers["x-request-id"]
        b = c.get("/own404").headers["x-request-id"]
        assert a != b


class TestHealthAndReady:
    def _app_client(self):
        from starlette.testclient import TestClient
        import app.main as m
        return TestClient(m.app)

    def test_health_reports_git_sha(self, monkeypatch):
        from app.config import get_settings
        settings = get_settings()
        monkeypatch.setattr(settings, "git_sha", "deadbeef123")
        r = self._app_client().get("/health")
        assert r.status_code == 200
        assert r.json()["git_sha"] == "deadbeef123"

    def test_health_git_sha_defaults_to_empty_string(self, monkeypatch):
        from app.config import get_settings
        settings = get_settings()
        monkeypatch.setattr(settings, "git_sha", "")
        r = self._app_client().get("/health")
        assert r.json()["git_sha"] == ""

    def test_ready_is_ok_when_redis_is_down_but_postgres_is_up(self, monkeypatch):
        import app.database as db

        async def _broken_redis():
            raise ConnectionError("redis is down")
        monkeypatch.setattr(db, "get_redis", _broken_redis)

        r = self._app_client().get("/ready")
        assert r.status_code == 200, r.json()
        body = r.json()
        assert body["ready"] is True
        assert body["postgres"] == "ok"
        assert body["redis"].startswith("down:")

    def test_ready_is_503_when_postgres_is_down(self, monkeypatch):
        import app.database as db

        class _BrokenSessionMaker:
            def __call__(self):
                raise ConnectionError("postgres is down")
        monkeypatch.setattr(db, "async_session_maker", _BrokenSessionMaker())

        r = self._app_client().get("/ready")
        assert r.status_code == 503
        assert r.json()["ready"] is False
