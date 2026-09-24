"""
«اتصال به تلگرام» — a panel user links their own Telegram account for «سورین».

The profile card takes a one-time code from here; the user sends it to the
bot in a private chat, and the bot links whoever sent it (app/ai/assistant.py).
From then on the assistant answers that Telegram account with this panel
user's rights, and nobody else's. Staff only, like the rest of the panel: a
visitor has no panel data for the assistant to read.
"""
from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import assistant
from app.auth.dependencies import get_staff_user
from app.database import get_db
from app.models.telegram_link import TelegramLink
from app.models.user import User

router = APIRouter()


@router.get("/me/telegram")
async def my_telegram(db: AsyncSession = Depends(get_db), user: User = Depends(get_staff_user)):
    link = (await db.execute(select(TelegramLink).where(TelegramLink.user_id == user.id))).scalar_one_or_none()
    return link.to_dict() if link else {"linked": False}


@router.post("/me/telegram/link-code")
async def my_telegram_code(db: AsyncSession = Depends(get_db), user: User = Depends(get_staff_user)):
    """A code for «/start CODE», good for ten minutes; asking again replaces it."""
    try:
        code = await assistant.issue_link_code(user.id)
    except Exception as e:
        logger.warning(f"[telegram-link] no code for {user.username}: {type(e).__name__}: {e}")
        raise HTTPException(status_code=503, detail="ساختن کد الان ممکن نیست؛ کمی بعد دوباره امتحان کنید")
    bot = await assistant.bot_username(db)
    return {"code": code, "expires_in": assistant.LINK_TTL, "command": f"/start {code}",
            "deep_link": f"https://t.me/{bot}?start={code}" if bot else None}


@router.delete("/me/telegram")
async def my_telegram_unlink(db: AsyncSession = Depends(get_db), user: User = Depends(get_staff_user)):
    await db.execute(delete(TelegramLink).where(TelegramLink.user_id == user.id))
    await db.commit()
    logger.info(f"[telegram-link] {user.username} unlinked their Telegram account")
    return {"linked": False}
