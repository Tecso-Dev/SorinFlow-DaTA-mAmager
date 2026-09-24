"""
The nightly snapshot's copy off the server — issue #8.

The shipping code had been waiting on two environment variables nobody set.
Now the panel takes them (token encrypted), finds the chat, fires a shipment
on demand and shows the last outcome — so a broken offsite copy is a red
line on a screen, not a warning in a log.
"""
import os
import sys
import asyncio
import json
import re
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_bk.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.services import backup_service as bk   # noqa: E402
from app.services import secret_box              # noqa: E402


class FakeDb:
    """Just enough of secret_box's needs: a dict behind get_many/put."""
    def __init__(self):
        self.rows = {}


@pytest.fixture
def store(monkeypatch):
    db = FakeDb()

    async def get_many(_db, keys):
        return {k: db.rows[k] for k in keys if k in db.rows}

    async def put(_db, key, value, actor=""):
        db.rows[key] = value
    monkeypatch.setattr(secret_box, "get_many", get_many)
    monkeypatch.setattr(secret_box, "put", put)
    monkeypatch.setattr(bk.settings, "telegram_bot_token", "", raising=False)
    monkeypatch.setattr(bk.settings, "telegram_chat_id", "", raising=False)
    return db


class TestWhereTheCredentialsComeFrom:

    def test_nothing_set_means_not_configured(self, store):
        cfg = asyncio.run(bk.resolve_telegram(store))
        assert cfg == {"token": "", "chat_id": "", "source": None}

    def test_the_panel_values_are_used_and_the_token_is_encrypted_at_rest(self, store):
        store.rows[bk.KEY_TOKEN] = secret_box.encrypt("123456:abc")
        store.rows[bk.KEY_CHAT] = "42"
        assert "123456:abc" not in store.rows[bk.KEY_TOKEN]
        cfg = asyncio.run(bk.resolve_telegram(store))
        assert cfg == {"token": "123456:abc", "chat_id": "42", "source": "panel"}

    def test_the_environment_wins(self, store, monkeypatch):
        store.rows[bk.KEY_TOKEN] = secret_box.encrypt("panel:token")
        store.rows[bk.KEY_CHAT] = "1"
        monkeypatch.setattr(bk.settings, "telegram_bot_token", "env:token", raising=False)
        monkeypatch.setattr(bk.settings, "telegram_chat_id", "9", raising=False)
        cfg = asyncio.run(bk.resolve_telegram(store))
        assert cfg["token"] == "env:token" and cfg["chat_id"] == "9" and cfg["source"] == "env"


def _telegram(monkeypatch, handler):
    real = httpx.AsyncClient

    class Fake(real):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **kw)
    monkeypatch.setattr(bk.httpx, "AsyncClient", Fake)


class TestShipping:

    def test_a_delivery_is_recorded_as_such(self, store, monkeypatch, tmp_path):
        store.rows[bk.KEY_TOKEN] = secret_box.encrypt("123456:abc")
        store.rows[bk.KEY_CHAT] = "42"
        seen = {}

        def handler(req):
            seen["url"] = str(req.url)
            seen["body"] = req.read()
            return httpx.Response(200, json={"ok": True, "result": {}})
        _telegram(monkeypatch, handler)
        f = tmp_path / "sorinflow-backup-20260918-0000.json.gz"
        f.write_bytes(b"\x1f\x8b" + b"x" * 100)
        ok = asyncio.run(bk.send_to_telegram(f, store))
        assert ok is True
        assert seen["url"].endswith("/bot123456:abc/sendDocument")
        assert b'name="chat_id"' in seen["body"] and b"42" in seen["body"]
        last = json.loads(store.rows[bk.KEY_LAST])
        assert last["ok"] is True and last["file"] == f.name and last["error"] == ""
        # what Telegram received is the SEALED copy, and the plain bytes are
        # not in it — password hashes and Divar cookies do not leave in clear
        assert b".json.gz.enc" in seen["body"]
        assert b"\x1f\x8b" + b"x" * 100 not in seen["body"]
        assert not (tmp_path / (f.name + ".enc")).exists(), "the sealed temp file was left behind"

    def test_a_refusal_is_recorded_with_telegrams_reason(self, store, monkeypatch, tmp_path):
        store.rows[bk.KEY_TOKEN] = secret_box.encrypt("123456:abc")
        store.rows[bk.KEY_CHAT] = "42"
        _telegram(monkeypatch, lambda req: httpx.Response(
            400, json={"ok": False, "description": "Bad Request: chat not found"}))
        f = tmp_path / "b.json.gz"
        f.write_bytes(b"x" * 10)
        assert asyncio.run(bk.send_to_telegram(f, store)) is False
        last = json.loads(store.rows[bk.KEY_LAST])
        assert last["ok"] is False and "chat not found" in last["error"]

    def test_unconfigured_ships_nothing_and_says_so(self, store, tmp_path, monkeypatch):
        called = []
        _telegram(monkeypatch, lambda req: called.append(1) or httpx.Response(200, json={"ok": True}))
        f = tmp_path / "b.json.gz"
        f.write_bytes(b"x")
        assert asyncio.run(bk.send_to_telegram(f, store)) is False
        assert not called


