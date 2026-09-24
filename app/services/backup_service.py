"""
SorinFlow — nightly database backup

Dumps every table (FK-safe order) to a gzipped JSON file on the persistent
volume (/app/data/backups) and ships the file to Telegram, so the data
survives server loss or filtering and can be restored on any other server
with scripts/restore_backup.py.
"""
import asyncio
import gzip
import itertools
import json
import re
import time
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from typing import Optional

import httpx
from loguru import logger

from app.config import get_settings
from app.database import Base, async_session_maker
from app.services import net_guard, secret_box

settings = get_settings()

BACKUP_DIR = Path("data/backups")

# The panel's copy of the Telegram credentials. The environment still wins
# when set, like every other credential the panel accepts (see secret_box).
KEY_TOKEN = "backup_telegram_token"     # encrypted
KEY_CHAT = "backup_telegram_chat"
KEY_PROXY = "backup_telegram_proxy"     # encrypted — a proxy URL carries its password
KEY_PROXY_MODE = "backup_telegram_proxy_mode"   # manual | pool | relay
KEY_PROXY_POOL = "backup_telegram_proxy_pool"   # "*" or proxy ids from the dashboard's list
KEY_RELAY = "backup_telegram_relay"             # a Cloudflare Worker in front of api.telegram.org
KEY_RELAY_KEY = "backup_telegram_relay_key"     # encrypted — the worker's shared key, if any
# What happened the last time a file was shipped: shown on the panel so a
# broken offsite copy is a red line on a screen, not a warning in a log.
KEY_LAST = "backup_last_offsite"
KEEP_LOCAL = 14          # rotate: keep the newest N local backups
BACKUP_HOUR = 0          # server clock (UTC container → 03:30 Tehran)
BACKUP_MINUTE = 0


