"""
Chromium's own sandbox (app/scraper/stealth.py, CHROMIUM_SANDBOX in
app/config.py).

The container runs as pwuser (uid 1000), not root, so --no-sandbox — the
default every "Chromium in Docker" guide copies — was giving up a real
defence-in-depth layer for nothing. `auto` (the default) asks for the
sandbox unless running as root, which cannot use it at all; if the runtime
still refuses it, one retry without it, logged loudly, remembered for the
rest of the process. `on`/`off` skip that decision entirely.

Measured on k3d with the production k3s version and the Playwright 1.41
image as uid 1000: a kernel/seccomp policy that forbids the sandbox makes
Chromium abort with "No usable sandbox! ..." (surfaced inside Playwright's
launch error); a permissive policy launches sandboxed as non-root with no
error. `_FakeChromium` below stands in for both.
"""
import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_sandbox.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.scraper import stealth as st          # noqa: E402
from app.config import get_settings            # noqa: E402
from _fake_redis import fake_server, redis_factory  # noqa: E402

_PROFILE_ROOT = "/tmp/sorinflow-test-sandbox-profiles"


class _FakeCdp:
    async def send(self, *a, **kw):
        pass


class _FakePage:
    def __init__(self):
        self.context = self

    async def new_cdp_session(self, page):
        return _FakeCdp()

    def is_closed(self):
        return False


class _FakeContext:
    def __init__(self):
        self.pages = [_FakePage()]
        self.browser = None

    async def new_page(self):
        return _FakePage()

    async def close(self):
        pass


class _FakeChromium:
    """launch_persistent_context that fails its first `fail_times` calls
    with `fail_text`, then succeeds. Records the `chromium_sandbox` kwarg
    of every attempt, which is the one thing these tests are about."""

    def __init__(self, fail_times=0, fail_text="No usable sandbox! see docs"):
        self.calls = []
        self.fail_times = fail_times
        self.fail_text = fail_text

    async def launch_persistent_context(self, path, **kw):
        self.calls.append(kw.get("chromium_sandbox"))
        if len(self.calls) <= self.fail_times:
            raise RuntimeError(self.fail_text)
        return _FakeContext()


class _FakePlaywright:
    def __init__(self, chromium):
        self.chromium = chromium


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    """Redis for the profile lock open_browser also takes, a real euid to
    restore afterwards, a scratch profile directory, and a clean sandbox
    fallback state — each test decides its own CHROMIUM_SANDBOX and euid."""
    monkeypatch.setattr(st, "get_redis", redis_factory(fake_server()), raising=True)
    monkeypatch.setenv("SCRAPER_PROFILE_DIR", _PROFILE_ROOT)
    shutil.rmtree(_PROFILE_ROOT, ignore_errors=True)
    st._sandbox_state.update(mode=None, active=None, fallback_reason=None)
    real_geteuid = os.geteuid
    yield real_geteuid
    os.geteuid = real_geteuid
    shutil.rmtree(_PROFILE_ROOT, ignore_errors=True)


async def _open(account, chromium, monkeypatch, mode="auto", euid=1000):
    cfg = get_settings()
    monkeypatch.setattr(cfg, "chromium_sandbox", mode, raising=False)
    monkeypatch.setattr(os, "geteuid", lambda: euid)
    pw = _FakePlaywright(chromium)
    browser, ctx, page, device = await st.open_browser(pw, headless=True, account=account)
    return ctx