class TestFindingTheChat:

    def test_the_bots_chats_are_listed_once_each(self, monkeypatch):
        def handler(req):
            if req.url.path.endswith("/getMe"):
                return httpx.Response(200, json={"ok": True, "result": {"username": "sorinflow_backup_bot"}})
            return httpx.Response(200, json={"ok": True, "result": [
                {"message": {"chat": {"id": 111, "type": "private", "first_name": "سبحان", "last_name": "عظیم‌زاده"}}},
                {"message": {"chat": {"id": 111, "type": "private", "first_name": "سبحان"}}},
                {"channel_post": {"chat": {"id": -100222, "type": "channel", "title": "بکاپ‌ها"}}},
            ]})
        _telegram(monkeypatch, handler)
        info = asyncio.run(bk.telegram_probe("123456:abc"))
        assert info["bot"] == "sorinflow_backup_bot"
        assert info["chats"] == [
            {"id": "111", "name": "سبحان عظیم‌زاده", "type": "private"},
            {"id": "-100222", "name": "بکاپ‌ها", "type": "channel"},
        ]

    def test_a_bad_token_is_named_not_swallowed(self, monkeypatch):
        _telegram(monkeypatch, lambda req: httpx.Response(
            401, json={"ok": False, "description": "Unauthorized"}))
        with pytest.raises(ValueError):
            asyncio.run(bk.telegram_probe("bad"))


class TestItIsOnThePanel:

    def test_the_routes_are_for_super_admin_only(self):
        src = Path("app/api/routes/backup.py").read_text(encoding="utf-8")
        assert "_role_dep(ROLE_ROOT, ROLE_SUPER_ADMIN)" in src
        for route in ("/status", "/settings", "/probe", "/run"):
            assert f'"{route}"' in src
        assert "_super_admin" in src
        # the bare endpoint that bypassed the router is gone
        assert '@app.post("/api/backup/run")' not in Path("app/main.py").read_text(encoding="utf-8")

    def test_the_token_never_travels_back_in_the_clear(self):
        src = Path("app/api/routes/backup.py").read_text(encoding="utf-8")
        assert "secret_box.mask(cfg[\"token\"])" in src
        assert '"token":' not in src.split("async def backup_status")[1].split("@router")[0]

    def test_the_card_exists_and_loads_with_the_admin_section(self):
        html = Path("frontend/index.html").read_text(encoding="utf-8")
        js = Path("frontend/js/app.js").read_text(encoding="utf-8")
        assert 'id="backup-card"' in html and 'onclick="bkProbe()"' in html and 'onclick="bkRunNow()"' in html
        assert "loadBackup();" in js.split("case 'users':")[1][:300]
        css = Path("frontend/css/style.css").read_text(encoding="utf-8")
        assert "var(--input-bg)" in css[css.index(".bk-stat"):]

    def test_the_nightly_run_records_its_outcome_too(self):
        src = Path("app/services/backup_service.py").read_text(encoding="utf-8")
        body = src[src.index("async def run_backup"):]
        assert "async_session_maker() as own" in body, "the scheduler's run had no session to record with"


class TestTheSealedCopy:

    def test_it_round_trips_under_the_servers_key(self, tmp_path):
        import gzip
        f = tmp_path / "sorinflow-backup-x.json.gz"
        f.write_bytes(gzip.compress(b'{"tables": {"users": []}}'))
        sealed = bk.seal(f)
        assert sealed.name.endswith(".json.gz.enc")
        assert sealed.read_bytes() != f.read_bytes()
        assert bk.unseal(sealed) == f.read_bytes()

    def test_the_restore_opens_it_and_knows_every_table(self, tmp_path, monkeypatch):
        """The README once found the restore importing 7 of the model modules
        — app_settings, the portal and the email log were silently dropped."""
        import gzip
        import runpy
        monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///./_rs_test.db")
        ns = runpy.run_path("scripts/restore_backup.py", run_name="not_main")
        from app.database import Base
        names = {t.name for t in Base.metadata.sorted_tables}
        for need in ("app_settings", "email_logs", "sms_events", "forwarder_devices", "users", "properties"):
            assert need in names, need
        f = tmp_path / "b.json.gz"
        f.write_bytes(gzip.compress(b'{"created_at": "t", "tables": {"users": [{"id": 1}]}}'))
        sealed = bk.seal(f)
        assert ns["load_payload"](sealed)["tables"]["users"] == [{"id": 1}]
        assert ns["load_payload"](f)["created_at"] == "t"


