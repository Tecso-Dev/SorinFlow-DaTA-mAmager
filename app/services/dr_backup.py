"""
Disaster recovery — the whole server, in Telegram, openable without it.

«بکاپی می‌خواهم که وقتی سرور از دست رفت، روی سرور جدید به‌راحتی کل اطلاعاتم
را بالا بیاورم، بدون نیاز به گرفتن دوبارهٔ کلیدهای API، و آن را به تلگرام بفرست.»

The nightly JSON snapshot (backup_service) is sealed under SECRET_KEY — which
lives in the Kubernetes Secret on the very server that was lost. It can only
be opened by a server that still exists. This is the other half:

  * scripts/dr_backup.sh, run on the HOST by a systemd timer (installed by the
    deploy), builds exactly the bundle scripts/new_server.sh restores from:
    pg_dump, the Kubernetes Secret (SECRET_KEY, the database password, every
    API key — LLM, SMS, SMTP, Telegram), the data volume without listing
    photos (Divar cookies, Chromium profiles, avatars), and the TLS
    certificate. It encrypts it with DR_BACKUP_PASSPHRASE — a GitHub secret
    the owner also keeps — and splits it into 45 MB parts.
  * The host cannot reach Telegram's configuration; this module can. The
    parts are dropped into data/dr-outbox/<stamp>/ on the shared volume and
    the host runs `python -m app.services.dr_backup ship <dir>` inside the
    pod, which sends them the same way every Telegram message goes: direct
    from the server first, the Cloudflare relay or proxies after.
  * scripts/dr_restore.sh turns the parts back into the bundle on a new box.

Status for the panel lives in data/dr-status.json, written by the host at
build time and by this module after shipping. The panel's «همین حالا» button
drops data/dr-request, which a systemd path unit on the host is watching.
"""
import asyncio
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger

DATA = Path(os.environ.get("SORINFLOW_DATA_DIR", "data"))
OUTBOX = DATA / "dr-outbox"
STATUS = DATA / "dr-status.json"
REQUEST = DATA / "dr-request"

# Telegram's Bot API takes documents up to 50 MB; the parts are 45 MB.
PART_LIMIT = 50 * 1024 * 1024
HISTORY = 10


