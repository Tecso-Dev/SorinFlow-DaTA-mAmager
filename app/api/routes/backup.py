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
from app.config import get_settings
from app.database import get_db
from app.models.user import User
from app.services import backup_service as bk
from app.services import secret_box

router = APIRouter()
settings = get_settings()
_super_admin = Depends(_role_dep(ROLE_ROOT, ROLE_SUPER_ADMIN))

_TOKEN_RE = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$")


class BackupSettingsIn(BaseModel):
    bot_token: Optional[str] = Field(None, max_length=120)
    chat_id: Optional[str] = Field(None, max_length=200)     # one id, or several separated by commas
    # api.telegram.org is blocked from Iran; the shipment goes through this
    proxy: Optional[str] = Field(None, max_length=300)


class ProbeIn(BaseModel):
    bot_token: Optional[str] = Field(None, max_length=120)
    proxy: Optional[str] = Field(None, max_length=300)


async def _proxy_for(payload_proxy: Optional[str], db) -> str:
    """The proxy being typed wins over the saved one, so it can be tried
    before it is saved."""
    typed = (payload_proxy or "").strip()
    if typed:
        if not bk.valid_proxy(typed):
            raise HTTPException(400, "آدرس پراکسی شکل درستی ندارد (مثل socks5://user:pass@host:1080)")
        return typed
    return await bk.resolve_proxy(db)


@router.get("/status")
async def backup_status(db: AsyncSession = Depends(get_db), _: User = _super_admin):
    cfg = await bk.resolve_telegram(db)
    snaps = bk.local_snapshots()
    proxy = await bk.resolve_proxy(db)
    return {
        "configured": bool(cfg["token"] and bk.chat_ids(cfg["chat_id"])),
        "chat_ids": bk.chat_ids(cfg["chat_id"]),
        "source": cfg["source"],
        "token_masked": secret_box.mask(cfg["token"]),
        "chat_id": cfg["chat_id"],
        "proxy_masked": _mask_proxy(proxy),
        "proxy_source": ("env" if (settings.telegram_proxy or "").strip() else "panel") if proxy else None,
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
        raw = payload.chat_id.strip()
        ids = bk.chat_ids(raw)
        if raw and (not ids or len(ids) != len([p for p in re.split(r"[\s,،;]+", raw) if p])):
            raise HTTPException(400, "شناسهٔ چت باید عدد باشد (چت‌های گروهی با منفی شروع می‌شوند)؛ چند شناسه را با ویرگول جدا کنید")
        await secret_box.put(db, bk.KEY_CHAT, ", ".join(ids) or None, actor)
    if payload.proxy is not None:
        proxy = payload.proxy.strip()
        if proxy and not bk.valid_proxy(proxy):
            raise HTTPException(400, "آدرس پراکسی شکل درستی ندارد (مثل socks5://user:pass@host:1080)")
        # encrypted like the token: the URL usually carries the proxy's password
        await secret_box.put(db, bk.KEY_PROXY, secret_box.encrypt(proxy) if proxy else None, actor)
        logger.info(f"[backup] telegram proxy {'updated' if proxy else 'cleared'} by {actor}")
    return await backup_status(db, user)


def _mask_proxy(url: str) -> str:
    """scheme://host:port with the credentials blanked."""
    if not url:
        return ""
    return re.sub(r"://[^@/]+@", "://***@", url)


@router.post("/proxy-test")
async def proxy_test(payload: ProbeIn, db: AsyncSession = Depends(get_db),
                     _: User = _super_admin):
    """getMe through the proxy — the bot's name and the round trip, or why not."""
    tok = (payload.bot_token or "").strip() or (await bk.resolve_telegram(db))["token"]
    if not tok:
        raise HTTPException(400, "ابتدا توکن ربات را وارد کنید")
    proxy = await _proxy_for(payload.proxy, db)
    try:
        info = await bk.telegram_ping(tok, proxy)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(503, ("از طریق پراکسی به تلگرام نرسید" if proxy else
                                  "بدون پراکسی به تلگرام نرسید — از این سرور تلگرام مسدود است")
                            + f" ({type(e).__name__})")
    info["proxy"] = _mask_proxy(proxy)
    return info


@router.post("/probe")
async def probe_bot(payload: ProbeIn, db: AsyncSession = Depends(get_db),
                    _: User = _super_admin):
    """Who the bot is and which chats have written to it. Takes the token
    being typed, or the saved one, so the chat can be picked before saving."""
    tok = (payload.bot_token or "").strip() or (await bk.resolve_telegram(db))["token"]
    if not tok:
        raise HTTPException(400, "ابتدا توکن ربات را وارد کنید")
    proxy = await _proxy_for(payload.proxy, db)
    try:
        info = await bk.telegram_probe(tok, proxy)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(503, ("از طریق پراکسی به تلگرام نرسید" if proxy else
                                  "بدون پراکسی به تلگرام نرسید — از این سرور تلگرام مسدود است؛ پراکسی را تنظیم کنید")
                            + f" ({type(e).__name__})")
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