# ── the proxy ────────────────────────────────────────────────────────────────
# The server is in Iran and api.telegram.org is blocked there. Sobhan entered
# the token and the chat and watched «در حال پرسیدن از تلگرام…» never finish.
# Every Bot API call now goes through a proxy: environment first, then the
# panel, encrypted at rest because the URL carries the proxy's password.

class TestTheProxy:

    def test_nothing_set_means_direct(self, store, monkeypatch):
        monkeypatch.setattr(bk.settings, "telegram_proxy", "", raising=False)
        assert asyncio.run(bk.resolve_proxy(store)) == ""

    def test_the_panel_value_is_used_and_encrypted_at_rest(self, store, monkeypatch):
        monkeypatch.setattr(bk.settings, "telegram_proxy", "", raising=False)
        store.rows[bk.KEY_PROXY] = secret_box.encrypt("socks5://u:p@proxy.example:1080")
        assert "socks5://u:p@proxy.example:1080" not in store.rows[bk.KEY_PROXY]
        assert asyncio.run(bk.resolve_proxy(store)) == "socks5://u:p@proxy.example:1080"

    def test_the_environment_wins(self, store, monkeypatch):
        monkeypatch.setattr(bk.settings, "telegram_proxy", "http://env-proxy:3128", raising=False)
        store.rows[bk.KEY_PROXY] = secret_box.encrypt("socks5://panel:1080")
        assert asyncio.run(bk.resolve_proxy(store)) == "http://env-proxy:3128"

    def test_only_proxy_urls_are_accepted(self):
        for ok in ("http://1.2.3.4:3128", "socks5://user:pa%40ss@host.example:1080", "https://proxy.example"):
            assert bk.valid_proxy(ok), ok
        for bad in ("ftp://x:1", "1.2.3.4:3128", "socks5://", "http://host:port", "javascript:alert(1)"):
            assert not bk.valid_proxy(bad), bad

    def test_every_call_is_made_through_it(self, monkeypatch):
        # the fallback leg on its own — direct-first is TestDirectFirst's
        monkeypatch.setattr(bk.settings, "telegram_direct_first", "0", raising=False)
        seen = []
        real = httpx.AsyncClient

        class Fake(real):
            def __init__(self, *a, **kw):
                seen.append(kw.get("proxy"))
                kw.pop("proxy", None)
                kw["transport"] = httpx.MockTransport(lambda req: httpx.Response(
                    200, json={"ok": True, "result": {"username": "b", "id": 1} if "getMe" in str(req.url) else []}))
                super().__init__(*a, **kw)
        monkeypatch.setattr(bk.httpx, "AsyncClient", Fake)
        asyncio.run(bk.telegram_probe("1:x", "socks5://p:1080"))     # getMe + getUpdates
        asyncio.run(bk.telegram_ping("1:x", "http://q:3128"))
        assert seen == ["socks5://p:1080", "socks5://p:1080", "http://q:3128"]

    def test_a_shipment_without_a_proxy_that_fails_says_why(self, store, monkeypatch, tmp_path):
        monkeypatch.setattr(bk.settings, "telegram_proxy", "", raising=False)
        store.rows[bk.KEY_TOKEN] = secret_box.encrypt("1:x")
        store.rows[bk.KEY_CHAT] = "5"
        _telegram(monkeypatch, lambda req: (_ for _ in ()).throw(httpx.ConnectTimeout("blocked")))
        f = tmp_path / "sorinflow-backup-x.json.gz"
        f.write_bytes(b"x")
        assert asyncio.run(bk.send_to_telegram(f, store)) is False
        assert "پراکسی" in json.loads(store.rows[bk.KEY_LAST])["error"]

    def test_the_routes_take_and_mask_it(self):
        src = Path("app/api/routes/backup.py").read_text(encoding="utf-8")
        assert "proxy: Optional[str] = Field(None, max_length=300)" in src
        assert "secret_box.encrypt(proxy)" in src, "stored encrypted, like the token"
        assert '@router.post("/proxy-test")' in src
        assert '"route_label": bk.describe_route(route)' in src
        assert bk.mask_url("socks5://u:p@h:1") == "socks5://***@h:1"

    def test_the_crm_notifier_uses_the_same_proxy(self):
        src = Path("app/crm/notification.py").read_text(encoding="utf-8")
        assert 'tg_request(token, "sendMessage", await resolve_route(), timeout=10' in src
        assert "httpx.AsyncClient(" not in src[src.index("async def send_telegram"):src.index("def _sync_send_email")]

    def test_the_panel_has_the_field_and_socks_is_installable(self):
        html = Path("frontend/index.html").read_text(encoding="utf-8")
        js = Path("frontend/js/app.js").read_text(encoding="utf-8")
        assert 'id="bk-proxy"' in html and 'onclick="bkProxyTest()"' in html
        assert "async function bkProxyTest" in js and "bkClearProxy" in js
        assert "const body = { chat_id: chat, ..._bkRouteBody() };" in js
        assert "socksio" in Path("requirements.txt").read_text(encoding="utf-8")