class TestAutoMode:

    async def test_non_root_sandboxes_on_the_first_try(self, monkeypatch):
        chromium = _FakeChromium(fail_times=0)
        ctx = await _open("09130001111", chromium, monkeypatch, mode="auto", euid=1000)
        assert chromium.calls == [True]
        assert st.sandbox_status() == {"mode": "auto", "active": True, "fallback_reason": None}
        await st.close_context(ctx)

    async def test_root_never_even_tries_the_sandbox(self, monkeypatch):
        chromium = _FakeChromium(fail_times=0)
        ctx = await _open("09130002222", chromium, monkeypatch, mode="auto", euid=0)
        assert chromium.calls == [False]
        await st.close_context(ctx)

    async def test_a_sandbox_failure_falls_back_once(self, monkeypatch):
        chromium = _FakeChromium(fail_times=1,
                                 fail_text="No usable sandbox! Update your kernel or see "
                                          "https://chromium.googlesource.com/.../suid_sandbox_"
                                          "development.md")
        ctx = await _open("09130003333", chromium, monkeypatch, mode="auto", euid=1000)
        assert chromium.calls == [True, False]
        status = st.sandbox_status()
        assert status["active"] is False
        assert "No usable sandbox" in status["fallback_reason"]
        await st.close_context(ctx)

    async def test_the_fallback_is_remembered_for_the_process(self, monkeypatch):
        """Once auto has learned this process cannot sandbox, it must not
        pay for (and log) the same failure again on every later launch."""
        first = _FakeChromium(fail_times=1, fail_text="No usable sandbox!")
        ctx1 = await _open("09130004444", first, monkeypatch, mode="auto", euid=1000)
        await st.close_context(ctx1)

        second = _FakeChromium(fail_times=0)
        ctx2 = await _open("09130005555", second, monkeypatch, mode="auto", euid=1000)
        assert second.calls == [False], "a remembered fallback must skip straight to unsandboxed"
        await st.close_context(ctx2)

    async def test_a_non_sandbox_failure_is_not_swallowed(self, monkeypatch):
        """auto must only retry for a sandbox-shaped error — anything else
        (a bad proxy, a missing binary) has to reach the caller unchanged."""
        chromium = _FakeChromium(fail_times=99, fail_text="net::ERR_CONNECTION_REFUSED")
        with pytest.raises(RuntimeError, match="ERR_CONNECTION_REFUSED"):
            await _open("09130006666", chromium, monkeypatch, mode="auto", euid=1000)
        assert chromium.calls == [True], "only one attempt for a non-sandbox failure"

    async def test_the_profile_lock_is_released_when_every_attempt_fails(self, monkeypatch):
        chromium = _FakeChromium(fail_times=99, fail_text="No usable sandbox!")
        with pytest.raises(RuntimeError, match="No usable sandbox"):
            await _open("09130007777", chromium, monkeypatch, mode="auto", euid=1000)
        assert await st.profile_in_use("09130007777") is False, \
            "a failed launch must not leave the profile locked"


class TestOnMode:

    async def test_on_sandboxes_even_as_root(self, monkeypatch):
        chromium = _FakeChromium(fail_times=0)
        ctx = await _open("09130008888", chromium, monkeypatch, mode="on", euid=0)
        assert chromium.calls == [True]
        await st.close_context(ctx)

    async def test_on_never_falls_back(self, monkeypatch):
        chromium = _FakeChromium(fail_times=99, fail_text="No usable sandbox!")
        with pytest.raises(RuntimeError, match="No usable sandbox"):
            await _open("09130009999", chromium, monkeypatch, mode="on", euid=1000)
        assert chromium.calls == [True], "mode=on must not retry unsandboxed"


class TestOffMode:

    async def test_off_never_sandboxes_even_as_non_root(self, monkeypatch):
        chromium = _FakeChromium(fail_times=0)
        ctx = await _open("09130000001", chromium, monkeypatch, mode="off", euid=1000)
        assert chromium.calls == [False]
        await st.close_context(ctx)


class TestSandboxStatus:

    async def test_active_is_none_before_any_launch(self):
        assert st.sandbox_status()["active"] is None

    def test_config_rejects_an_unknown_mode(self):
        from pydantic import ValidationError
        from app.config import Settings
        with pytest.raises(ValidationError):
            Settings(chromium_sandbox="sometimes")


class TestGetBrowserArgsNoLongerHardcodesIt:

    def test_no_sandbox_is_not_in_the_static_args(self):
        """Sandboxing is chromium_sandbox= at launch now, which Playwright
        turns into --no-sandbox itself when off — get_browser_args must not
        also carry a hard-coded copy that would fight that."""
        assert "--no-sandbox" not in st.get_browser_args(headless=True)
        assert "--no-sandbox" not in st.get_browser_args(headless=False)
