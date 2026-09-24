"""
دستیار دفتر «سورین» — the panel's side of the Telegram assistant.

Whether it is on, whom it answers, what it has been asked; a switch; and
«بپرس» — the same assistant from the panel, for testing a question without
opening Telegram. root and super_admin, like the rest of the AI card.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import _role_dep
from app.database import get_db
from app.models.user import User
from app.services import secret_box
from app.ai import assistant

router = APIRouter()
_super_admin = Depends(_role_dep("root", "super_admin"))


class AssistantSettingsIn(BaseModel):
    enabled: bool


class AskIn(BaseModel):
    text: str = Field(..., min_length=1, max_length=assistant.MAX_QUESTION)


@router.get("/status")
async def assistant_status(db: AsyncSession = Depends(get_db), _: User = _super_admin):
    return await assistant.status(db)


@router.put("/settings")
async def assistant_settings(payload: AssistantSettingsIn, db: AsyncSession = Depends(get_db),
                             user: User = _super_admin):
    await secret_box.put(db, assistant.KEY_ENABLED, "true" if payload.enabled else "false", user.username)
    logger.info(f"[assistant] switched {'on' if payload.enabled else 'off'} by {user.username}")
    return await assistant.status(db)


@router.post("/ask")
async def assistant_ask(payload: AskIn, db: AsyncSession = Depends(get_db), user: User = _super_admin):
    """The assistant, from the panel: the same tools, the same ledger row, the
    same log — with the panel user as the asker."""
    res = await assistant.answer(db, payload.text, user=user, chat_id="")
    if not res["ok"]:
        raise HTTPException(status_code=502, detail=res["text"])
    return res


@router.get("/log")
async def assistant_log(limit: int = Query(30, ge=1, le=200),
                        db: AsyncSession = Depends(get_db), _: User = _super_admin):
    from app.models.ai_chat import AiChat
    rows = (await db.execute(select(AiChat).order_by(AiChat.created_at.desc()).limit(limit))).scalars().all()
    return {"items": [r.to_dict() for r in rows]}
