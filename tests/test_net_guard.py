"""
The addresses the panel can point the server at are public ones only
(app/services/net_guard.py): the proxy list import, a proxy itself, the
Telegram relay and proxy, the SMTP host.

No network: name resolution is a table in this file (socket.getaddrinfo,
which the event loop calls), and HTTP is httpx's MockTransport.
"""
import asyncio
import os
import socket
import sys

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_net_guard.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from fastapi import HTTPException  # noqa: E402

from app.services import net_guard  # noqa: E402
from app.services.net_guard import BlockedAddress  # noqa: E402

PUBLIC_V4, PUBLIC_V6 = "93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"

DNS = {
    "list.example": [PUBLIC_V4],
    "dual.example": [PUBLIC_V4, PUBLIC_V6],
    "mixed.example": [PUBLIC_V4, "10.0.0.7"],       # one public answer is not enough
    "inside.example": ["10.43.0.10"],
    "meta.example": ["169.254.169.254"],
    "localhost": ["127.0.0.1", "::1"],
    "relay.example": [PUBLIC_V4],
    "relay.inside": ["192.168.1.5"],
    "smtp.example": [PUBLIC_V4],
}


@pytest.fixture(autouse=True)
def dns(monkeypatch):
    """socket.getaddrinfo from the table above; anything else does not exist."""
    def fake(host, port, family=0, type=0, proto=0, flags=0):
        if host not in DNS:
            raise socket.gaierror(socket.EAI_NONAME, "not in the test table")
        out = []
        for ip in DNS[host]:
            fam = socket.AF_INET6 if ":" in ip else socket.AF_INET
            addr = (ip, port or 0, 0, 0) if fam == socket.AF_INET6 else (ip, port or 0)
            out.append((fam, socket.SOCK_STREAM, 6, "", addr))
        return out
    monkeypatch.setattr(socket, "getaddrinfo", fake)
    return DNS


def run(coro):
    return asyncio.run(coro)


# ── the address rule ─────────────────────────────────────────────────────────

REFUSED_ADDRESSES = {
    "loopback": ["127.0.0.1", "127.1.2.3", "::1"],
    "private": ["10.0.0.1", "172.16.5.4", "192.168.0.1", "fd00::1", "fc00::1"],
    "link-local, the metadata address among them": ["169.254.169.254", "169.254.0.1", "fe80::1"],
    "shared carrier-grade NAT": ["100.64.0.1", "100.127.255.254"],
    "reserved": ["240.0.0.1", "255.255.255.255"],
    "multicast": ["224.0.0.1", "239.255.255.250", "ff02::1"],
    "unspecified": ["0.0.0.0", "::"],
    "IPv4 wrapped in IPv6": ["::ffff:127.0.0.1", "::ffff:10.0.0.1", "::ffff:169.254.169.254",
                             "2002:7f00:1::", "2002:a00:1::", "64:ff9b::a00:1"],
    "not an address": ["", "localhost", "999.1.1.1"],
}


@pytest.mark.parametrize("kind,addresses", REFUSED_ADDRESSES.items())
def test_every_kind_of_internal_address_is_refused(kind, addresses):
    for a in addresses:
        assert not net_guard.is_public(a), (kind, a)


def test_public_addresses_pass():
    for a in ("8.8.8.8", "1.1.1.1", PUBLIC_V4, PUBLIC_V6, "2606:4700::1111", "::ffff:8.8.8.8",
              "2002:808:808::"):
        assert net_guard.is_public(a), a


def test_a_name_passes_only_if_every_address_it_has_is_public():
    assert run(net_guard.resolve_public("dual.example", 443)) == [PUBLIC_V4, PUBLIC_V6]
    for host in ("mixed.example", "inside.example", "meta.example", "localhost"):
        with pytest.raises(BlockedAddress):
            run(net_guard.resolve_public(host, 443))
    with pytest.raises(BlockedAddress, match="پیدا نشد"):
        run(net_guard.resolve_public("nowhere.example", 443))
    # a literal needs no lookup, in any spelling the URL parser leaves
    assert run(net_guard.resolve_public("[2606:4700::1111]", 443)) == ["2606:4700::1111"]
    with pytest.raises(BlockedAddress):
        run(net_guard.resolve_public("[::ffff:7f00:1]", 443))


def test_urls_are_http_or_https_with_no_credentials():
    assert run(net_guard.check_url("https://list.example/x.txt")) == [PUBLIC_V4]
    for bad in ("file:///etc/passwd", "ftp://list.example/x", "gopher://list.example/",
                "https://user:pw@list.example/x", "https://list.example:99999/", "http:///nohost",
                "http://127.0.0.1:6379/", "http://localhost/", "http://[::1]/",
                "http://169.254.169.254/latest/meta-data/"):
        with pytest.raises(BlockedAddress):
            run(net_guard.check_url(bad))
    with pytest.raises(BlockedAddress):
        run(net_guard.check_url("http://list.example/", schemes=("https",)))