class TestSeveralChats:
    """«این چت‌آیدی را هم اضافه کن»: one setting, several recipients, each
    addressed on its own — a dead id costs nobody else their copy."""

    def test_the_setting_is_a_list(self):
        assert bk.chat_ids("542901635, 133142359") == ["542901635", "133142359"]
        assert bk.chat_ids("542901635،-1001234567890") == ["542901635", "-1001234567890"]
        assert bk.chat_ids(" ") == [] and bk.chat_ids("abc, 12") == ["12"]

    def test_each_chat_gets_the_file_and_a_dead_one_does_not_sink_the_shipment(self, store, monkeypatch, tmp_path):
        store.rows[bk.KEY_TOKEN] = secret_box.encrypt("123456:abc")
        store.rows[bk.KEY_CHAT] = "542901635, 133142359"
        monkeypatch.setattr(bk.settings, "telegram_proxy", "", raising=False)
        seen = []

        def handler(req):
            body = req.read()
            chat = "542901635" if b"542901635" in body else "133142359"
            seen.append(chat)
            if chat == "133142359":
                return httpx.Response(400, json={"ok": False, "description": "Bad Request: chat not found"})
            return httpx.Response(200, json={"ok": True, "result": {}})
        _telegram(monkeypatch, handler)
        f = tmp_path / "sorinflow-backup-x.json.gz"
        f.write_bytes(b"x" * 10)
        assert asyncio.run(bk.send_to_telegram(f, store)) is True
        assert seen == ["542901635", "133142359"]
        last = json.loads(store.rows[bk.KEY_LAST])
        assert last["ok"] is True and last["delivered"] == ["542901635"] and "chat not found" in last["error"]

    def test_the_route_saves_the_list_and_refuses_junk(self):
        src = Path("app/api/routes/backup.py").read_text(encoding="utf-8")
        assert 'await secret_box.put(db, bk.KEY_CHAT, ", ".join(ids) or None, actor)' in src
        assert '"chat_ids": bk.chat_ids(cfg["chat_id"])' in src
        js = Path("frontend/js/app.js").read_text(encoding="utf-8")
        assert "function bkPickChat" in js and "have.join(', ')" in js


