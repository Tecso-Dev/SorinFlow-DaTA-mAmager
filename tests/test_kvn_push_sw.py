"""
The old Kavenegar push worker, and how it leaves (#35).

Browsers that visited the landing page or the portal while the Kavenegar push
SDK was on them registered /kvn-push-sw.js, and they keep it — the worker
imports Kavenegar's own script and connects to Kavenegar's domain, which the
site's CSP then reports (70 reports of `connect-src` in the log). Push was
removed, but a registration outlives the page that made it: the only way to
get rid of it is to serve, at the same address, a newer script that removes
itself.

The first class checks what the real app serves, with the real middleware in
front of it. The second one runs a real Chromium: it registers an old-style
worker, swaps the served script for the one the app serves now, lets the
browser do its own update check, and asserts the registration is gone —
without a single request leaving 127.0.0.1.
"""
import asyncio
import glob
import os
import re
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_kvn_push_sw.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

import app.main as m  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def _client():
    from fastapi.testclient import TestClient
    return TestClient(m.app)      # no lifespan: startup wants Postgres


class TestWhatTheAppServes:

    def test_it_unregisters_itself(self):
        body = _client().get("/kvn-push-sw.js").text
        assert re.search(r"registration\s*\.\s*unregister\s*\(", body), body
        assert "skipWaiting" in body, "without it the new worker waits behind the old one until every tab is closed"

    def test_it_does_nothing_else(self):
        """No fetch handler (it would sit in front of every request of every
        page it still controls), no script imported from anywhere, no
        connection to anything, and no address of any site in the file."""
        body = _client().get("/kvn-push-sw.js").text
        code = re.sub(r"//[^\n]*|/\*.*?\*/", "", body, flags=re.S)
        assert not re.search(r"""addEventListener\(\s*['"](fetch|push|message|sync|notificationclick)['"]""", code), code
        assert not re.search(r"\bonfetch\b|\bonpush\b|\bonmessage\b", code), code
        for banned in ("importScripts", "fetch(", "XMLHttpRequest", "WebSocket", "EventSource",
                       "sendBeacon", "caches.", "indexedDB", "clients.claim", "showNotification"):
            assert banned not in code, banned
        assert not re.search(r"https?://", body), "the file names no site, in code or in comments"
        assert "kavenegar" not in body.lower()

    def test_browsers_are_told_to_look_again_every_time(self):
        """The old route said «cache for a day». A script a browser holds for a
        day is a worker that stays for a day: revalidate on every check."""
        cc = _client().get("/kvn-push-sw.js").headers["cache-control"].lower()
        directives = {d.strip() for d in cc.split(",")}
        assert "no-cache" in directives or "max-age=0" in directives, cc
        assert not any(d.startswith("max-age=") and d != "max-age=0" for d in directives), cc
        assert "public" not in directives or "no-cache" in directives, cc


# ── a real browser ───────────────────────────────────────────────────────────

def _launch_args():
    # Nothing but this machine is reachable: a name that is not 127.0.0.1
    # fails to resolve, so a request to another site cannot even be attempted.
    return ["--no-sandbox", "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1"]


async def _launch(p):
    """The Chromium Playwright installed for this version, or — where the
    browsers directory holds a build of another revision (a CI image, a
    sandbox) — the newest chrome found under PLAYWRIGHT_BROWSERS_PATH."""
    try:
        return await p.chromium.launch(headless=True, args=_launch_args())
    except Exception:
        root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
        found = sorted(glob.glob(os.path.join(root, "chromium-*", "chrome-linux", "chrome")))
        if not found:
            raise
        return await p.chromium.launch(headless=True, executable_path=found[-1], args=_launch_args())


def _chromium_available() -> bool:
    try:
        from playwright.async_api import async_playwright

        async def probe():
            async with async_playwright() as p:
                b = await _launch(p)
                await b.close()
        asyncio.run(probe())
        return True
    except Exception:
        return False


needs_chromium = pytest.mark.skipif(not _chromium_available(),
                                    reason="Playwright Chromium is not installed here")