def test_a_proxy_may_carry_credentials_but_not_an_internal_host():
    assert run(net_guard.check_proxy("socks5://u:p@list.example:1080")) == [PUBLIC_V4]
    for bad in ("socks5://u:p@127.0.0.1:1080", "http://inside.example:3128", "ftp://list.example:21"):
        with pytest.raises(BlockedAddress):
            run(net_guard.check_proxy(bad))


# ── fetch_text: the connection goes to the address that was checked ─────────

@pytest.fixture
def web(monkeypatch):
    """Every client net_guard opens answers from `routes`, keyed by the name
    the request was for; `seen` records where each request was sent."""
    routes, seen = {}, []

    def handler(request):
        seen.append({"url": str(request.url), "host": request.headers.get("host"),
                     "sni": request.extensions.get("sni_hostname")})
        return routes[request.headers["host"]](request)

    class Client(httpx.AsyncClient):
        def __init__(self, **kw):
            super().__init__(transport=httpx.MockTransport(handler), **kw)
    monkeypatch.setattr(net_guard.httpx, "AsyncClient", Client)
    return routes, seen


def test_the_list_is_fetched_from_the_checked_address(web):
    routes, seen = web
    routes["list.example"] = lambda r: httpx.Response(200, text="1.2.3.4:8080\n")
    assert run(net_guard.fetch_text("https://list.example/proxies.txt")) == "1.2.3.4:8080\n"
    assert seen == [{"url": f"https://{PUBLIC_V4}/proxies.txt", "host": "list.example", "sni": "list.example"}]


def test_every_redirect_is_checked_before_it_is_followed(web):
    routes, seen = web
    routes["list.example"] = lambda r: httpx.Response(302, headers={"location": "http://inside.example/secret"})
    with pytest.raises(BlockedAddress):
        run(net_guard.fetch_text("https://list.example/"))
    assert [s["host"] for s in seen] == ["list.example"], "nothing was sent inside"

    seen.clear()
    routes["list.example"] = lambda r: httpx.Response(301, headers={"location": "http://169.254.169.254/latest/"})
    with pytest.raises(BlockedAddress):
        run(net_guard.fetch_text("https://list.example/"))
    assert len(seen) == 1

    seen.clear()
    routes["list.example"] = lambda r: httpx.Response(302, headers={"location": "https://relay.example/list"})
    routes["relay.example"] = lambda r: httpx.Response(200, text="ok")
    assert run(net_guard.fetch_text("https://list.example/")) == "ok"
    assert [s["host"] for s in seen] == ["list.example", "relay.example"]


def test_redirects_stop_after_three(web):
    routes, seen = web
    routes["list.example"] = lambda r: httpx.Response(302, headers={"location": "/again"})
    with pytest.raises(BlockedAddress, match="تغییر مسیر"):
        run(net_guard.fetch_text("https://list.example/"))
    assert len(seen) == net_guard.MAX_REDIRECTS + 1


def test_the_body_is_capped_and_the_clock_is_overall(web):
    routes, _seen = web
    routes["list.example"] = lambda r: httpx.Response(200, content=b"x" * 3_000_000)
    assert len(run(net_guard.fetch_text("https://list.example/", max_bytes=1_000_000))) == 1_000_000

    async def slow(request):
        await asyncio.sleep(5)
        return httpx.Response(200, text="late")
    routes["list.example"] = slow
    with pytest.raises(asyncio.TimeoutError):
        run(net_guard.fetch_text("https://list.example/", timeout=0.2))


# ── where it is applied ───────────────────────────────────────────────────────

def test_the_proxy_import_refuses_an_internal_url_before_fetching(web):
    from app.api.routes.proxies import ProxyImportRequest, import_proxies
    routes, seen = web
    for url in ("http://169.254.169.254/latest/meta-data/", "http://inside.example/list",
                "http://localhost:8000/metrics", "file:///etc/passwd"):
        with pytest.raises(HTTPException) as e:
            run(import_proxies(ProxyImportRequest(url=url, test=False), db=None))
        assert e.value.status_code == 400 and "could not fetch" not in e.value.detail
    assert seen == []


def test_a_proxy_is_added_only_at_a_public_address():
    from app.api.routes.proxies import create_proxy
    from app.schemas import ProxyCreate
    for address in ("127.0.0.1", "10.43.0.10", "inside.example", "169.254.169.254", "::1"):
        with pytest.raises(HTTPException) as e:
            run(create_proxy(ProxyCreate(address=address, port=3128), db=None))
        assert e.value.status_code == 400