class TestTheThreeWaysOut:
    """«از پراکسی‌های داشبورد هم بشود انتخاب کرد، بالانس هم بشود» and «رلهٔ
    Cloudflare را هم به عنوان راه جایگزین اضافه کن»: one route, three modes."""

    def _rows(self, **kw):
        return {k: v for k, v in kw.items()}

    def test_the_manual_route_is_the_typed_url(self, store, monkeypatch):
        monkeypatch.setattr(bk.settings, "telegram_proxy", "", raising=False)
        monkeypatch.setattr(bk.settings, "telegram_api_base", "", raising=False)
        store.rows[bk.KEY_PROXY] = secret_box.encrypt("http://u:p@h:9990")
        r = asyncio.run(bk.resolve_route(store))
        assert r["mode"] == "manual" and r["proxies"] == ["http://u:p@h:9990"] and r["api_base"] == bk.TELEGRAM_API
        assert bk.describe_route(r) == "http://***@h:9990"

    def test_the_relay_route_goes_direct_to_the_worker_with_its_key(self, store, monkeypatch):
        monkeypatch.setattr(bk.settings, "telegram_proxy", "", raising=False)
        monkeypatch.setattr(bk.settings, "telegram_api_base", "", raising=False)
        store.rows[bk.KEY_PROXY_MODE] = "relay"
        store.rows[bk.KEY_RELAY] = "https://tg.sorinflow.example"
        store.rows[bk.KEY_RELAY_KEY] = secret_box.encrypt("k3y")
        r = asyncio.run(bk.resolve_route(store))
        assert r == {"mode": "relay", "proxies": [], "api_base": "https://tg.sorinflow.example", "relay_key": "k3y"}
        assert bk.valid_relay("https://tg.sorinflow.example") and not bk.valid_relay("http://x") and not bk.valid_relay("https://x/path")

    def test_the_pool_rotates_and_fails_over(self, store, monkeypatch):
        monkeypatch.setattr(bk.settings, "telegram_direct_first", "0", raising=False)
        monkeypatch.setattr(bk.settings, "telegram_proxy", "", raising=False)
        monkeypatch.setattr(bk.settings, "telegram_api_base", "", raising=False)
        store.rows[bk.KEY_PROXY_MODE] = "pool"
        store.rows[bk.KEY_PROXY_POOL] = "*"

        async def pool_urls(db, spec):
            return ["http://a:1", "http://b:2", "http://c:3"]
        monkeypatch.setattr(bk, "_pool_urls", pool_urls)
        first = asyncio.run(bk.resolve_route(store))["proxies"]
        second = asyncio.run(bk.resolve_route(store))["proxies"]
        assert set(first) == {"http://a:1", "http://b:2", "http://c:3"} and first != second, "each call starts one further along"
        assert second[0] == first[1]

        # a proxy that does not answer is skipped for the next one; Telegram's answer counts whatever it is
        tried = []
        real = httpx.AsyncClient

        class Fake(real):
            def __init__(self, *a, **kw):
                self._p = kw.pop("proxy", None)
                tried.append(self._p)
                def handler(req):
                    if self._p == "http://a:1":
                        raise httpx.ConnectTimeout("dead")
                    return httpx.Response(200, json={"ok": True, "result": {"username": "b"}})
                kw["transport"] = httpx.MockTransport(handler)
                super().__init__(*a, **kw)
        monkeypatch.setattr(bk.httpx, "AsyncClient", Fake)
        resp, used = asyncio.run(bk.tg_request("1:x", "getMe", bk._route("pool", ["http://a:1", "http://b:2"])))
        assert resp.status_code == 200 and used == "http://b:2" and tried == ["http://a:1", "http://b:2"]

    def test_the_relay_key_travels_as_a_header(self, monkeypatch):
        monkeypatch.setattr(bk.settings, "telegram_direct_first", "0", raising=False)
        seen = {}
        real = httpx.AsyncClient

        class Fake(real):
            def __init__(self, *a, **kw):
                kw.pop("proxy", None)
                def handler(req):
                    seen["url"] = str(req.url); seen["key"] = req.headers.get("X-Relay-Key")
                    return httpx.Response(200, json={"ok": True, "result": {"username": "b"}})
                kw["transport"] = httpx.MockTransport(handler)
                super().__init__(*a, **kw)
        monkeypatch.setattr(bk.httpx, "AsyncClient", Fake)
        r = asyncio.run(bk.telegram_ping("1:x", route=bk._route("relay", [], "https://tg.example", "k3y")))
        assert seen["url"] == "https://tg.example/bot1:x/getMe" and seen["key"] == "k3y" and r["via"] == "رله https://tg.example"

    def test_the_panel_offers_the_three_and_the_worker_code(self):
        html = Path("frontend/index.html").read_text(encoding="utf-8")
        js = Path("frontend/js/app.js").read_text(encoding="utf-8")
        for v in ("manual", "pool", "relay"):
            assert f'name="bk-mode" value="{v}"' in html
        assert 'id="bk-pool-list"' in html and 'id="bk-relay"' in html and 'id="bk-relay-code"' in html
        # the Worker is not copied into the page: it is fetched from the one
        # deploy/telegram-relay tests (see TestTheFullBackupCard)
        assert "apiCall('/backup/relay-worker', { raw: true })" in js
        assert "apiCall('/proxies?active_only=true')" in js and "function _bkRouteBody" in js
        src = Path("app/api/routes/backup.py").read_text(encoding="utf-8")
        assert 'if route["mode"] == "pool":' in src and '"results": results' in src, "the pool is tested one proxy at a time"


