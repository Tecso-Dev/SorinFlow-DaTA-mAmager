"""
CORS: the site answered every foreign origin with «allow-origin: *» and
«allow-credentials: true». With a cookie on the request Starlette echoes the
caller's Origin, so any site could read a credentialed response — harmless
while logins are bearer tokens, not once they become cookies. The panel, the
portal and the landing page are same-origin and need no CORS at all.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_cors.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")


def _cors(app):
    return next(m for m in app.user_middleware if m.cls.__name__ == "CORSMiddleware").kwargs


def _headers(origins, credentials, cookie=True):
    """What a browser on evil.example is told, through Starlette's own
    CORSMiddleware configured the way app/main.py configures it."""
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=credentials,
                       allow_methods=["*"], allow_headers=["*"])

    @app.get("/x")
    def x():
        return {"ok": True}
    h = {"Origin": "https://evil.example"}
    if cookie:
        h["Cookie"] = "sf_session=1"
    return TestClient(app).get("/x", headers=h).headers


class TestTheSiteItself:

    def test_by_default_no_other_origin_is_allowed(self):
        import app.main as m
        kw = _cors(m.app)
        assert kw["allow_origins"] == [] and kw["allow_credentials"] is False
        assert "access-control-allow-origin" not in _headers(kw["allow_origins"], kw["allow_credentials"])

    def test_the_old_setting_let_a_foreign_site_read_a_credentialed_answer(self):
        h = _headers(["*"], True)
        assert h["access-control-allow-origin"] == "https://evil.example"
        assert h["access-control-allow-credentials"] == "true"


class TestWhatCorsOriginsMayName:

    def test_nothing_set_means_same_origin_only(self):
        from app.main import _cors_config
        assert _cors_config("") == ([], False)

    def test_named_origins_get_credentials(self):
        from app.main import _cors_config
        assert _cors_config("https://app.example, https://b.example") == \
            (["https://app.example", "https://b.example"], True)

    def test_a_star_never_comes_with_credentials(self):
        from app.main import _cors_config
        origins, credentials = _cors_config("*")
        assert (origins, credentials) == (["*"], False)
        assert "access-control-allow-credentials" not in _headers(origins, credentials)
