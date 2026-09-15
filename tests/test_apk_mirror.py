"""
The forwarder APK is served from this site, mirrored from GitHub.

The phone that needs the app is the one that cannot fetch it from GitHub's
download host, so the panel's button must point here — and what is here must
be the latest release, whole, with the version shown beside the button.
"""
import os
import sys
import asyncio
import json
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_apk.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.services import apk_mirror as m   # noqa: E402

APK = b"PK\x03\x04" + b"x" * 5000
RELEASE = {"tag_name": "v3.1.1", "assets": [
    {"name": "notes.txt", "browser_download_url": "https://dl/notes.txt", "size": 5},
    {"name": "sorinflow-forwarder-v3.1.1.apk",
     "browser_download_url": "https://dl/sorinflow-forwarder-v3.1.1.apk", "size": len(APK)},
]}


class TestPickingTheAsset:

    def test_the_apk_not_the_notes(self):
        tag, url, size = m.pick_apk_asset(RELEASE)
        assert (tag, url, size) == ("v3.1.1", "https://dl/sorinflow-forwarder-v3.1.1.apk", len(APK))

    def test_a_release_without_an_apk(self):
        tag, url, size = m.pick_apk_asset({"tag_name": "v9", "assets": [{"name": "a.txt"}]})
        assert tag == "v9" and url is None and size == 0


def _fake_client(release, body, monkeypatch, calls):
    """httpx.AsyncClient that answers GitHub from memory."""
    def handler(req: httpx.Request):
        calls.append(str(req.url))
        if str(req.url) == m.LATEST_URL:
            return httpx.Response(200, json=release)
        return httpx.Response(200, content=body)

    real = httpx.AsyncClient

    class Fake(real):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **kw)
    monkeypatch.setattr(m.httpx, "AsyncClient", Fake)


class TestRefreshing:

    def test_a_new_release_is_downloaded_whole_and_versioned(self, tmp_path, monkeypatch):
        monkeypatch.setattr(m.settings, "downloads_path", str(tmp_path), raising=False)
        calls = []
        _fake_client(RELEASE, APK, monkeypatch, calls)
        r = asyncio.run(m.refresh())
        assert r["changed"] is True and r["version"] == "v3.1.1" and r["bytes"] == len(APK)
        assert (tmp_path / "sorinflow-forwarder.apk").read_bytes() == APK
        assert (tmp_path / "VERSION").read_text().strip() == "v3.1.1"
        assert not (tmp_path / "sorinflow-forwarder.apk.part").exists(), "half-written file left behind"
        assert m.mirrored_version() == "v3.1.1"

    def test_the_same_release_is_not_downloaded_again(self, tmp_path, monkeypatch):
        monkeypatch.setattr(m.settings, "downloads_path", str(tmp_path), raising=False)
        (tmp_path / "sorinflow-forwarder.apk").write_bytes(APK)
        (tmp_path / "VERSION").write_text("v3.1.1\n")
        calls = []
        _fake_client(RELEASE, APK, monkeypatch, calls)
        r = asyncio.run(m.refresh())
        assert r["changed"] is False and r["version"] == "v3.1.1"
        assert calls == [m.LATEST_URL], "the asset was fetched although nothing moved"

    def test_a_short_download_never_replaces_the_good_copy(self, tmp_path, monkeypatch):
        """A network that drops mid-file must not leave a truncated APK where
        the panel serves it."""
        monkeypatch.setattr(m.settings, "downloads_path", str(tmp_path), raising=False)
        old = b"PK\x03\x04old-good-apk"
        (tmp_path / "sorinflow-forwarder.apk").write_bytes(old)
        (tmp_path / "VERSION").write_text("v3.1.0\n")
        calls = []
        _fake_client(RELEASE, APK[:100], monkeypatch, calls)
        with pytest.raises(RuntimeError):
            asyncio.run(m.refresh())
        assert (tmp_path / "sorinflow-forwarder.apk").read_bytes() == old
        assert m.mirrored_version() == "v3.1.0"
        assert not (tmp_path / "sorinflow-forwarder.apk.part").exists()

    def test_no_copy_means_no_version(self, tmp_path, monkeypatch):
        monkeypatch.setattr(m.settings, "downloads_path", str(tmp_path), raising=False)
        (tmp_path / "VERSION").write_text("v3.1.1\n")     # a version file without the apk
        assert m.mirrored_version() == ""


class TestItIsWiredIn:

    def test_the_site_serves_downloads_publicly_with_the_right_media_type(self):
        import mimetypes
        import app.main  # noqa: F401  (registers the media type)
        assert mimetypes.guess_type("x.apk")[0] == "application/vnd.android.package-archive"
        src = Path("app/main.py").read_text(encoding="utf-8")
        assert 'app.mount("/downloads", StaticFiles(directory=settings.downloads_path)' in src
        assert 'request.url.path.startswith("/downloads")' in src, "the API-key gate would 401 the phone"

    def test_the_loop_starts_with_the_app(self):
        src = Path("app/main.py").read_text(encoding="utf-8")
        assert "apk_task = asyncio.create_task(_apk_mirror())" in src
        assert "apk_task.cancel()" in src

    def test_the_guide_gets_the_version_and_the_source(self):
        src = Path("app/api/routes/forwarder.py").read_text(encoding="utf-8")
        assert '"android_apk_version": _apk_version()' in src
        assert '"android_release_url"' in src
        assert "sobhanaz/sorinflow-sms-forwarder" in src

    def test_the_guide_names_the_real_apps_buttons(self):
        js = Path("frontend/js/app.js").read_text(encoding="utf-8")
        for needle in ("Set up", "Scan QR", "Send test to server",
                       "Allow restricted settings", "Play Protect", "RCS"):
            assert needle in js, needle
        assert "android_apk_version" in js and "android_release_url" in js
