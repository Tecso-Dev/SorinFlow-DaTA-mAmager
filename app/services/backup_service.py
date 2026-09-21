"""
SorinFlow — nightly database backup

Dumps every table (FK-safe order) to a gzipped JSON file on the persistent
volume (/app/data/backups) and ships the file to Telegram, so the data
survives server loss or filtering and can be restored on any other server
with scripts/restore_backup.py.
"""
import asyncio
import gzip
import json
import re
import time
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

import httpx
from loguru import logger

from app.config import get_settings
from app.database import Base, async_session_maker
from app.services import secret_box

settings = get_settings()

BACKUP_DIR = Path("data/backups")

# The panel's copy of the Telegram credentials. The environment still wins
# when set, like every other credential the panel accepts (see secret_box).
KEY_TOKEN = "backup_telegram_token"     # encrypted
KEY_CHAT = "backup_telegram_chat"
KEY_PROXY = "backup_telegram_proxy"     # encrypted — a proxy URL carries its password
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


def valid_proxy(url: str) -> bool:
    """http://host:port, socks5://user:pass@host:port — nothing else."""
    m = re.match(r"^(?P<scheme>[a-z0-9]+)://(?:[^@/\s]+@)?(?P<host>[^:/\s]+)(?::(?P<port>\d{1,5}))?/?$", url.strip())
    return bool(m) and m.group("scheme") in PROXY_SCHEMES


async def resolve_proxy(db=None) -> str:
    """The proxy every Telegram call goes through — environment first, then
    the panel. Empty means a direct connection, which from this server is a
    connection that never answers: api.telegram.org is blocked in Iran."""
    proxy = (settings.telegram_proxy or "").strip()
    if proxy or db is None:
        return proxy
    try:
        raw = (await secret_box.get_many(db, (KEY_PROXY,))).get(KEY_PROXY)
        return secret_box.decrypt(raw).strip() if raw else ""
    except Exception as e:
        logger.warning(f"[backup] saved telegram proxy unreadable: {e}")
        return ""


def telegram_client(proxy: str = "", timeout: float = 20) -> httpx.AsyncClient:
    """One client for every Bot API call, so the proxy is never forgotten."""
    return httpx.AsyncClient(timeout=timeout, proxy=proxy or None)


async def telegram_ping(token: str, proxy: str) -> dict:
    """getMe through the proxy: the bot's name and how long the round trip took."""
    started = time.monotonic()
    async with telegram_client(proxy, timeout=20) as client:
        me = await client.get(f"https://api.telegram.org/bot{token}/getMe")
    body = me.json() if me.headers.get("content-type", "").startswith("application/json") else {}
    if me.status_code != 200 or not body.get("ok"):
        raise ValueError(body.get("description") or "توکن ربات پذیرفته نشد")
    return {"bot": body["result"].get("username", ""), "ms": int((time.monotonic() - started) * 1000)}


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
    proxy = await resolve_proxy(db)

    size_kb = path.stat().st_size // 1024
    sealed = seal(path)
    caption = (
        f"🗄 بکاپ شبانه سورین‌فلو\n"
        f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"📦 {sealed.name} ({size_kb} KB)\n"
        f"🔐 رمزشده با SECRET_KEY سرور\n"
        f"بازگردانی: python scripts/restore_backup.py <file>"
    )
    url = f"https://api.telegram.org/bot{token}/sendDocument"
    delivered, failures = [], []
    unreachable = False        # never got an answer, as opposed to a refusal
    try:
        async with telegram_client(proxy, timeout=180) as client:
            for chat in chats:
                try:
                    with open(sealed, "rb") as f:
                        resp = await client.post(
                            url,
                            data={"chat_id": chat, "caption": caption},
                            files={"document": (sealed.name, f, "application/octet-stream")},
                        )
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
                    logger.error(f"[backup] telegram upload to {chat} error ({'via proxy' if proxy else 'direct'}): {e}")
    finally:
        sealed.unlink(missing_ok=True)
    ok = bool(delivered)
    error = "؛ ".join(failures)
    if not delivered and unreachable and not proxy:
        # the usual reason on this server, said where the panel shows it
        error = "تلگرام از ایران در دسترس نیست — پراکسی تلگرام را تنظیم کنید"
    if db is not None:
        await _remember_offsite(db, {"at": datetime.now().isoformat(timespec="seconds"),
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
                    "at": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")})
    return out


async def telegram_probe(token: str, proxy: str = "") -> dict:
    """Who the bot is, and which chats have written to it — so the panel can
    fill in the chat id instead of somebody reading JSON off a curl."""
    async with telegram_client(proxy, timeout=20) as client:
        me = await client.get(f"https://api.telegram.org/bot{token}/getMe")
        mb = me.json() if me.headers.get("content-type", "").startswith("application/json") else {}
        if me.status_code != 200 or not mb.get("ok"):
            raise ValueError(mb.get("description") or "توکن ربات پذیرفته نشد")
        upd = await client.get(f"https://api.telegram.org/bot{token}/getUpdates",
                               params={"limit": 100})
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
