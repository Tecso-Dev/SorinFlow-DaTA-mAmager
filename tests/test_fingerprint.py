"""
What Divar's JavaScript sees when it looks at our browser.

Measured on the live pod, 2026-09-12, before this change:

    user-agent           Chrome/130 on Windows      the engine is Chromium 121
    sec-ch-ua            <absent>                   real Chrome always sends it
    navigator.webdriver  undefined, own descriptor  real Chrome: false, inherited
    navigator.plugins    [1,2,3,4,5]                plugins[0].name undefined
    window.chrome        {runtime:{}}               real: loadTimes, csi, app

Each line is a one-comparison bot check. These tests launch the real
Chromium the scraper launches, the way the scraper launches it, and assert
the properties a detection script would read. They need Playwright's
Chromium installed; they need no network.
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_fp.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.scraper.stealth import (  # noqa: E402
    CHROMIUM_MAJOR, Device, StealthConfig, get_browser_args, get_context_options, open_browser,
)

PROBE = """() => ({
  ua: navigator.userAgent, platform: navigator.platform,
  webdriver: String(navigator.webdriver),
  wd_own: !!Object.getOwnPropertyDescriptor(navigator, 'webdriver'),
  plugins_len: navigator.plugins.length,
  plugin0: (navigator.plugins[0] && navigator.plugins[0].name) || null,
  chrome_keys: window.chrome ? Object.keys(window.chrome).sort().join(',') : 'NONE',
  brands: navigator.userAgentData ? navigator.userAgentData.brands.map(b => b.brand + '/' + b.version).join('|') : 'n/a',
  uad_platform: navigator.userAgentData ? navigator.userAgentData.platform : 'n/a',
  vp: innerWidth + 'x' + innerHeight,
  langs: navigator.languages.join(','),
})"""


def _chromium_available() -> bool:
    try:
        from playwright.async_api import async_playwright

        async def probe():
            async with async_playwright() as p:
                b = await p.chromium.launch(headless=True)
                await b.close()
        asyncio.run(probe())
        return True
    except Exception:
        return False


needs_chromium = pytest.mark.skipif(not _chromium_available(),
                                    reason="Playwright Chromium is not installed here")


async def _probe(account):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser, context, page, device = await open_browser(
            p, headless=True, account=account, stealth_config=StealthConfig())
        try:
            await page.goto("about:blank")
            js = await page.evaluate(PROBE)
        finally:
            await browser.close()
    return js, device


class TestTheStaticParts:
    """Cheap, no browser."""

    def test_the_ua_claims_the_engine_that_is_actually_running(self):
        d = Device.for_account("09120000000")
        assert f"Chrome/{CHROMIUM_MAJOR}." in d.user_agent
        meta = d.cdp_override()["userAgentMetadata"]
        assert all(b["version"] == CHROMIUM_MAJOR for b in meta["brands"] if "Chrom" in b["brand"])

    def test_the_pinned_playwright_matches_the_declared_engine(self):
        """CHROMIUM_MAJOR must move when requirements.txt does."""
        req = open("requirements.txt", encoding="utf-8").read()
        assert "playwright==1.41.0" in req, "Playwright pin changed — re-measure CHROMIUM_MAJOR/FULL"
        assert CHROMIUM_MAJOR == "121"

    def test_no_javascript_is_injected(self):
        import app.scraper.stealth as st
        assert not hasattr(st, "STEALTH_JS")

    def test_the_context_never_overrides_the_ua_the_playwright_way(self):
        """That path strips Client Hints. The UA is set over CDP with metadata."""
        assert "user_agent" not in get_context_options(StealthConfig())

    @pytest.mark.parametrize("flag", ["--disable-web-security", "--ignore-certificate-errors",
                                      "--disable-site-isolation-trials"])
    def test_the_dangerous_flags_are_gone(self, flag):
        assert flag not in get_browser_args(headless=True)

    def test_headless_uses_the_new_mode(self):
        assert "--headless=new" in get_browser_args(headless=True)
        assert "--headless=new" not in get_browser_args(headless=False)

    def test_a_device_is_stable_for_an_account(self):
        assert Device.for_account("09120000000") == Device.for_account("09120000000")

    def test_two_accounts_are_not_forced_to_the_same_device(self):
        """Not guaranteed different for any two — a hash can collide — but
        across the real pool of ten they must not all be one laptop."""
        pool = ["09017852452", "09029315496", "09053833026", "09058432452", "09125005495",
                "09145172065", "09146382408", "09190665165", "09362191758", "09982469110"]
        assert len({Device.for_account(p) for p in pool}) >= 3

    def test_the_viewport_is_a_real_size(self):
        d = Device.for_account("09120000000")
        assert (d.width, d.height) in {(1920, 1080), (1366, 768), (1536, 864),
                                       (1440, 900), (1600, 900), (1280, 720)}


@needs_chromium
class TestWhatDivarSees:
    """The real browser, launched the real way."""

    @pytest.fixture(scope="class")
    def seen(self):
        return asyncio.run(_probe("09146382408"))

    def test_webdriver_is_false_and_inherited(self, seen):
        js, _ = seen
        assert js["webdriver"] == "false"
        assert js["wd_own"] is False, "an own-property override is itself the tell"

    def test_real_plugins(self, seen):
        js, _ = seen
        assert js["plugins_len"] >= 1
        assert js["plugin0"], "plugins[0].name must be a real string"

    def test_real_window_chrome(self, seen):
        js, _ = seen
        assert "loadTimes" in js["chrome_keys"] and "csi" in js["chrome_keys"]

    def test_the_ua_header_and_client_hints_agree(self, seen):
        js, device = seen
        assert js["ua"] == device.user_agent
        assert "HeadlessChrome" not in js["ua"]
        assert f"Google Chrome/{CHROMIUM_MAJOR}" in js["brands"], js["brands"]
        assert "HeadlessChrome" not in js["brands"]

    def test_platform_agrees_everywhere(self, seen):
        js, _ = seen
        assert js["platform"] == "Win32"
        assert js["uad_platform"] == "Windows"

    def test_viewport_is_the_devices(self, seen):
        js, device = seen
        assert js["vp"] == f"{device.width}x{device.height}"