def read_status() -> dict:
    try:
        return json.loads(STATUS.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_status(update: dict) -> dict:
    """Merge `update` into the last run and keep a short history."""
    cur = read_status()
    last = dict(cur.get("last_run") or {})
    if update.get("stamp") and last.get("stamp") and update["stamp"] != last["stamp"]:
        hist = [last] + list(cur.get("history") or [])
        cur["history"] = hist[:HISTORY]
        last = {}
    last.update(update)
    cur["last_run"] = last
    try:
        STATUS.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATUS.with_suffix(".tmp")
        tmp.write_text(json.dumps(cur, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(STATUS)
    except Exception as e:
        logger.warning(f"[dr] could not write the status file: {e}")
    return cur


def request_run() -> bool:
    """Ask the host for a bundle now (the panel's button)."""
    try:
        REQUEST.parent.mkdir(parents=True, exist_ok=True)
        REQUEST.write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
        return True
    except Exception as e:
        logger.warning(f"[dr] could not drop the request file: {e}")
        return False


def _fmt_mb(n: int) -> str:
    return f"{n / (1024 * 1024):.1f} MB"


def _header(manifest: dict) -> str:
    parts = manifest.get("parts") or []
    total = sum(int(p.get("size") or 0) for p in parts)
    rows = manifest.get("row_counts") or ""
    return (
        "🛟 بکاپ کامل سرور سورین‌فلو (بازیابی فاجعه)\n"
        f"📅 {manifest.get('created_at', '')}\n"
        f"📦 {len(parts)} بخش · {_fmt_mb(total)}\n"
        "🔐 رمزشده با DR_BACKUP_PASSPHRASE (همان که در GitHub Secrets گذاشتید)\n"
        "شامل: پایگاه داده، همهٔ کلیدها و رمزها، نشست‌های دیوار، پروفایل مرورگرها، گواهی SSL"
        " (بدون عکس آگهی‌ها)\n"
        + (f"\n{rows}\n" if rows else "")
        + "\nبازگردانی روی سرور تازه: همهٔ بخش‌ها را در یک پوشه بگذارید و\n"
          "bash scripts/dr_restore.sh <پوشه>\n"
          "سپس دستوری که چاپ می‌کند (scripts/new_server.sh) را اجرا کنید."
    )


async def ship(bundle_dir: Path, db=None) -> dict:
    """Send one bundle's parts to every configured chat. Never raises.

    Delivered = at least one chat received every part. The directory is
    removed once delivered; the host keeps its own copies."""
    from app.database import async_session_maker
    from app.services import backup_service as bk

    bundle_dir = Path(bundle_dir)
    try:
        manifest = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    except Exception as e:
        out = {"ok": False, "error": f"manifest unreadable: {type(e).__name__}"}
        _write_status({"sent": out})
        return out
    stamp = manifest.get("stamp") or bundle_dir.name
    parts = [bundle_dir / p["name"] for p in manifest.get("parts") or []]
    missing = [p.name for p in parts if not p.exists()]
    too_big = [p.name for p in parts if p.exists() and p.stat().st_size > PART_LIMIT]
    if missing or too_big or not parts:
        out = {"ok": False, "at": datetime.now().isoformat(timespec="seconds"),
               "error": ("بخش‌ها ناقص است: " + ", ".join(missing)) if missing else
                        ("بخش بزرگ‌تر از ۵۰ مگابایت: " + ", ".join(too_big)) if too_big else "هیچ بخشی نیست"}
        _write_status({"stamp": stamp, "sent": out})
        return out

    async def _go(session) -> dict:
        cfg = await bk.resolve_telegram(session)
        token, chats = cfg["token"], bk.chat_ids(cfg["chat_id"])
        if not token or not chats:
            return {"ok": False, "error": "ربات تلگرام یا شناسهٔ چت در بخش بکاپ تنظیم نشده است"}
        route = await bk.resolve_route(session)
        delivered, failures, via = [], [], set()
        for chat in chats:
            try:
                r, used = await bk.tg_request(token, "sendMessage", route, timeout=30,
                                              json={"chat_id": chat, "text": _header(manifest)})
                via.add(used)
                if r.status_code != 200:
                    raise RuntimeError(f"HTTP {r.status_code}")
                files = [(p.name, p.read_bytes()) for p in parts]
                files.append((f"sorinflow-dr-{stamp}.manifest.json",
                              json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8")))
                for i, (name, blob) in enumerate(files, 1):
                    cap = (f"بخش {i} از {len(parts)} · {stamp}" if i <= len(parts)
                           else f"فهرست بخش‌ها و چک‌سام · {stamp}")
                    r, used = await bk.tg_request(
                        token, "sendDocument", route, timeout=900,
                        data={"chat_id": chat, "caption": cap},
                        files={"document": (name, blob, "application/octet-stream")})
                    via.add(used)
                    body = {}
                    try:
                        body = r.json()
                    except Exception:
                        pass
                    if r.status_code != 200 or not body.get("ok"):
                        raise RuntimeError(body.get("description") or f"HTTP {r.status_code}")
                delivered.append(chat)
            except Exception as e:
                failures.append(f"{chat}: {type(e).__name__}: {str(e)[:120]}")
                logger.error(f"[dr] bundle {stamp} to {chat} failed: {e}")
        return {"ok": bool(delivered), "delivered": delivered, "error": "؛ ".join(failures)[:300],
                "via": sorted(v for v in via if v)}

    try:
        if db is not None:
            res = await _go(db)
        else:
            async with async_session_maker() as session:
                res = await _go(session)
    except Exception as e:
        res = {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}
    res["at"] = datetime.now().isoformat(timespec="seconds")
    _write_status({"stamp": stamp, "sent": res})
    if res.get("ok"):
        shutil.rmtree(bundle_dir, ignore_errors=True)
        logger.info(f"[dr] bundle {stamp} delivered to {res['delivered']} via {res.get('via')}")
    return res


async def alert(text: str) -> bool:
    """One Telegram line from the host script (a run that could not even be
    built — no passphrase, no disk). Never raises."""
    from app.database import async_session_maker
    from app.services import backup_service as bk
    try:
        async with async_session_maker() as session:
            cfg = await bk.resolve_telegram(session)
            chats = bk.chat_ids(cfg["chat_id"])
            if not cfg["token"] or not chats:
                return False
            route = await bk.resolve_route(session)
            ok = False
            for chat in chats:
                try:
                    r, _ = await bk.tg_request(cfg["token"], "sendMessage", route, timeout=30,
                                               json={"chat_id": chat, "text": text[:3500]})
                    ok = ok or r.status_code == 200
                except Exception as e:
                    logger.warning(f"[dr] alert to {chat} failed: {e}")
            return ok
    except Exception as e:
        logger.warning(f"[dr] alert failed: {e}")
        return False


def _main(argv) -> int:
    if len(argv) >= 3 and argv[1] == "ship":
        res = asyncio.run(ship(Path(argv[2])))
        print(json.dumps(res, ensure_ascii=False))
        return 0 if res.get("ok") else 1
    if len(argv) >= 3 and argv[1] == "alert":
        return 0 if asyncio.run(alert(" ".join(argv[2:]))) else 1
    if len(argv) >= 2 and argv[1] == "status":
        print(json.dumps(read_status(), ensure_ascii=False, indent=1))
        return 0
    print("usage: python -m app.services.dr_backup ship <dir> | alert <text> | status")
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
