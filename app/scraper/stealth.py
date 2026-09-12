"""
SorinFlow Divar Scraper - how the browser presents itself.

Measured on the live pod (2026-09-12), what Divar actually received from the
previous version of this file:

    user-agent:          Chrome/130 on Windows         (the engine is 121)
    sec-ch-ua:           <absent>                      Playwright's UA override
                                                       strips Client Hints; real
                                                       Chrome always sends them
    navigator.webdriver: undefined, own descriptor     real Chrome: false, inherited
    navigator.plugins:   [1,2,3,4,5], plugins[0].name undefined
    window.chrome:       {runtime:{}}                  real: loadTimes, csi, app
    navigator.platform:  Win32 under a Macintosh UA    3 of the 8 UAs

Each line is a one-comparison tell; together they are the signature of a
copied "stealth" snippet, and modern detection greps for exactly these.

What replaced it, measured the same way (see tests/test_fingerprint.py):

    --headless=new      real Chrome, not old headless: PDF Viewer plugins,
                        window.chrome with loadTimes/csi/app, webdriver=false
                        with no override trace. No injected JavaScript at all.
    CDP UA override     Emulation.setUserAgentOverride WITH userAgentMetadata,
                        so the header, Sec-CH-UA, Sec-CH-UA-Platform,
                        navigator.platform and navigator.userAgentData all
                        say the same thing: Chrome 121 — the real engine — on
                        Windows.
    one device per      viewport, Windows version and Accept-Language are
    account             chosen by hashing the account's phone number, so
                        account A is always the same laptop and account B is a
                        different one. Before, every account shared one
                        fingerprint and a browser recycle changed it under the
                        same account — the inverse of what people look like.

Delay and request-limit settings are unchanged; other code reads them.
"""
import hashlib
import os
import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# The engine that is actually running. The UA must claim this version and no
# other: Client Hints and feature detection both reveal the truth, and a UA
# that disagrees with them is the cheapest bot check there is. Bump it when
# the Playwright pin in requirements.txt changes.
CHROMIUM_MAJOR = "121"
CHROMIUM_FULL = "121.0.6167.57"

# Real, common Windows desktop sizes. Not randomised ±50: a viewport of
# 1926×1099 belongs to nobody.
_VIEWPORTS: Tuple[Tuple[int, int], ...] = (
    (1920, 1080), (1366, 768), (1536, 864), (1440, 900), (1600, 900), (1280, 720),
)
# platformVersion "10.0.0" is Windows 10; "15.0.0" is how Chrome reports 11.
_WINDOWS: Tuple[Tuple[str, str], ...] = (("10.0.0", "10"), ("15.0.0", "11"))
_ACCEPT_LANGUAGES: Tuple[str, ...] = (
    "fa-IR,fa;q=0.9,en-US;q=0.8,en;q=0.7",
    "fa-IR,fa;q=0.9,en;q=0.8",
    "fa,en-US;q=0.9,en;q=0.8",
)


@dataclass(frozen=True)
class Device:
    """One person's browser, derived from one account. Stable across runs and
    across browser recycles, distinct between accounts."""
    width: int
    height: int
    platform_version: str
    accept_language: str

    @property
    def user_agent(self) -> str:
        return (f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                f"(KHTML, like Gecko) Chrome/{CHROMIUM_MAJOR}.0.0.0 Safari/537.36")

    def cdp_override(self) -> Dict[str, Any]:
        """The Emulation.setUserAgentOverride payload. The metadata is what
        makes Sec-CH-UA and navigator.userAgentData agree with the header."""
        brands = [{"brand": "Not A(Brand", "version": "99"},
                  {"brand": "Google Chrome", "version": CHROMIUM_MAJOR},
                  {"brand": "Chromium", "version": CHROMIUM_MAJOR}]
        full = [{"brand": "Not A(Brand", "version": "99.0.0.0"},
                {"brand": "Google Chrome", "version": CHROMIUM_FULL},
                {"brand": "Chromium", "version": CHROMIUM_FULL}]
        return {
            "userAgent": self.user_agent,
            "platform": "Win32",
            "acceptLanguage": self.accept_language,
            "userAgentMetadata": {
                "brands": brands, "fullVersionList": full,
                "platform": "Windows", "platformVersion": self.platform_version,
                "architecture": "x86", "model": "", "mobile": False,
                "bitness": "64", "wow64": False,
            },
        }

    @classmethod
    def for_account(cls, account: Optional[str]) -> "Device":
        """Deterministic: the same phone number always yields the same device.

        No account (the anonymous probe, single-URL scrapes) gets a fixed
        default rather than a random one, for the same reason — a device that
        changes every visit is itself a signal.
        """
        seed = int(hashlib.sha256((account or "").encode("utf-8")).hexdigest()[:8], 16)
        w, h = _VIEWPORTS[seed % len(_VIEWPORTS)]
        pv, _ = _WINDOWS[(seed >> 8) % len(_WINDOWS)]
        al = _ACCEPT_LANGUAGES[(seed >> 16) % len(_ACCEPT_LANGUAGES)]
        return cls(width=w, height=h, platform_version=pv, accept_language=al)