class TestDirectFirst:
    """«راه اول برای ارسال به تلگرام از سرور ایرانی باید باشد و پراکسی راه
    جایگزین آن است.» api.telegram.org straight from the server first; the
    relay or the proxies only when that does not connect."""

    @pytest.fixture(autouse=True)
    def _fresh(self, monkeypatch):
        monkeypatch.setattr(bk, "_direct_down_until", 0.0)
        monkeypatch.setattr(bk.settings, "telegram_direct_first", "1", raising=False)

    def _fake(self, monkeypatch, direct_works):
        calls = []
        real = httpx.AsyncClient

        class Fake(real):
            def __init__(self, *a, **kw):
                self._p = kw.pop("proxy", None)
                def handler(req):
                    calls.append((str(req.url).split("/bot")[0], self._p, req.headers.get("X-Relay-Key")))
                    if str(req.url).startswith(bk.TELEGRAM_API) and self._p is None and not direct_works:
                        raise httpx.ConnectTimeout("filtered")
                    return httpx.Response(200, json={"ok": True, "result": {"username": "b"}})
                kw["transport"] = httpx.MockTransport(handler)
                super().__init__(*a, **kw)
        monkeypatch.setattr(bk.httpx, "AsyncClient", Fake)
        return calls

    def test_direct_is_tried_first_and_used_when_it_works(self, monkeypatch):
        calls = self._fake(monkeypatch, direct_works=True)
        resp, used = asyncio.run(bk.tg_request("1:x", "getMe", bk._route("relay", [], "https://tg.example", "k")))
        assert used == "direct" and calls == [(bk.TELEGRAM_API, None, None)]

    def test_the_relay_is_the_fallback(self, monkeypatch):
        calls = self._fake(monkeypatch, direct_works=False)
        resp, used = asyncio.run(bk.tg_request("1:x", "getMe", bk._route("relay", [], "https://tg.example", "k")))
        assert used == "relay"
        assert calls == [(bk.TELEGRAM_API, None, None), ("https://tg.example", None, "k")]

    def test_proxies_are_the_fallback_too(self, monkeypatch):
        calls = self._fake(monkeypatch, direct_works=False)
        resp, used = asyncio.run(bk.tg_request("1:x", "getMe", bk._route("pool", ["http://a:1"])))
        assert used == "http://a:1" and [c[1] for c in calls] == [None, "http://a:1"]

    def test_a_failed_direct_is_rested_so_polling_does_not_pay_it_every_time(self, monkeypatch):
        calls = self._fake(monkeypatch, direct_works=False)
        route = bk._route("relay", [], "https://tg.example", "")
        asyncio.run(bk.tg_request("1:x", "getMe", route))
        asyncio.run(bk.tg_request("1:x", "getMe", route))
        assert [c[0] for c in calls] == [bk.TELEGRAM_API, "https://tg.example", "https://tg.example"]

    def test_direct_connects_with_a_short_timeout(self):
        import inspect
        src = inspect.getsource(bk.tg_request)
        assert "connect=DIRECT_CONNECT_TIMEOUT" in src and bk.DIRECT_CONNECT_TIMEOUT <= 10

    def test_it_can_be_switched_off(self, monkeypatch):
        monkeypatch.setattr(bk.settings, "telegram_direct_first", "0", raising=False)
        calls = self._fake(monkeypatch, direct_works=True)
        _r, used = asyncio.run(bk.tg_request("1:x", "getMe", bk._route("relay", [], "https://tg.example", "")))
        assert used == "relay" and len(calls) == 1

    def test_with_nothing_configured_direct_is_the_only_way(self, monkeypatch):
        monkeypatch.setattr(bk.settings, "telegram_direct_first", "0", raising=False)
        calls = self._fake(monkeypatch, direct_works=True)
        _r, used = asyncio.run(bk.tg_request("1:x", "getMe", bk._route("manual", [])))
        assert used == "direct" and len(calls) == 1

    def test_diagnose_reports_every_way_separately(self, monkeypatch):
        self._fake(monkeypatch, direct_works=False)
        rows = asyncio.run(bk.diagnose("1:x", bk._route("relay", [], "https://tg.example", "k")))
        assert [r["route"] for r in rows] == ["direct", "relay"]
        assert rows[0]["ok"] is False and rows[0]["error"] == "ConnectTimeout"
        assert rows[1]["ok"] is True and rows[1]["http"] == 200


