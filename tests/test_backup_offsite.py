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
        asyncio.run(bk.telegram_probe("1:x", "socks5://p:1080"))
        asyncio.run(bk.telegram_ping("1:x", "http://q:3128"))
        assert seen == ["socks5://p:1080", "http://q:3128"]

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
        assert '"proxy_masked": _mask_proxy(proxy)' in src
        assert re.sub(r"://[^@/]+@", "://***@", "socks5://u:p@h:1") == "socks5://***@h:1"

    def test_the_crm_notifier_uses_the_same_proxy(self):
        src = Path("app/crm/notification.py").read_text(encoding="utf-8")
        assert "telegram_client(await resolve_proxy(), timeout=10)" in src
        assert "httpx.AsyncClient(" not in src[src.index("async def send_telegram"):src.index("def _sync_send_email")]

    def test_the_panel_has_the_field_and_socks_is_installable(self):
        html = Path("frontend/index.html").read_text(encoding="utf-8")
        js = Path("frontend/js/app.js").read_text(encoding="utf-8")
        assert 'id="bk-proxy"' in html and 'onclick="bkProxyTest()"' in html
        assert "async function bkProxyTest" in js and "bkClearProxy" in js
        assert "if (proxy) body.proxy = proxy;" in js
        assert "socksio" in Path("requirements.txt").read_text(encoding="utf-8")