@dataclass
class StealthConfig:
    """Pace and limits. The fingerprint no longer lives here — see Device."""

    # Locale and timezone (Iran)
    locale: str = "fa-IR"
    timezone_id: str = "Asia/Tehran"

    # Geolocation (Tehran default)
    geolocation: Dict[str, float] = field(default_factory=lambda: {
        "latitude": 35.6892, "longitude": 51.3890, "accuracy": 100,
    })
    device_scale_factor: float = 1.0

    # Delay settings (seconds)
    # کمی کاهش داده شده برای سرعت بیشتر ولی همچنان شبیه کاربر واقعی
    # Between actions against Divar.
    #
    # These were 0.35/0.9 while app/config.py declared SCRAPER_DELAY_MIN=2.0 and
    # SCRAPER_DELAY_MAX=5.0 — and nothing read those settings, so the operator
    # knob did nothing and the real pace was five times what it claimed. The
    # defaults now match the documented ones, and __post_init__ below actually
    # reads the setting.
    min_delay: float = 2.0
    max_delay: float = 5.0
    page_load_delay: float = 0.6
    scroll_delay: float = 0.12
    click_delay: float = 0.2
    typing_delay: float = 0.08

    # Scroll settings
    scroll_steps: int = 2
    scroll_distance_min: int = 100
    scroll_distance_max: int = 500
    
    # Request limits
    max_requests_per_minute: int = 20
    # Chromium does not give memory back, so this is a memory ceiling wearing a
    # request count. 500 never fired before a 2Gi pod ran out — the real guard
    # is the cgroup check in _check_rate_limit, and this is the backstop for
    # hosts where that file cannot be read.
    max_requests_per_session: int = 1000
    
    def get_random_delay(self) -> float:
        """Get a random delay between min and max"""
        return random.uniform(self.min_delay, self.max_delay)
    
    def __post_init__(self):
        """Let the documented environment settings actually take effect.

        SCRAPER_DELAY_MIN / SCRAPER_DELAY_MAX have existed in app/config.py and
        in the README for months and were read by nothing, so anyone who turned
        the pace down to be kinder to Divar changed nothing at all.
        """
        try:
            from app.config import get_settings
            cfg = get_settings()
            lo = float(getattr(cfg, "scraper_delay_min", self.min_delay) or self.min_delay)
            hi = float(getattr(cfg, "scraper_delay_max", self.max_delay) or self.max_delay)
            if lo > 0 and hi >= lo:
                self.min_delay, self.max_delay = lo, hi
        except Exception:
            # A scraper that will not start because a setting could not be read
            # is worse than one running on its defaults.
            pass

    def get_random_scroll_distance(self) -> int:
        """Get a random scroll distance"""
        return random.randint(self.scroll_distance_min, self.scroll_distance_max)


def get_browser_args(headless: bool = True) -> List[str]:
    """Chromium launch flags: what a container needs, plus the one that matters.

    The previous list carried --disable-web-security (turns off CORS — a
    security hole and a behavioural tell), --ignore-certificate-errors, and a
    dozen site-isolation and feature flags from an old gist. None made the
    browser look more real; several made it look less.

    --headless=new is the real Chrome code path. It must be paired with
    headless=False at launch: Playwright 1.41 maps headless=True to the OLD
    mode, which says HeadlessChrome in the UA and the Client Hints and has no
    plugins and no window.chrome.
    """
    args = [
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--password-store=basic",
        "--use-mock-keychain",
        "--lang=fa-IR",
    ]
    if headless:
        args.insert(0, "--headless=new")
    return args


def get_context_options(stealth_config: StealthConfig, proxy=None,
                        device: Optional[Device] = None) -> Dict[str, Any]:
    """Browser context options.

    No `user_agent` here, deliberately. Playwright implements that option as a
    UA override with no userAgentMetadata, so Chromium stops sending Sec-CH-UA
    and navigator.userAgentData.brands comes back empty — a Chrome UA with no
    Client Hints, which real Chrome never produces. The UA is set once the page
    exists, over CDP, with metadata (apply_device).

    No bypass_csp and no ignore_https_errors: a real browser does neither.

    Only headers that are genuinely identical on every request go in
    extra_http_headers — Playwright applies them to EVERY request the context
    makes, so per-request headers (Sec-Fetch-*, Cache-Control) must be left to
    Chromium, which computes them correctly.
    """
    d = device or Device.for_account(None)
    options = {
        "viewport": {"width": d.width, "height": d.height},
        "locale": stealth_config.locale,
        "timezone_id": stealth_config.timezone_id,
        "geolocation": stealth_config.geolocation,
        "permissions": ["geolocation"],
        "device_scale_factor": stealth_config.device_scale_factor,
        "is_mobile": False,
        "has_touch": False,
        "java_script_enabled": True,
        "extra_http_headers": {"Accept-Language": d.accept_language},
    }
    if proxy:
        options["proxy"] = proxy if isinstance(proxy, dict) else {"server": proxy}
    return options