class TestTheRelaysOwnRefusals:
    """The Worker answers a wrong key, a bot it does not serve, or a Telegram
    it cannot reach with its own JSON ({"ok": false, "relay": ...}). Those
    used to come back as Telegram's own 401/403/502: no proxy was tried and
    the panel said «HTTP 401» as if the bot token were wrong."""

    @pytest.fixture(autouse=True)
    def _fresh(self, monkeypatch):
        monkeypatch.setattr(bk, "_direct_down_until", 0.0)
        monkeypatch.setattr(bk.settings, "telegram_direct_first", "1", raising=False)

    def _fake(self, monkeypatch, relay_answer):
        used = []
        real = httpx.AsyncClient

        class Fake(real):
            def __init__(self, *a, **kw):
                proxy = kw.pop("proxy", None)

                def handler(req):
                    url = str(req.url)
                    if url.startswith(bk.TELEGRAM_API) and proxy is None:
                        raise httpx.ConnectTimeout("filtered")
                    used.append(proxy or url.split("/bot")[0])
                    if url.startswith("https://tg.example"):
                        return relay_answer
                    return httpx.Response(200, json={"ok": True, "result": {"username": "b"}})
                kw["transport"] = httpx.MockTransport(handler)
                super().__init__(*a, **kw)
        monkeypatch.setattr(bk.httpx, "AsyncClient", Fake)
        return used

    def test_a_refusal_falls_through_to_the_proxies(self, monkeypatch):
        used = self._fake(monkeypatch, httpx.Response(401, json={"ok": False, "relay": "unauthorized"}))
        route = bk._route("relay", ["http://a:1"], "https://tg.example", "wrong")
        resp, via = asyncio.run(bk.tg_request("1:x", "getMe", route))
        assert via == "http://a:1" and resp.status_code == 200
        assert used == ["https://tg.example", "http://a:1"]

    def test_with_nowhere_left_it_says_what_the_relay_said(self, monkeypatch):
        self._fake(monkeypatch, httpx.Response(403, json={"ok": False, "relay": "forbidden_bot"}))
        route = bk._route("relay", [], "https://tg.example", "k")
        with pytest.raises(bk.RelayError, match="ALLOWED_BOTS"):
            asyncio.run(bk.tg_request("1:x", "getMe", route))

    def test_telegrams_own_401_still_reaches_the_caller(self, monkeypatch):
        tg401 = httpx.Response(401, json={"ok": False, "error_code": 401, "description": "Unauthorized"})
        self._fake(monkeypatch, tg401)
        route = bk._route("relay", ["http://a:1"], "https://tg.example", "k")
        resp, via = asyncio.run(bk.tg_request("1:x", "getMe", route))
        assert via == "relay" and resp.status_code == 401

    def test_the_route_test_names_the_relays_reason(self, monkeypatch):
        self._fake(monkeypatch, httpx.Response(401, json={"ok": False, "relay": "unauthorized"}))
        rows = asyncio.run(bk.diagnose("1:x", bk._route("relay", [], "https://tg.example", "wrong")))
        relay = next(r for r in rows if r["route"] == "relay")
        assert relay["ok"] is False and "X-Relay-Key" in relay["error"]


class TestEveryWayOut:
    """«تست همهٔ راه‌ها» tries every way the server knows of, whichever mode
    is in effect: straight, the relay, and each proxy on its own."""

    def test_it_gathers_the_relay_and_every_proxy_once(self, store, monkeypatch):
        monkeypatch.setattr(bk.settings, "telegram_api_base", "", raising=False)
        monkeypatch.setattr(bk.settings, "telegram_proxy", "", raising=False)
        store.rows[bk.KEY_PROXY_MODE] = "manual"            # the mode does not narrow it
        store.rows[bk.KEY_RELAY] = "https://tg.example"
        store.rows[bk.KEY_RELAY_KEY] = secret_box.encrypt("k3y")
        store.rows[bk.KEY_PROXY] = secret_box.encrypt("socks5://a:1")

        async def pool(_db, spec):
            return ["http://b:2", "socks5://a:1"]
        monkeypatch.setattr(bk, "_pool_urls", pool)
        route = asyncio.run(bk.every_way_out(store))
        assert route == {"mode": "relay", "proxies": ["socks5://a:1", "http://b:2"],
                         "api_base": "https://tg.example", "relay_key": "k3y"}
        legs = [leg[0] for leg in bk._legs(route, direct=True)]
        assert legs == ["direct", "relay", "socks5://a:1", "http://b:2"]


