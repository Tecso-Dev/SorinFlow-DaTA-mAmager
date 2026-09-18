"""
The panel as an app.

A consultant taps a home-screen icon and gets the call list full-screen.
What makes that work is three files and two rules: a manifest with real
icons, a service worker registered under /dashboard/, the shell cached
network-first — and never, under any circumstances, an /api/ response.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_pwa.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

ROOT = Path(__file__).resolve().parent.parent
FE = ROOT / "frontend"
INDEX = (FE / "index.html").read_text(encoding="utf-8")
JS = (FE / "js/app.js").read_text(encoding="utf-8")
SW = (FE / "sw.js").read_text(encoding="utf-8")


class TestManifest:

    def test_it_is_valid_and_complete(self):
        m = json.loads((FE / "manifest.webmanifest").read_text(encoding="utf-8"))
        for k in ("name", "short_name", "start_url", "scope", "display", "icons", "theme_color", "background_color"):
            assert k in m, k
        assert m["display"] == "standalone" and m["scope"] == "/dashboard/"
        assert m["start_url"].startswith("/dashboard/")
        assert m["dir"] == "rtl" and m["lang"] == "fa"

    def test_the_icons_exist_at_the_sizes_claimed(self):
        from PIL import Image
        m = json.loads((FE / "manifest.webmanifest").read_text(encoding="utf-8"))
        purposes = set()
        for ic in m["icons"]:
            f = FE / ic["src"]
            assert f.exists(), ic["src"]
            w, h = Image.open(f).size
            assert f"{w}x{h}" == ic["sizes"], (ic["src"], (w, h))
            purposes.add(ic.get("purpose", "any"))
        assert {"any", "maskable"} <= purposes
        assert (FE / "icons/apple-touch-icon.png").exists()

    def test_the_page_links_it_and_speaks_ios(self):
        assert '<link rel="manifest" href="manifest.webmanifest">' in INDEX
        assert 'name="theme-color"' in INDEX
        assert 'name="apple-mobile-web-app-capable" content="yes"' in INDEX
        assert 'rel="apple-touch-icon"' in INDEX

    def test_the_server_knows_the_media_type(self):
        import mimetypes
        import app.main  # noqa: F401
        assert mimetypes.guess_type("manifest.webmanifest")[0] == "application/manifest+json"


class TestServiceWorker:

    def test_it_is_registered_relative_to_the_dashboard(self):
        assert "navigator.serviceWorker.register('sw.js')" in JS
        assert (FE / "sw.js").exists()

    def test_the_api_is_never_cached(self):
        assert "url.pathname.indexOf('/api/') === 0" in SW
        # every fetch path that could reach a cache put is behind the api guard
        assert SW.index("isApi(url)") < SW.index("caches.match(req)")
        assert "/images/" in SW and "/downloads/" in SW, "user data and the APK are not shell either"

    def test_the_shell_is_network_first_so_a_deploy_needs_no_bump(self):
        blk = SW[SW.index("// the shell: network first"):]
        assert "fetch(req).then" in blk and ".catch(function () {" in blk
        assert "caches.match(req)" in blk
        assert "CACHE = 'sorinflow-shell-v1'" in SW

    def test_vendor_and_icons_are_cache_first(self):
        assert "isImmutable(url)" in SW and "/dashboard\\/(vendor|icons)\\/" in SW

    def test_it_is_es5(self):
        for modern in ("=>", "const ", "let ", "`", "?.", "async "):
            assert modern not in SW, f"old phones again: {modern!r}"

    def test_one_copy_per_versioned_file(self):
        assert "ku.pathname === url.pathname && ku.search !== url.search" in SW


class TestInstallHint:

    def test_phones_get_a_hint_desktops_do_not(self):
        fn = JS[JS.index("function _pwaMaybeHint"):JS.index("async function pwaInstall")]
        assert "_pwaStandalone()" in fn, "an installed app must not be asked to install itself"
        assert "/iPhone|iPad|iPod/" in fn and "/Android/" in fn
        assert "if (!phone) return;" in fn
        assert "sf_pwa_dismissed" in fn

    def test_the_prompt_is_kept_for_the_button(self):
        assert "window.addEventListener('beforeinstallprompt'" in JS
        assert "e.preventDefault();" in JS[JS.index("beforeinstallprompt"):JS.index("beforeinstallprompt") + 200]
        assert 'id="pwa-install-btn"' in INDEX and 'id="pwa-ios"' in INDEX

    def test_the_hint_uses_the_panels_tokens(self):
        css = (ROOT / "frontend/css/style.css").read_text(encoding="utf-8")
        blk = css[css.index(".pwa-hint {"):]
        assert "var(--accent)" in blk and "var(--r-sm)" in blk
        assert "--bs-" not in blk