# What a browser that visited before this change holds: a worker that is
# active, controls the page, and has handlers for the requests and the
# pushes that arrive. (The real one imports Kavenegar's script, which cannot
# be fetched from a test.)
OLD_WORKER = b"""
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', () => {});
self.addEventListener('push', () => {});
"""

PAGE = b"""<!doctype html><meta charset="utf-8"><title>page</title>
<script>
window.registered = navigator.serviceWorker
  .register('/kvn-push-sw.js', {scope: '/'})
  .then(() => navigator.serviceWorker.ready).then(() => true, (e) => String(e));
</script>"""


class _Site:
    """One origin on 127.0.0.1 that, before the switch, serves the old worker
    and, after it, whatever the real app serves at that address."""

    def __init__(self):
        import socket
        import uvicorn
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        self.port = s.getsockname()[1]
        s.close()
        self.old = True
        self.script_requests = 0
        self.other_hosts = []
        site = self

        async def app(scope, receive, send):
            if scope["type"] != "http":
                return await m.app(scope, receive, send)
            path = scope["path"]
            if path == "/":
                return await _send(send, 200, PAGE, {"content-type": "text/html; charset=utf-8"})
            if path == "/kvn-push-sw.js":
                site.script_requests += 1
                if site.old:
                    return await _send(send, 200, OLD_WORKER, {
                        "content-type": "application/javascript", "service-worker-allowed": "/",
                        "cache-control": "public, max-age=86400"})
            return await m.app(scope, receive, send)

        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port,
                                                    log_level="error", lifespan="off"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        for _ in range(100):
            if self.server.started:
                return self
            time.sleep(0.05)
        raise RuntimeError("the test server did not start")

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(timeout=5)

    @property
    def base(self):
        return f"http://127.0.0.1:{self.port}"


async def _send(send, status, body, headers):
    await send({"type": "http.response.start", "status": status,
                "headers": [(k.encode(), v.encode()) for k, v in headers.items()]})
    await send({"type": "http.response.body", "body": body})


async def _registrations(page) -> int:
    return await page.evaluate("navigator.serviceWorker.getRegistrations().then(r => r.length)")


async def _wait_until(page, want: int, seconds: float = 15.0) -> int:
    end = time.monotonic() + seconds
    n = await _registrations(page)
    while n != want and time.monotonic() < end:
        await asyncio.sleep(0.2)
        n = await _registrations(page)
    return n


@needs_chromium
class TestInARealBrowser:

    async def test_a_registration_of_the_old_worker_is_removed_after_the_new_script_is_fetched(self):
        from playwright.async_api import async_playwright
        with _Site() as site:
            async with async_playwright() as p:
                browser = await _launch(p)
                ctx = await browser.new_context()
                seen = []
                ctx.on("request", lambda r: seen.append(r.url))
                page = await ctx.new_page()
                await page.goto(site.base + "/")
                assert await page.evaluate("window.registered") is True
                assert await _registrations(page) == 1, "the old worker is registered — the state of a returning visitor"

                site.old = False       # the deploy: the same address now serves the app's own script
                update = await page.evaluate(
                    "navigator.serviceWorker.getRegistration('/').then(r => r.update()).then(() => 'ok', e => String(e))")

                assert await _wait_until(page, 0) == 0, \
                    f"the registration is still there after the browser fetched the new script (update: {update})"
                assert not [u for u in seen if not u.startswith(site.base)], seen
                await browser.close()

    async def test_a_page_that_registers_it_again_leaves_nothing_behind(self):
        """The Kavenegar SDK on a page registers this address on every visit;
        each time, the worker removes itself again."""
        from playwright.async_api import async_playwright
        with _Site() as site:
            site.old = False
            async with async_playwright() as p:
                browser = await _launch(p)
                ctx = await browser.new_context()
                seen = []
                ctx.on("request", lambda r: seen.append(r.url))
                page = await ctx.new_page()
                await page.goto(site.base + "/")
                assert await page.evaluate("window.registered") is True
                assert await _wait_until(page, 0) == 0
                await page.goto(site.base + "/")
                assert await page.evaluate("window.registered") is True
                assert await _wait_until(page, 0) == 0
                assert not [u for u in seen if not u.startswith(site.base)], seen
                await browser.close()