def test_an_imported_internal_address_never_enters_the_table(tmp_path):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy import select
    from app.api.routes.proxies import ProxyImportRequest, import_proxies
    from app.database import Base
    from app.models.proxy import Proxy

    async def go():
        eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/p.db")
        async with eng.begin() as c:
            await c.run_sync(lambda sc: Base.metadata.create_all(sc, tables=[Proxy.__table__]))
        maker = async_sessionmaker(eng, expire_on_commit=False)
        async with maker() as db:
            out = await import_proxies(ProxyImportRequest(
                proxy_list="127.0.0.1:8080\n10.1.2.3:3128:u:p\n8.8.8.8:3128\nlist.example:8080", test=False), db=db)
            rows = sorted((await db.execute(select(Proxy.address))).scalars().all())
        await eng.dispose()
        return out, rows
    out, rows = run(go())
    assert out["imported"] == 2 and out["refused"] == 2
    assert rows == ["8.8.8.8", "list.example"], "a name waits for the probe, which checks it"


def test_the_probe_never_connects_to_an_internal_proxy(monkeypatch):
    from app.models.proxy import Proxy
    from app.services import proxy_pool

    class Boom(httpx.AsyncClient):
        def __init__(self, **kw):
            raise AssertionError("connected")
    monkeypatch.setattr(proxy_pool.httpx, "AsyncClient", Boom)
    for address in ("127.0.0.1", "inside.example", "meta.example"):
        p = Proxy(address=address, port=8080, protocol="http", is_working=True, fail_count=0)
        out = run(proxy_pool.probe(p))
        assert out["success"] is False and "نشانی" in out["error"] and p.is_working is False


def test_the_telegram_relay_and_proxy_set_on_the_panel_are_public(monkeypatch):
    from app.services import backup_service as bk

    class Boom(httpx.AsyncClient):
        def __init__(self, **kw):
            raise AssertionError("connected")
    monkeypatch.setattr(bk, "telegram_client", lambda *a, **kw: Boom())
    for route in (bk._route("relay", [], "https://relay.inside", "k"),
                  bk._route("manual", ["socks5://u:p@127.0.0.1:1080"]),
                  bk._route("pool", ["http://10.0.0.9:3128"])):
        with pytest.raises(BlockedAddress):
            run(bk.tg_request("123:abc", "getMe", route, direct=False))
        # «تست همهٔ راه‌ها» says why, per way
        legs = [r for r in run(bk.diagnose("123:abc", route)) if r["route"] != "direct"]
        assert legs and all(not r["ok"] and "نشانی" in r["error"] for r in legs)

    # the operator's own relay in the environment is theirs to point anywhere
    monkeypatch.setattr(bk.settings, "telegram_api_base", "http://127.0.0.1:8081")
    run(bk._leg_is_public("http://127.0.0.1:8081", None))


def test_saving_an_internal_relay_or_proxy_is_refused():
    from app.api.routes.backup import BackupSettingsIn, put_backup_settings

    class Root:
        username, role = "root", "root"
    for payload in (BackupSettingsIn(relay="https://relay.inside"),
                    BackupSettingsIn(proxy="socks5://u:p@10.0.0.1:1080"),
                    BackupSettingsIn(proxy="http://localhost:3128")):
        with pytest.raises(HTTPException) as e:
            run(put_backup_settings(payload, db=None, user=Root()))
        assert e.value.status_code == 400 and "نشانی" in e.value.detail


def test_an_smtp_host_typed_on_the_panel_is_public(monkeypatch):
    from app.api.routes.email import EmailSettingsIn, put_email_settings
    from app.services import email_service as mail

    def boom(*a, **kw):
        raise AssertionError("connected")
    monkeypatch.setattr(mail, "_sync_send", boom)
    cfg = {"host": "inside.example", "port": 587, "user": "u", "password": "p", "from_name": "x",
           "security": "starttls", "reply_to": "", "from_email": "", "enabled": True,
           "source": "panel", "host_from_panel": True}
    out = run(mail.send("someone@example.com", "s", "<p>x</p>", cfg=cfg))
    assert out["success"] is False and "SMTP" in out["error"]

    class Boss:
        username, role = "boss", "super_admin"
    with pytest.raises(HTTPException) as e:
        run(put_email_settings(EmailSettingsIn(host="127.0.0.1"), db=None, user=Boss(), request=None))
    assert e.value.status_code == 400
    # SMTP_HOST from the environment is the operator's: not checked
    assert run(mail._host_refused({"host": "127.0.0.1", "port": 25})) is None
