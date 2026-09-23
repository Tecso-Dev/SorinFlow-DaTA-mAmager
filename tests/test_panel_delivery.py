"""
The panel was being served raw.

665 KB of app.js, 359 KB of markup, 170 KB of stylesheet — about 1.9 MB over
the wire before anything appeared on screen, with no Content-Encoding on any
of it. Over a domestic Iranian line that is a wait, and no amount of redesign
makes it shorter. Text compresses five- to sixfold, so this is the cheapest
speed there is: the same panel, a third of the bytes, not a line of it changed.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_delivery.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from starlette.middleware.gzip import GZipMiddleware  # noqa: E402

import app.main as m  # noqa: E402


class TestTheBytesOnTheWire:

    def test_the_app_compresses(self):
        assert any(mw.cls is GZipMiddleware for mw in m.app.user_middleware), \
            "nothing was setting Content-Encoding, so every asset went out raw"

    def test_it_is_the_outermost_layer(self):
        """Starlette runs the last-registered middleware first, so it has to be
        registered last to wrap what everything below it produces."""
        classes = [mw.cls for mw in m.app.user_middleware]
        assert classes[0] is GZipMiddleware, \
            f"gzip must be outermost; stack starts with {classes[0]}"

    def test_small_answers_are_left_alone(self):
        """Below a kilobyte the header costs more than the saving."""
        gz = next(mw for mw in m.app.user_middleware if mw.cls is GZipMiddleware)
        assert gz.kwargs.get("minimum_size", 0) >= 500

    def test_the_panel_shell_actually_arrives_compressed(self):
        from fastapi.testclient import TestClient
        # No lifespan: startup wants Postgres, and this asks about bytes on the
        # wire, which the middleware decides on its own.
        c = TestClient(m.app)
        r = c.get("/dashboard/js/app.js", headers={"Accept-Encoding": "gzip"})
        if r.status_code == 404:
            return                          # frontend not mounted in this env
        assert r.headers.get("content-encoding") == "gzip", \
            "the panel's largest asset still goes out raw"
        assert len(r.content) > 100_000, "that is not app.js"
        # It streams, so there is no content-length to read the saving from —
        # what the transfer costs is what gzip makes of those bytes.
        import gzip as _gz
        assert len(_gz.compress(r.content)) < len(r.content) / 3, \
            "the panel's code should compress at least threefold"