def _ser(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return v


async def create_backup() -> Path:
    """Dump all tables to a gzipped JSON snapshot and rotate old ones."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "created_at": datetime.now().isoformat(),
        "format": 1,
        "tables": {},
    }
    async with async_session_maker() as db:
        for table in Base.metadata.sorted_tables:
            rows = (await db.execute(table.select())).mappings().all()
            payload["tables"][table.name] = [
                {k: _ser(v) for k, v in dict(r).items()} for r in rows
            ]

    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    path = BACKUP_DIR / f"sorinflow-backup-{stamp}.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, default=str)

    for old in sorted(BACKUP_DIR.glob("sorinflow-backup-*.json.gz"))[:-KEEP_LOCAL]:
        old.unlink(missing_ok=True)

    return path


async def resolve_telegram(db=None) -> dict:
    """Effective Telegram credentials — environment first, then the panel."""
    cfg = {"token": (settings.telegram_bot_token or "").strip(),
           "chat_id": (settings.telegram_chat_id or "").strip(),
           "source": "env" if (settings.telegram_bot_token and settings.telegram_chat_id) else None}
    if db is None or (cfg["token"] and cfg["chat_id"]):
        return cfg
    try:
        v = await secret_box.get_many(db, (KEY_TOKEN, KEY_CHAT))
    except Exception as e:
        logger.warning(f"[backup] saved telegram settings unreadable: {e}")
        return cfg
    if not cfg["token"] and v.get(KEY_TOKEN):
        cfg["token"] = secret_box.decrypt(v[KEY_TOKEN])
    if not cfg["chat_id"] and v.get(KEY_CHAT):
        cfg["chat_id"] = (v[KEY_CHAT] or "").strip()
    if cfg["token"] and cfg["chat_id"] and cfg["source"] is None:
        cfg["source"] = "panel"
    return cfg


PROXY_SCHEMES = ("http", "https", "socks5", "socks5h", "socks4")
TELEGRAM_API = "https://api.telegram.org"
_round_robin = itertools.count()


def valid_proxy(url: str) -> bool:
    """http://host:port, socks5://user:pass@host:port — nothing else."""
    m = re.match(r"^(?P<scheme>[a-z0-9]+)://(?:[^@/\s]+@)?(?P<host>[^:/\s]+)(?::(?P<port>\d{1,5}))?/?$", url.strip())
    return bool(m) and m.group("scheme") in PROXY_SCHEMES


def valid_relay(url: str) -> bool:
    """https://tg.example.com or https://x.workers.dev — a base, no path."""
    return bool(re.match(r"^https://[a-z0-9.-]+(?::\d{1,5})?/?$", url.strip(), re.I))


def mask_url(url: str) -> str:
    return re.sub(r"://[^@/]+@", "://***@", url or "")


# ── how the server reaches Telegram ──────────────────────────────────────────
# DIRECT FIRST. «راه اول برای ارسال به تلگرام از سرور ایرانی باید باشد و پراکسی
# راه جایگزین آن است.» The server tries api.telegram.org itself before anything
# else, with a short connect timeout; only when that does not connect does it
# take the configured way round. api.telegram.org is usually filtered from
# Iranian networks, so a failed direct attempt is remembered for a few
# minutes and the next calls go straight to the fallback — a long-polling
# assistant must not pay a connect timeout on every poll.
#
# The fallback, chosen on the panel:
#   manual — one proxy URL, typed;
#   pool   — the dashboard's proxy list (all active ones, or a chosen few),
#            rotated so the load is spread and the next one tried when one
#            does not answer;
#   relay  — a Cloudflare Worker that forwards to api.telegram.org, reached
#            directly (Cloudflare answers from Iran), optionally with a key.
#            deploy/telegram-relay/ has the worker.
# The environment wins over the panel, as everywhere else.
#
# TELEGRAM_DIRECT_FIRST=0 skips the direct attempt (a server that is known
# never to reach Telegram).

DIRECT_CONNECT_TIMEOUT = 6.0     # seconds to wait for api.telegram.org to answer at all
DIRECT_RETRY_AFTER = 300         # after a failed direct attempt, fall back straight away for this long
_direct_down_until = 0.0


def _direct_first() -> bool:
    return str(getattr(settings, "telegram_direct_first", "1") or "1").strip().lower() not in ("0", "false", "no", "off")


def _direct_is_resting() -> bool:
    return time.monotonic() < _direct_down_until


def _note_direct(ok: bool) -> None:
    global _direct_down_until
    _direct_down_until = 0.0 if ok else time.monotonic() + DIRECT_RETRY_AFTER

async def _pool_urls(db, spec: str) -> list:
    """The URLs of the dashboard proxies the pool names, active ones only."""
    from app.models.proxy import Proxy
    from sqlalchemy import select as _select
    q = _select(Proxy).where(Proxy.is_active == True)   # noqa: E712
    ids = [int(x) for x in re.split(r"[\s,،;]+", spec or "") if x.isdigit()]
    if spec.strip() != "*" and ids:
        q = q.where(Proxy.id.in_(ids))
    elif spec.strip() != "*":
        return []
    rows = (await db.execute(q.order_by(Proxy.id.asc()))).scalars().all()
    return [r.url for r in rows if valid_proxy(r.url)]


def _route(mode: str = "manual", proxies=None, api_base: str = "", relay_key: str = "") -> dict:
    return {"mode": mode, "proxies": list(proxies or []),
            "api_base": (api_base or TELEGRAM_API).rstrip("/"), "relay_key": relay_key or ""}


async def resolve_route(db=None) -> dict:
    """The way out, resolved: environment first, then the panel."""
    env_relay = (getattr(settings, "telegram_api_base", "") or "").strip()
    env_proxy = (settings.telegram_proxy or "").strip()
    if env_relay:
        return _route("relay", [], env_relay, (getattr(settings, "telegram_relay_key", "") or "").strip())
    if env_proxy:
        return _route("manual", [env_proxy])
    if db is None:
        return _route("manual", [])
    try:
        v = await secret_box.get_many(db, (KEY_PROXY, KEY_PROXY_MODE, KEY_PROXY_POOL, KEY_RELAY, KEY_RELAY_KEY))
    except Exception as e:
        logger.warning(f"[backup] saved telegram route unreadable: {e}")
        return _route("manual", [])
    mode = (v.get(KEY_PROXY_MODE) or "manual").strip()
    if mode == "relay":
        base = (v.get(KEY_RELAY) or "").strip()
        key = secret_box.decrypt(v[KEY_RELAY_KEY]).strip() if v.get(KEY_RELAY_KEY) else ""
        return _route("relay", [], base, key) if base else _route("manual", [])
    if mode == "pool":
        urls = await _pool_urls(db, v.get(KEY_PROXY_POOL) or "*")
        if urls:
            # balanced: every call starts one proxy further along the list,
            # and falls through to the next when one does not answer
            k = next(_round_robin) % len(urls)
            urls = urls[k:] + urls[:k]
        return _route("pool", urls)
    raw = v.get(KEY_PROXY)
    return _route("manual", [secret_box.decrypt(raw).strip()] if raw else [])


async def every_way_out(db) -> dict:
    """Every way to Telegram this server knows of, whichever mode is in
    effect — the relay if one is set, the manual proxy, and each proxy the
    pool names — so «تست همهٔ راه‌ها» can try each of them on its own."""
    env_relay = (getattr(settings, "telegram_api_base", "") or "").strip()
    env_proxy = (settings.telegram_proxy or "").strip()
    v = {}
    try:
        v = await secret_box.get_many(db, (KEY_PROXY, KEY_PROXY_POOL, KEY_RELAY, KEY_RELAY_KEY))
    except Exception as e:
        logger.warning(f"[backup] saved telegram route unreadable: {e}")
    relay = env_relay or (v.get(KEY_RELAY) or "").strip()
    key = ((getattr(settings, "telegram_relay_key", "") or "").strip() if env_relay else
           secret_box.decrypt(v[KEY_RELAY_KEY]).strip() if v.get(KEY_RELAY_KEY) else "")
    proxies = [env_proxy] if env_proxy else []
    if v.get(KEY_PROXY):
        proxies.append(secret_box.decrypt(v[KEY_PROXY]).strip())
    try:
        proxies += await _pool_urls(db, v.get(KEY_PROXY_POOL) or "*")
    except Exception as e:
        logger.warning(f"[backup] proxy pool unreadable: {e}")
    proxies = [p for p in dict.fromkeys(proxies) if p]
    return _route("relay" if relay else "manual", proxies, relay, key)


async def resolve_proxy(db=None) -> str:
    """The first proxy of the route, or '' — for the panel and for callers that
    want one address. The engines and the shipment use the whole route."""
    r = await resolve_route(db)
    return r["proxies"][0] if r["proxies"] else ""


def describe_route(route: dict) -> str:
    """One line for the panel, credentials blanked."""
    if route["mode"] == "relay":
        return f"رله: {route['api_base']}"
    if route["mode"] == "pool":
        n = len(route["proxies"])
        return f"{n} پراکسی از فهرست داشبورد (چرخشی)" if n else "فهرست داشبورد — هیچ پراکسی فعالی نیست"
    return mask_url(route["proxies"][0]) if route["proxies"] else ""


def telegram_client(proxy: str = "", timeout: float = 20) -> httpx.AsyncClient:
    """One client for every Bot API call, so the proxy is never forgotten."""
    return httpx.AsyncClient(timeout=timeout, proxy=proxy or None)


def _as_route(proxies, route: Optional[dict]) -> dict:
    if route:
        return route
    if isinstance(proxies, str):
        return _route("manual", [proxies] if proxies else [])
    return _route("manual", list(proxies or []))


def _legs(route: dict, *, direct: bool) -> list:
    """The ways to try, in order: (label, url base, proxy, headers).

    `label` is what callers see as «which way it went»: "direct", "relay", or
    the proxy URL itself (so existing callers that printed the proxy still do).
    """
    legs = []
    configured = route["mode"] == "relay" or bool(route["proxies"])
    if direct or not configured:
        legs.append(("direct", TELEGRAM_API, None, None))
    if route["mode"] == "relay" and route["api_base"].rstrip("/") != TELEGRAM_API:
        hdr = {"X-Relay-Key": route["relay_key"]} if route.get("relay_key") else None
        legs.append(("relay", route["api_base"], None, hdr))
    for p in route["proxies"]:
        legs.append((p, TELEGRAM_API, p, None))
    return legs


async def _leg_is_public(base: str, proxy) -> None:
    """A relay or proxy set on the panel reaches public addresses only
    (app/services/net_guard.py) — checked on every call, since a name can
    change what it points at after it was saved. The environment's own
    TELEGRAM_API_BASE / TELEGRAM_PROXY are the operator's, and a sidecar on
    localhost is a fair thing for them to name. Raises BlockedAddress."""
    target = proxy or base
    if target in ((getattr(settings, "telegram_api_base", "") or "").strip(),
                  (settings.telegram_proxy or "").strip()):
        return
    # ponytail: checked, then connected to by name; the pod's egress policy
    # covers the moment between. Pin the address as fetch_text does if the
    # relay ever stops being an operator-only setting.
    await (net_guard.check_proxy(proxy) if proxy else net_guard.check_url(base, schemes=("https",)))


class RelayError(RuntimeError):
    """The relay itself refused, not Telegram: its own JSON body carries a
    "relay" reason (deploy/telegram-relay/worker.js)."""


# What the Worker's reasons mean for whoever reads the panel.
RELAY_REASONS_FA = {
    "unauthorized": "رله درخواست را رد کرد: کلید رله (X-Relay-Key) با RELAY_KEY در Worker یکی نیست",
    "forbidden_bot": "رله این ربات را نمی‌پذیرد: شناسهٔ ربات را به ALLOWED_BOTS در Worker اضافه کنید",
    "upstream_unreachable": "رله هم به تلگرام نرسید",
    "not_found": "آدرس رله درست نیست",
}


def relay_refusal(resp) -> Optional[str]:
    """The relay's own refusal as a Persian sentence, or None when the answer
    is Telegram's — whose 401/403 must still reach the caller unchanged."""
    if resp.status_code == 200:
        return None
    try:
        body = resp.json()
    except Exception:
        return None
    reason = body.get("relay") if isinstance(body, dict) else None
    return RELAY_REASONS_FA.get(reason, f"رله خطا داد ({reason})") if reason else None


async def tg_request(token: str, method: str, route: Optional[dict] = None, *, json=None,
                     data=None, files=None, params=None, timeout: float = 20,
                     direct: Optional[bool] = None):
    """One Bot API call: straight to api.telegram.org first, then the
    configured way round — the relay, or each proxy in turn — until one
    answers (an HTTP answer of any status counts — it is Telegram speaking).

    Returns (response, how it went): "direct", "relay", or the proxy URL.

    `direct` forces the direct attempt on or off for this call; by default it
    is tried unless it failed within the last DIRECT_RETRY_AFTER seconds, or
    TELEGRAM_DIRECT_FIRST=0. With nothing else configured, direct is the only
    way and is always tried.
    """
    route = route or _route("manual", [])
    if direct is None:
        direct = _direct_first() and not _direct_is_resting()
    last: Optional[Exception] = None
    for label, base, proxy, headers in _legs(route, direct=direct):
        url = f"{base.rstrip('/')}/bot{token}/{method}"
        # Direct gets a short CONNECT timeout — a filtered host usually never
        # answers the SYN — but the full timeout once connected, because an
        # upload of a backup part takes as long as it takes.
        tmo = httpx.Timeout(timeout, connect=DIRECT_CONNECT_TIMEOUT) if label == "direct" else timeout
        try:
            if label != "direct":
                await _leg_is_public(base, proxy)
            async with telegram_client(proxy or "", timeout=tmo) as client:
                if json is not None or data is not None or files is not None:
                    resp = await client.post(url, json=json, data=data, files=files, headers=headers)
                else:
                    resp = await client.get(url, params=params, headers=headers)
            if label == "direct":
                _note_direct(True)
            refused = relay_refusal(resp) if label == "relay" else None
            if refused:
                # A wrong key or a bot the Worker does not serve used to come
                # back as Telegram's own 401/403, and no proxy was tried.
                last = RelayError(refused)
                logger.warning(f"[telegram] {method} via relay refused: {refused}")
                continue
            return resp, label
        except net_guard.BlockedAddress as e:
            last = e
            logger.warning(f"[telegram] {method} via {mask_url(label)} refused: {e}")
        except (httpx.TransportError, OSError) as e:
            last = e
            if label == "direct":
                _note_direct(False)
            logger.warning(f"[telegram] {method} via {mask_url(label)} failed: {type(e).__name__}")
    raise last if last else RuntimeError("no route to telegram")


async def diagnose(token: str, route: dict) -> list:
    """getMe down every way there is, one at a time, for the panel's
    «تست راه‌ها»: which of direct / relay / each proxy reaches Telegram today,
    and how fast. Never raises; never touches the direct-rest memory."""
    out = []
    for label, base, proxy, headers in _legs(route, direct=True):
        started = time.monotonic()
        row = {"route": "direct" if label == "direct" else ("relay" if label == "relay" else "proxy"),
               "target": (base if label != "direct" else TELEGRAM_API) if not proxy else mask_url(proxy)}
        try:
            if label != "direct":
                await _leg_is_public(base, proxy)
            tmo = httpx.Timeout(15, connect=DIRECT_CONNECT_TIMEOUT if label == "direct" else 10)
            async with telegram_client(proxy or "", timeout=tmo) as client:
                r = await client.get(f"{base.rstrip('/')}/bot{token}/getMe", headers=headers)
            body = {}
            try:
                body = r.json()
            except Exception:
                pass
            row.update(ok=bool(r.status_code == 200 and body.get("ok")), http=r.status_code,
                       error=None if body.get("ok") else (
                           (relay_refusal(r) if label == "relay" else None)
                           or body.get("description") or f"HTTP {r.status_code}"))
        except Exception as e:
            row.update(ok=False, http=None,
                       error=str(e) if isinstance(e, net_guard.BlockedAddress) else type(e).__name__)
        row["ms"] = int((time.monotonic() - started) * 1000)
        out.append(row)
    return out


async def telegram_ping(token: str, proxies="", route: Optional[dict] = None) -> dict:
    """getMe through the route: the bot's name, the round trip, and which way."""
    started = time.monotonic()
    me, used = await tg_request(token, "getMe", _as_route(proxies, route), timeout=20)
    body = me.json() if me.headers.get("content-type", "").startswith("application/json") else {}
    if me.status_code != 200 or not body.get("ok"):
        raise ValueError(body.get("description") or "توکن ربات پذیرفته نشد")
    r = _as_route(proxies, route)
    via = ("مستقیم" if used == "direct" else
           f"رله {r['api_base']}" if used == "relay" else mask_url(used))
    return {"bot": body["result"].get("username", ""), "ms": int((time.monotonic() - started) * 1000), "via": via}


async def send_text(text: str) -> bool:
    """One Telegram message to the backup chats, the same way out as the
    backups. For the host's DR alerts and the daily SMS-code cap. Never raises."""
    from app.database import async_session_maker
    try:
        async with async_session_maker() as session:
            cfg = await resolve_telegram(session)
            chats = chat_ids(cfg["chat_id"])
            if not cfg["token"] or not chats:
                return False
            route = await resolve_route(session)
            ok = False
            for chat in chats:
                try:
                    r, _ = await tg_request(cfg["token"], "sendMessage", route, timeout=30,
                                            json={"chat_id": chat, "text": text[:3500]})
                    ok = ok or r.status_code == 200
                except Exception as e:
                    logger.warning(f"[telegram] message to {chat} failed: {e}")
            return ok
    except Exception as e:
        logger.warning(f"[telegram] message failed: {e}")
        return False


def chat_ids(value: Optional[str]) -> list:
    """The chats a message goes to: «542901635» or «542901635, 133142359».
    One setting, several recipients — the owner and a colleague, or a group
    and a person — each addressed on its own so one dead id costs nobody else
    their copy."""
    return [c for c in re.split(r"[\s,،;]+", str(value or "")) if re.fullmatch(r"-?\d{1,20}", c)]


async def _remember_offsite(db, outcome: dict) -> None:
    """Keep the last outcome where the panel can read it. Never raises."""
    try:
        await secret_box.put(db, KEY_LAST, json.dumps(outcome, ensure_ascii=False), "backup")
    except Exception as e:
        logger.warning(f"[backup] could not record the offsite outcome: {e}")


def seal(path: Path) -> Path:
    """The copy that leaves the server, encrypted.

    The snapshot is every table: password hashes, TOTP secrets, live Divar
    session cookies. On this disk that is where they already live; in a
    Telegram chat it is a copy of the keys to the business held by a third
    party. So the file is sealed under the same key secret_box uses — derived
    from SECRET_KEY — before it goes. Restoring needs that key, which is in
    the Kubernetes Secret and in the server bundle, and nowhere else.
    """
    sealed = path.with_suffix(path.suffix + ".enc")
    sealed.write_bytes(secret_box._fernet().encrypt(path.read_bytes()))
    return sealed


def unseal(path: Path) -> bytes:
    """The gzip bytes back out of a sealed copy. Raises on the wrong key."""
    return secret_box._fernet().decrypt(path.read_bytes())


async def send_to_telegram(path: Path, db=None) -> bool:
    """Ship the backup file to the configured Telegram chats (offsite copy).

    Sent to every configured chat; the shipment counts as delivered when at
    least one of them has it, and the outcome names the ones that did not."""
    cfg = await resolve_telegram(db)
    token, chats = cfg["token"], chat_ids(cfg["chat_id"])
    if not token or not chats:
        logger.warning("[backup] TELEGRAM_BOT_TOKEN/CHAT_ID not set — offsite copy skipped")
        return False
    route = await resolve_route(db)
    proxy = bool(route["proxies"]) or route["mode"] == "relay"     # some way out is configured

    size_kb = path.stat().st_size // 1024
    sealed = seal(path)
    caption = (
        f"🗄 بکاپ شبانه سورین‌فلو\n"
        f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"📦 {sealed.name} ({size_kb} KB)\n"
        f"🔐 رمزشده با SECRET_KEY سرور\n"
        f"بازگردانی: python scripts/restore_backup.py <file>"
    )
    delivered, failures = [], []
    unreachable = False        # never got an answer, as opposed to a refusal
    try:
        blob = sealed.read_bytes()      # bytes, so a retry on the next proxy can resend it
        for chat in chats:
            try:
                resp, _used = await tg_request(
                    token, "sendDocument", route, timeout=180,
                    data={"chat_id": chat, "caption": caption},
                    files={"document": (sealed.name, blob, "application/octet-stream")})
                body = {}
                try:
                    body = resp.json()
                except Exception:
                    pass
                if resp.status_code == 200 and body.get("ok") is True:
                    delivered.append(chat)
                else:
                    failures.append(f"{chat}: {body.get('description') or 'HTTP ' + str(resp.status_code)}")
                    logger.error(f"[backup] telegram upload to {chat} failed: {resp.status_code} {resp.text[:200]}")
            except Exception as e:
                unreachable = True
                failures.append(f"{chat}: {type(e).__name__}")
                logger.error(f"[backup] telegram upload to {chat} error ({describe_route(route) or 'direct'}): {e}")
    finally:
        sealed.unlink(missing_ok=True)
    ok = bool(delivered)
    error = "؛ ".join(failures)
    if not delivered and unreachable and not proxy:
        # the usual reason on this server, said where the panel shows it
        error = "تلگرام مستقیم از سرور در دسترس نبود و راه جایگزینی (رلهٔ Cloudflare یا پراکسی) تنظیم نشده است"
    if db is not None:
        # with its offset — a bare container time is UTC, read by the browser as Tehran
        await _remember_offsite(db, {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                     "ok": ok, "file": path.name, "size_kb": size_kb,
                                     "delivered": delivered, "error": error[:200]})
    return ok


async def last_offsite(db) -> dict:
    """The recorded outcome of the last shipment, or {}."""
    try:
        raw = (await secret_box.get_many(db, (KEY_LAST,))).get(KEY_LAST)
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def local_snapshots() -> list:
    """What is on the volume, newest first."""
    out = []
    for p in sorted(BACKUP_DIR.glob("sorinflow-backup-*.json.gz"), reverse=True):
        st = p.stat()
        out.append({"file": p.name, "size_kb": st.st_size // 1024,
                    "at": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(timespec="seconds")})
    return out


async def telegram_probe(token: str, proxies="", route: Optional[dict] = None) -> dict:
    """Who the bot is, and which chats have written to it — so the panel can
    fill in the chat id instead of somebody reading JSON off a curl."""
    r = _as_route(proxies, route)
    me, _ = await tg_request(token, "getMe", r, timeout=20)
    mb = me.json() if me.headers.get("content-type", "").startswith("application/json") else {}
    if me.status_code != 200 or not mb.get("ok"):
        raise ValueError(mb.get("description") or "توکن ربات پذیرفته نشد")
    upd, _ = await tg_request(token, "getUpdates", r, params={"limit": 100}, timeout=20)
    ub = upd.json() if upd.status_code == 200 else {}
    chats, seen = [], set()
    for u in ub.get("result") or []:
        msg = u.get("message") or u.get("channel_post") or u.get("my_chat_member") or {}
        chat = msg.get("chat") or {}
        cid = chat.get("id")
        if cid is None or cid in seen:
            continue
        seen.add(cid)
        name = chat.get("title") or " ".join(
            x for x in (chat.get("first_name"), chat.get("last_name")) if x) or chat.get("username") or ""
        chats.append({"id": str(cid), "name": name, "type": chat.get("type", "")})
    return {"bot": mb["result"].get("username", ""), "chats": chats}


async def run_backup(db=None) -> dict:
    """Create a snapshot, rotate, ship offsite. Returns a summary dict."""
    path = await create_backup()
    size_kb = path.stat().st_size // 1024
    if db is None:
        async with async_session_maker() as own:
            sent = await send_to_telegram(path, own)
    else:
        sent = await send_to_telegram(path, db)
    logger.info(f"[backup] {path.name} ({size_kb} KB) | telegram={'✓' if sent else '✗'}")
    return {"file": path.name, "size_kb": size_kb, "telegram_sent": sent}


def _seconds_until_next_run() -> float:
    now = datetime.now()
    target = now.replace(hour=BACKUP_HOUR, minute=BACKUP_MINUTE, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def backup_scheduler():
    """Background task: run the backup every night."""
    logger.info(f"[backup] nightly scheduler armed — next run in {_seconds_until_next_run()/3600:.1f}h")
    while True:
        try:
            await asyncio.sleep(_seconds_until_next_run())
            await run_backup()
            await asyncio.sleep(90)  # step past the trigger minute
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[backup] scheduler error: {e}")
            await asyncio.sleep(3600)
