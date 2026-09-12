"""
The proxy pool — choosing, testing, and being honest about exits.

Three facts from the live system, 2026-09-12:

  * PROXY_ENABLED was not in the manifest. A proxy was added and tested in the
    panel and never carried a single request.
  * The one proxy configured reaches Divar — from a hosting provider in
    Reykjavik. The test said «working». For an Iranian classifieds site that is
    the least convincing exit a visitor can have.
  * One proxy was chosen for the whole run, so every account exited from the
    same address. Ten people, one IP.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_pp.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.services import proxy_pool  # noqa: E402


class _P:
    def __init__(self, url, country=None, hosting=None, ok=0, fail=0, rt=1.0):
        self.url = url; self.exit_country = country; self.is_hosting = hosting
        self.success_count = ok; self.fail_count = fail; self.avg_response_time = rt


class TestParsing:

    def test_the_three_formats(self):
        got = proxy_pool.parse_list("""
            # a comment
            1.1.1.1:8080
            2.2.2.2:1080:user:pass
            socks5://u:p@3.3.3.3:9050
            http://4.4.4.4:3128
            not a proxy
        """)
        assert [(g["address"], g["port"], g["protocol"], g["username"]) for g in got] == [
            ("1.1.1.1", 8080, "http", None),
            ("2.2.2.2", 1080, "http", "user"),
            ("3.3.3.3", 9050, "socks5", "u"),
            ("4.4.4.4", 3128, "http", None),
        ]

    def test_garbage_is_skipped_not_fatal(self):
        assert proxy_pool.parse_list("hello\n:::\n1.1.1.1:notaport") == []


class TestChoosing:

    @pytest.mark.asyncio
    async def test_an_account_always_gets_the_same_proxy(self, monkeypatch):
        pool = [_P("http://a"), _P("http://b"), _P("http://c")]
        async def _working(db): return pool
        monkeypatch.setattr(proxy_pool, "working", _working)
        first = await proxy_pool.pick_for_account(None, "09146382408")
        for _ in range(5):
            assert await proxy_pool.pick_for_account(None, "09146382408") == first

    @pytest.mark.asyncio
    async def test_the_real_pool_of_accounts_does_not_all_share_one(self, monkeypatch):
        pool = [_P("http://a"), _P("http://b"), _P("http://c")]
        async def _working(db): return pool
        monkeypatch.setattr(proxy_pool, "working", _working)
        accounts = ["09017852452", "09029315496", "09053833026", "09058432452", "09125005495",
                    "09145172065", "09146382408", "09190665165", "09362191758", "09982469110"]
        chosen = {await proxy_pool.pick_for_account(None, a) for a in accounts}
        assert len(chosen) >= 2, "ten accounts, one address — the thing this replaces"

    @pytest.mark.asyncio
    async def test_no_working_proxy_means_none_not_a_crash(self, monkeypatch):
        async def _working(db): return []
        monkeypatch.setattr(proxy_pool, "working", _working)
        assert await proxy_pool.pick_for_account(None, "0912") is None


class TestScoring:
    """An Iranian residential exit beats a foreign datacenter one whatever
    their success counts say."""

    def test_iran_beats_abroad(self):
        ir = _P("ir", "IR", False, ok=1)
        isl = _P("is", "IS", True, ok=100)
        assert sorted([isl, ir], key=proxy_pool._score, reverse=True)[0] is ir

    def test_residential_beats_hosting_within_iran(self):
        home = _P("h", "IR", False)
        dc = _P("d", "IR", True, ok=50)
        assert sorted([dc, home], key=proxy_pool._score, reverse=True)[0] is home


class TestItIsReachable:

    def test_the_flag_reaches_the_pod(self):
        text = open("k8s/04-backend.yaml", encoding="utf-8").read()
        assert "key: PROXY_ENABLED, optional: true" in text

    def test_the_refresh_loop_is_started(self):
        text = open("app/main.py", encoding="utf-8").read()
        assert "refresh_loop" in text and "proxy_task" in text

    def test_the_scraper_picks_per_account(self):
        import inspect
        from app.scraper.divar_scraper import DivarScraper
        src = inspect.getsource(DivarScraper._get_working_proxy)
        assert "pick_for_account" in src

    def test_the_panel_says_where_it_exits(self):
        js = open("frontend/js/app.js", encoding="utf-8").read()
        assert "_proxyExitCell" in js and "exit_country" in js
