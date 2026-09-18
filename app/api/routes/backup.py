"""
The nightly backup, and its copy off the server.

The snapshot has always been taken; what was missing was anywhere for it to
go. Shipping it to Telegram was written and waited on two values that only
existed as environment variables — so it never ran. This is where the two
values are entered from the panel (the token encrypted, like the SMTP
password), where the bot's chat is found instead of read off a curl, and
where a shipment can be fired right now to see it arrive.

super_admin and root only: it is the whole database.
"""
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import _role_dep
from app.auth.permissions import ROLE_ROOT, ROLE_SUPER_ADMIN
from app.database import get_db
from app.models.user import User
from app.services import backup_service as bk
from app.services import secret_box

router = APIRouter()
_super_admin = Depends(_role_dep(ROLE_ROOT, ROLE_SUPER_ADMIN))

_TOKEN_RE = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$")


class BackupSettingsIn(BaseModel):
    bot_token: Optional[str] = Field(None, max_length=120)
    chat_id: Optional[str] = Field(None, max_length=40)


class ProbeIn(BaseModel):
    bot_token: Optional[str] = Field(None, max_length=120)


@router.get("/status")
async def backup_status(db: AsyncSession = Depends(get_db), _: User = _super_admin):
    cfg = await bk.resolve_telegram(db)
    snaps = bk.local_snapshots()
    return {
        "configured": bool(cfg["token"] and cfg["chat_id"]),
        "source": cfg["source"],
        "token_masked": secret_box.mask(cfg["token"]),
        "chat_id": cfg["chat_id"],
        "schedule_fa": "هر شب ۰۳:۳۰ به وقت تهران",
        "keep_local": bk.KEEP_LOCAL,
        "snapshots": snaps[:5],
        "snapshot_count": len(snaps),
        "last_offsite": await bk.last_offsite(db),
    }


@router.put("/settings")
async def put_backup_settings(payload: BackupSettingsIn,
                              db: AsyncSession = Depends(get_db),
                              user: User = _super_admin):
    actor = user.username
    if payload.bot_token is not None:
        tok = payload.bot_token.strip()
        if tok:
            if not _TOKEN_RE.match(tok):
                raise HTTPException(400, "توکن ربات شکل درستی ندارد (مثل 123456789:AA…)")
            await secret_box.put(db, bk.KEY_TOKEN, secret_box.encrypt(tok), actor)
            logger.info(f"[backup] telegram token updated by {actor}")
        else:
            # An empty string is «forget it», the same as the SMTP password.
            await secret_box.put(db, bk.KEY_TOKEN, None, actor)
            logger.info(f"[backup] telegram token cleared by {actor}")
    if payload.chat_id is not None:
        cid = payload.chat_id.strip()
        if cid and not re.fullmatch(r"-?\d{3,20}", cid):
            raise HTTPException(400, "شناسهٔ چت باید عدد باشد (چت‌های گروهی با منفی شروع می‌شوند)")
        await secret_box.put(db, bk.KEY_CHAT, cid or None, actor)
    return await backup_status(db, user)


@router.post("/probe")
async def probe_bot(payload: ProbeIn, db: AsyncSession = Depends(get_db),
                    _: User = _super_admin):
    """Who the bot is and which chats have written to it. Takes the token
    being typed, or the saved one, so the chat can be picked before saving."""
    tok = (payload.bot_token or "").strip() or (await bk.resolve_telegram(db))["token"]
    if not tok:
        raise HTTPException(400, "ابتدا توکن ربات را وارد کنید")
    try:
        info = await bk.telegram_probe(tok)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(503, f"تلگرام در دسترس نبود: {type(e).__name__}")
    if not info["chats"]:
        info["hint_fa"] = ("هنوز هیچ چتی به ربات پیام نداده. در تلگرام ربات را باز کنید، "
                           "Start را بزنید و یک پیام بفرستید، بعد دوباره «پیدا کن» را بزنید.")
    return info


@router.post("/run")
async def run_backup_now(db: AsyncSession = Depends(get_db), user: User = _super_admin):
    """A snapshot right now, shipped if Telegram is configured."""
    res = await bk.run_backup(db)
    logger.info(f"[backup] manual run by {user.username}: {res}")
    res["last_offsite"] = await bk.last_offsite(db)
    return res