class TestTheFullBackupCard:
    """The panel's card for the host's nightly disaster-recovery bundle."""

    @pytest.fixture
    def dr(self, tmp_path, monkeypatch):
        from app.services import dr_backup as dr
        monkeypatch.setattr(dr, "STATUS", tmp_path / "dr-status.json")
        monkeypatch.setattr(dr, "REQUEST", tmp_path / "dr-request")
        monkeypatch.setattr(dr, "OUTBOX", tmp_path / "dr-outbox")
        return dr

    def _user(self):
        from types import SimpleNamespace
        return SimpleNamespace(username="root", role="root")

    def test_now_asks_the_host_once(self, dr):
        from fastapi import HTTPException
        from app.api.routes import backup as routes
        assert asyncio.run(routes.dr_run_now(self._user())) == {"requested": True}
        assert dr.REQUEST.exists()
        with pytest.raises(HTTPException) as e:
            asyncio.run(routes.dr_run_now(self._user()))
        assert e.value.status_code == 409
        assert asyncio.run(routes.dr_status(self._user()))["requested"] is True

    def test_a_run_that_died_before_shipping_shows_on_the_card(self, dr, monkeypatch):
        from app.api.routes import backup as routes
        dr._write_status({"stamp": "20260923-040000", "sent": {"ok": True}})
        # no Telegram configured: the alert cannot be sent, but it is recorded
        monkeypatch.setattr(bk, "resolve_telegram", lambda db: _async({"token": "", "chat_id": ""}))
        asyncio.run(dr.alert("🛑 بکاپ فاجعه شکست خورد — مرحله: pg_dump"))
        st = asyncio.run(routes.dr_status(self._user()))
        assert st["last_run"]["stamp"] == "20260923-040000"
        assert "pg_dump" in st["last_alert"]["text"]
        # with its offset: the container's bare clock is UTC, and the panel's
        # browser would have shown it as Tehran time, 3½ hours early
        assert st["last_alert"]["at"].endswith("+00:00")

    def test_the_new_routes_are_root_or_super_admin_only(self):
        from app.api.routes import backup as routes
        role_dep = routes._super_admin.dependency
        seen = set()
        for r in routes.router.routes:
            if r.path in ("/diagnose", "/dr", "/dr/run", "/relay-worker"):
                assert role_dep in [d.call for d in r.dependant.dependencies], r.path
                seen.add(r.path)
        assert len(seen) == 4

    def test_the_panel_shows_the_worker_that_is_tested(self):
        from app.api.routes import backup as routes
        code = asyncio.run(routes.relay_worker_code(self._user()))
        assert code == (Path(__file__).resolve().parent.parent / "deploy/telegram-relay/worker.js").read_text(encoding="utf-8")
        assert "X-Relay-Key" in code and "ALLOWED_BOTS" in code

    def test_a_proxy_is_recorded_without_its_password(self, dr, tmp_path, monkeypatch):
        bundle = tmp_path / "20260924-040000"
        bundle.mkdir()
        (bundle / "part0000").write_bytes(b"x" * 10)
        (bundle / "manifest.json").write_text(json.dumps(
            {"stamp": "20260924-040000", "row_counts": "users 3", "parts": [{"name": "part0000", "size": 10}]}))
        monkeypatch.setattr(bk, "resolve_telegram", lambda db: _async({"token": "1:x", "chat_id": "42"}))
        monkeypatch.setattr(bk, "resolve_route", lambda db: _async(bk._route("manual", ["socks5://u:s3cret@h:1080"])))
        monkeypatch.setattr(bk, "_direct_first", lambda: False)
        real = httpx.AsyncClient

        class Fake(real):                         # the proxy is recorded, not dialled
            def __init__(self, *a, **kw):
                kw.pop("proxy", None)
                kw["transport"] = httpx.MockTransport(
                    lambda req: httpx.Response(200, json={"ok": True, "result": {}}))
                super().__init__(*a, **kw)
        monkeypatch.setattr(bk.httpx, "AsyncClient", Fake)
        res = asyncio.run(dr.ship(bundle, db=object()))
        assert res["ok"] and not bundle.exists()
        st = dr.read_status()["last_run"]
        assert "s3cret" not in json.dumps(st)
        assert st["parts"] == 1 and st["bytes"] == 10


async def _async(v):
    return v


class TestTheRelayTestUsesTheSavedKey:
    """The key field says «saved — empty means unchanged». The route test sent
    no key when it was empty, the Worker refused, and the panel reported a bad
    bot token while the nightly backup went through the same relay."""

    def test_an_empty_field_tests_with_the_saved_key(self, store):
        from app.api.routes import backup as routes
        store.rows[bk.KEY_RELAY_KEY] = secret_box.encrypt("s4ved-k3y")
        route = asyncio.run(routes._route_for(
            routes.ProbeIn(proxy_mode="relay", relay="https://tg.example"), store))
        assert route["relay_key"] == "s4ved-k3y"

    def test_a_typed_key_still_wins(self, store):
        from app.api.routes import backup as routes
        store.rows[bk.KEY_RELAY_KEY] = secret_box.encrypt("s4ved-k3y")
        route = asyncio.run(routes._route_for(
            routes.ProbeIn(proxy_mode="relay", relay="https://tg.example", relay_key="typed"), store))
        assert route["relay_key"] == "typed"
