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
from datetime import datetime, date, timedelta
from pathlib import Path

import httpx
from loguru import logger

from app.config import get_settings
from app.database import Base, async_session_maker

settings = get_settings()

BACKUP_DIR = Path("data/backups")
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


async def send_to_telegram(path: Path) -> bool:
    """Ship the backup file to the configured Telegram chat (offsite copy)."""
    token, chat_id = settings.telegram_bot_token, settings.telegram_chat_id
    if not token or not chat_id:
        logger.warning("[backup] TELEGRAM_BOT_TOKEN/CHAT_ID not set — offsite copy skipped")
        return False

    size_kb = path.stat().st_size // 1024
    caption = (
        f"🗄 بکاپ شبانه سورین‌فلو\n"
        f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"📦 {path.name} ({size_kb} KB)\n"
        f"بازگردانی: python scripts/restore_backup.py <file>"
    )
    url = f"https://api.telegram.org/bot{token}/sendDocument"
    try:
        async with httpx.AsyncClient(timeout=180) as client:
            with open(path, "rb") as f:
                resp = await client.post(
                    url,
                    data={"chat_id": chat_id, "caption": caption},
                    files={"document": (path.name, f, "application/gzip")},
                )
        ok = resp.status_code == 200 and resp.json().get("ok") is True
        if not ok:
            logger.error(f"[backup] telegram upload failed: {resp.status_code} {resp.text[:200]}")
        return ok
    except Exception as e:
        logger.error(f"[backup] telegram upload error: {e}")
        return False


async def run_backup() -> dict:
    """Create a snapshot, rotate, ship offsite. Returns a summary dict."""
    path = await create_backup()
    size_kb = path.stat().st_size // 1024
    sent = await send_to_telegram(path)
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