async def apply_device(page, device: Device) -> None:
    """Make this page present as `device`, consistently across header, Client
    Hints and JavaScript. Must run on every new page, including after a
    browser recycle — a recycled browser that forgot its device is a person
    whose laptop changed between two clicks.

    The CDP session is kept for the life of the page, on purpose. An
    emulation override belongs to the session that set it, and Chromium
    reverts it the moment that session detaches — measured: with a detach
    here, Divar received HeadlessChrome/121 on Linux, exactly as if nothing
    had been sent. The session is parked on the page object so it is released
    with the page and not before.
    """
    prev = getattr(page, "_sorinflow_cdp", None)
    if prev is not None:
        # Re-presenting the same page (a rotation): reuse the session rather
        # than stacking a second one.
        cdp = prev
    else:
        cdp = await page.context.new_cdp_session(page)
        page._sorinflow_cdp = cdp
    await cdp.send("Emulation.setUserAgentOverride", device.cdp_override())
    page._sorinflow_device = device


# One Chromium may hold a user_data_dir at a time. Two jobs on the same
# account would otherwise fail inside Chromium with an unreadable error.
_PROFILES_IN_USE: set = set()


def profile_dir(account: Optional[str]) -> "Path":
    """Where this account's browser profile lives, on the PVC.

    /app/data is the persistent volume, so a profile outlives the pod. That is
    the point: Divar's «this device already verified» lives in localStorage,
    IndexedDB and a device id inside the profile, not in the cookie jar, and a
    fresh profile carrying old cookies reads as a known account on an unknown
    machine — which is exactly when it asks for a code.
    """
    from pathlib import Path
    base = Path(os.environ.get("SCRAPER_PROFILE_DIR", "/app/data/profiles"))
    safe = "".join(c for c in (account or "") if c.isalnum()) or "_anonymous"
    return base / safe


async def open_browser(playwright, *, headless: bool, proxy=None,
                       account: Optional[str] = None,
                       stealth_config: Optional[StealthConfig] = None):
    """Open this account's PERSISTENT browser and present as its device.

    Every launch site goes through here, so the three of them cannot drift
    apart again — they had.

    Persistent, not a fresh context, and that is the whole point. Measured on
    the live pod: a run recycled the browser at 15:34:04, restored the cookie
    jar successfully at 15:34:08, and Divar demanded an SMS code at 15:34:37.
    The cookies were right; the device was new. Worse, `initialize()` opens a
    browser per run, so one account was three different machines in one
    afternoon.

    Returns (browser_or_none, context, page, device). A persistent context has
    no separate Browser object — `context.browser` is None — so callers must
    judge liveness from the context, never from the browser handle.
    """
    sc = stealth_config or StealthConfig()
    device = Device.for_account(account)
    udd = profile_dir(account)
    udd.mkdir(parents=True, exist_ok=True)

    key = str(udd)
    if key in _PROFILES_IN_USE:
        raise RuntimeError(
            f"profile {key} is already open in this process — two jobs cannot "
            f"share one account's browser profile")

    # A crash leaves Chromium's SingletonLock behind and the next launch hangs
    # on it. The lock names the pid that took it; if that process is gone the
    # lock is a leftover and removing it is correct.
    for stale in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        lk = udd / stale
        if lk.is_symlink() or lk.exists():
            try:
                lk.unlink()
            except OSError:
                pass

    opts = get_context_options(sc, proxy, device)
    _PROFILES_IN_USE.add(key)
    try:
        context = await playwright.chromium.launch_persistent_context(
            str(udd),
            # Always headless=False at the Playwright layer. When `headless` is
            # wanted the --headless=new flag provides it (real Chrome); when it
            # is not, the window is simply shown. Playwright's own
            # headless=True is the OLD mode and must never be used — see
            # get_browser_args.
            headless=False,
            args=get_browser_args(headless=headless),
            **opts,
        )
    except Exception:
        _PROFILES_IN_USE.discard(key)
        raise

    # A persistent context opens with one page already.
    page = context.pages[0] if context.pages else await context.new_page()
    await apply_device(page, device)

    # So close_context() can release the guard without re-deriving the path.
    context._sorinflow_profile_key = key
    return context.browser, context, page, device


async def close_context(context) -> None:
    """Close a persistent context and release its profile guard.

    Closing the context closes the browser too — there is no separate handle
    to close, and calling .close() on the None that `context.browser` returns
    is how this change would break the recycle path.
    """
    key = getattr(context, "_sorinflow_profile_key", None)
    try:
        await context.close()
    finally:
        if key:
            _PROFILES_IN_USE.discard(key)


def context_alive(context) -> bool:
    """Whether a (possibly persistent) context is still usable."""
    if context is None:
        return False
    try:
        # An open context has a pages list; a closed one raises or is empty of
        # usable pages. browser is None for persistent contexts, so it cannot
        # be the test.
        return any(not p.is_closed() for p in context.pages)
    except Exception:
        return False
