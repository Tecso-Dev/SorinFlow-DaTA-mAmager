"""
هوش مصنوعی — the panel's card.

What is connected, which model does which job, what it has cost, and one
button that proves the connection works. The key and the base URL are not
here: they are managed in GitHub and reach the pod as environment variables
(SECRETS.md §2d), so the card shows their state and never their value.

root and super_admin only, like the backup card: spending money and
switching the agents off are not an admin's to do.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import _role_dep
from app.database import get_db
from app.models.user import User
from app.services import llm, secret_box

router = APIRouter()
_super_admin = Depends(_role_dep("root", "super_admin"))


class AiSettingsIn(BaseModel):
    enabled: Optional[bool] = None
    daily_cap_usd: Optional[float] = Field(None, ge=0, le=100)
    notes: Optional[str] = Field(None, max_length=600)
    model_write: Optional[str] = Field(None, max_length=120)
    model_read: Optional[str] = Field(None, max_length=120)
    model_vision: Optional[str] = Field(None, max_length=120)
    model_embed: Optional[str] = Field(None, max_length=120)


@router.get("/status")
async def ai_status(db: AsyncSession = Depends(get_db), _: User = _super_admin):
    """Everything the card draws: the connection, the models, the money."""
    cfg = await llm.config(db)
    usage = await llm.usage_summary(db)
    spent = usage["today"]["cost_usd"]
    return {
        **cfg,
        "key_set": bool((llm.settings.llm_api_key or "").strip()),
        "base_url_set": bool((llm.settings.llm_base_url or "").strip()),
        "env_models": llm.env_models(),
        "usage": usage,
        "spent_today_usd": spent,
        "cap_reached": spent >= cfg["cap_usd"],
        "liara": await llm.liara_activity(),
        # what runs on this today; the agents of the later phases join here
        "agents": [
            {"key": "explainer", "name": "توضیح‌دهندهٔ پیشنهاد", "job": "write",
             "desc": "یک جملهٔ فارسی کنار هر ملک در «ملک‌های مشابه» و «ملک‌های مناسب»", "live": True},
            {"key": "reader", "name": "خوانندهٔ آگهی", "job": "read",
             "desc": "مشخصاتی که فقط در متن آگهی آمده — فاز بعد", "live": False},
            {"key": "need", "name": "خوانندهٔ نیاز مشتری", "job": "read",
             "desc": "متن آزاد مشتری به معیار — فاز بعد", "live": False},
            {"key": "embed", "name": "جستجوی معنایی", "job": "embed",
             "desc": "ملک‌های نزدیک به یک نیاز، و آگهی‌های تکراری — فاز بعد", "live": False},
        ],
    }


@router.put("/settings")
async def put_ai_settings(payload: AiSettingsIn,
                          db: AsyncSession = Depends(get_db),
                          user: User = _super_admin):
    """The knobs that live in the panel. An empty model falls back to the
    environment's; the key and the URL are not settable here on purpose."""
    actor = user.username
    if payload.enabled is not None:
        await secret_box.put(db, llm.KEY_ENABLED, "true" if payload.enabled else "false", actor)
        logger.info(f"[ai] switched {'on' if payload.enabled else 'off'} by {actor}")
    if payload.daily_cap_usd is not None:
        await secret_box.put(db, llm.KEY_CAP, f"{payload.daily_cap_usd:.4f}", actor)
    if payload.notes is not None:
        await secret_box.put(db, llm.KEY_NOTES, payload.notes.strip() or None, actor)
    for job, value in (("write", payload.model_write), ("read", payload.model_read),
                       ("vision", payload.model_vision), ("embed", payload.model_embed)):
        if value is not None:
            await secret_box.put(db, llm.KEY_MODELS[job], value.strip() or None, actor)
    return await llm.config(db)


@router.post("/test")
async def ai_test(db: AsyncSession = Depends(get_db), user: User = _super_admin):
    """One tiny request, on the write model. Ignores the daily cap — a card
    that cannot be tested because the cap is full tells nobody anything."""
    try:
        out = await llm.test_connection(db)
    except llm.LLMError as e:
        raise HTTPException(status_code=502, detail=str(e))
    logger.info(f"[ai] connection tested by {user.username}: {out['model']} in {out['ms']}ms")
    return out


@router.get("/usage")
async def ai_usage(limit: int = Query(30, ge=1, le=200),
                   db: AsyncSession = Depends(get_db), _: User = _super_admin):
    """The last calls, newest first — for «چه چیزی هزینه شد»."""
    from app.models.ai_usage import AiUsage
    rows = (await db.execute(
        select(AiUsage).order_by(AiUsage.created_at.desc()).limit(limit))).scalars().all()
    return {"items": [r.to_dict() for r in rows], "summary": await llm.usage_summary(db)}
