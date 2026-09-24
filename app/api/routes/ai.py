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
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import _role_dep
from app.database import get_db
from app.models.user import User
from app.services import llm, secret_box

router = APIRouter()
_super_admin = Depends(_role_dep("root", "super_admin"))


class AiSettingsIn(BaseModel):
    model_config = {"protected_namespaces": ()}   # model_write etc. are not pydantic's "model_"

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
        "breaker": llm.breaker_status(),
        "liara": await llm.liara_activity(),
        # what runs on this today; the agents of the later phases join here
        "agents": [
            {"key": "explainer", "name": "توضیح‌دهندهٔ پیشنهاد", "job": "write",
             "desc": "یک جملهٔ فارسی کنار هر ملک در «ملک‌های مشابه» و «ملک‌های مناسب»", "live": True},
            {"key": "reader", "name": "خوانندهٔ آگهی", "job": "read",
             "desc": "مشخصاتی که فقط در متن آگهی آمده: نوع واقعی، طبقه، سند، قابل تبدیل، مناسبِ…", "live": True},
            {"key": "need", "name": "خوانندهٔ نیاز مشتری", "job": "read",
             "desc": "«پر کردن از متن» در فرم مشتری، و توضیح درخواست‌های پرتال", "live": True},
            {"key": "embed", "name": "جستجوی معنایی و تکراری‌یاب", "job": "embed",
             "desc": "ملک‌های نزدیک به یک نیاز، «شباهت متن» در ملک‌های مشابه، و آگهی‌های تکراری", "live": True},
            {"key": "vision", "name": "برچسب‌زن عکس", "job": "vision",
             "desc": "بازسازی‌شده، مبله، نقشه به‌جای عکس، لوگوی مشاور — روی سه عکس اول هر آگهی", "live": True},
            {"key": "assistant", "name": "دستیار دفتر «سورین»", "job": "write",
             "desc": "در تلگرام از دیتابیس دفتر جواب می‌دهد — به همان چت‌های بکاپ؛ فقط خواندن، بدون شماره", "live": True},
        ],
    }


AGENT_CARDS = [
    {"key": "explainer", "name": "توضیح‌دهندهٔ پیشنهاد", "job": "write", "kind": "on_demand",
     "desc": "کنار هر ملک در «ملک‌های مشابه» و «ملک‌های مناسب» یک جملهٔ فارسی می‌نویسد که چرا مناسب است یا نیست.",
     "where": ["CRM ← ملک‌های مشابه", "CRM ← ملک‌های مناسب", "صف تطبیق"]},
    {"key": "reader", "name": "خوانندهٔ آگهی", "job": "read", "kind": "loop", "status_url": "/ai/reader/status",
     "desc": "متن هر آگهی تازه را می‌خواند: نوع واقعی ملک، طبقه، سند، وضعیت، «قابل تبدیل»، «معاوضه»، «مناسبِ…» و ایرادها.",
     "where": ["جزئیات ملک ← برداشت هوش مصنوعی", "موتور تطبیق", "ملک‌های مشابه (نوع واقعی و قابل تبدیل)"]},
    {"key": "need", "name": "خوانندهٔ نیاز مشتری", "job": "read", "kind": "on_demand",
     "desc": "حرف آزاد مشتری را به معیارهای فرم تبدیل می‌کند؛ فقط فیلدهای خالی را پر می‌کند و چیزی را ذخیره نمی‌کند.",
     "where": ["فرم مشتری ← پر کردن از متن", "درخواست‌های پرتال (هنگام ساخت مشتری)"]},
    {"key": "embed", "name": "جستجوی معنایی و تکراری‌یاب", "job": "embed", "kind": "loop", "status_url": "/ai/embed/status",
     "desc": "متن هر آگهی را به بردار تبدیل می‌کند: جستجو با جملهٔ آزاد، «شباهت متن» در امتیاز، و تشخیص آگهی تکراری.",
     "where": ["لیدها ← جستجوی معنایی", "ملک‌های مناسب (کاندیدهای شباهت متن)", "نشان «احتمالاً تکراری»", "ابزار دستیار"]},
    {"key": "vision", "name": "برچسب‌زن عکس", "job": "vision", "kind": "loop", "status_url": "/ai/photo/status",
     "desc": "سه عکس اول هر آگهی را می‌بیند: بازسازی‌شده، مبله، اتاق‌ها، نقشه به‌جای عکس، لوگوی مشاور، کیفیت.",
     "where": ["جزئیات ملک ← برچسب‌های عکس", "هوش تصویری ← برچسب‌های هوش تصویری"]},
    {"key": "assistant", "name": "دستیار دفتر «سورین»", "job": "write", "kind": "telegram", "status_url": "/ai/assistant/status",
     "desc": "در تلگرام از دیتابیس دفتر جواب می‌دهد — شش ابزار فقط‌خواندنی، بدون شمارهٔ کسی.",
     "where": ["تلگرام (چت‌های بکاپ)", "همین صفحه ← بپرس"]},
]


@router.get("/overview")
async def ai_overview(db: AsyncSession = Depends(get_db), _: User = _super_admin):
    """Everything the AI screen draws, in one request: the connection, the
    money (ours and Liara's), and every agent with its own state."""
    from app.ai import assistant as _assistant
    cfg = await llm.config(db)
    usage = await llm.usage_summary(db)
    switches = await llm.agents_enabled(db)
    spent = usage["today"]["cost_usd"]

    per_agent = {a["agent"]: a for a in usage["by_agent"]}
    today_agent = await _usage_by_agent_today(db)
    agents = []
    for card in AGENT_CARDS:
        key = card["key"]
        state: dict = {}
        try:
            if key == "reader":
                from app.ai.listing_reader import status as _st
                state = await _st(db)
            elif key == "embed":
                state = await _embed_state(db)
            elif key == "vision":
                from app.ai.photo_tagger import status as _st
                state = await _st(db)
            elif key == "assistant":
                state = await _assistant.status(db)
        except Exception as e:
            logger.warning(f"[ai] state for {key} unavailable: {type(e).__name__}: {e}")
            state = {"error": type(e).__name__}
        agents.append({**card, "enabled": switches.get(key, True),
                       "model": cfg["models"].get(card["job"]),
                       "state": state,
                       "cap_usd": cfg["agent_caps"].get(key, 0.0),
                       "month": per_agent.get(key, {"calls": 0, "cost_usd": 0.0, "cost_toman": 0, "failed": 0}),
                       "today": today_agent.get(key, {"calls": 0, "cost_usd": 0.0, "cost_toman": 0, "failed": 0})})
    return {
        **cfg,
        "key_set": bool((llm.settings.llm_api_key or "").strip()),
        "base_url_set": bool((llm.settings.llm_base_url or "").strip()),
        "env_models": llm.env_models(),
        "usage": usage, "spent_today_usd": spent, "cap_reached": spent >= cfg["cap_usd"],
        "breaker": llm.breaker_status(),
        "liara": await llm.liara_activity(), "quota": await llm.liara_quota(),
        "agents": agents,
    }


async def _embed_state(db) -> dict:
    """The finder's own numbers, without going through its route's dependencies."""
    from sqlalchemy import and_
    from app.ai import embeddings as emb
    from app.models.property import Property
    cursor = await emb._cursor(db) if hasattr(emb, "_cursor") else 0

    async def count(*where):
        return int((await db.execute(select(func.count(Property.id)).where(and_(*where)))).scalar_one())
    return {
        "cursor": cursor, "version": emb.EMBED_VERSION,
        "embedded": await count(Property.ai_embedding.isnot(None)),
        "behind": await count(Property.id > cursor, Property.is_active == True, Property.title.isnot(None)),   # noqa: E712
        "duplicates": await count(Property.ai_duplicate_of.isnot(None)),
        "interval_seconds": emb.TICK_SECONDS,
    }


async def _usage_by_agent_today(db) -> dict:
    """Today per agent — and, for each, when it last failed and how many calls
    it has got right since. A count of failures alone reads as «broken now»
    when it may be a model that was replaced hours ago."""
    from sqlalchemy import case
    from app.models.ai_usage import AiUsage
    day = llm._day_start_utc()
    rows = (await db.execute(
        select(AiUsage.agent, func.count(AiUsage.id), func.coalesce(func.sum(AiUsage.cost_toman), 0.0),
               func.coalesce(func.sum(AiUsage.cost_usd), 0.0),
               func.coalesce(func.sum(case((AiUsage.ok.is_(False), 1), else_=0)), 0),
               func.max(case((AiUsage.ok.is_(False), AiUsage.created_at))))
        .where(AiUsage.created_at >= day).group_by(AiUsage.agent))).all()
    out = {}
    for agent, calls, toman, usd, failed, last_bad in rows:
        entry = {"calls": int(calls), "cost_toman": round(float(toman)), "cost_usd": round(float(usd), 6),
                 "failed": int(failed or 0),
                 "last_error_at": last_bad.isoformat() if last_bad else None, "ok_since_error": 0,
                 "last_error": None}
        if last_bad is not None:
            entry["ok_since_error"] = int((await db.execute(
                select(func.count(AiUsage.id)).where(AiUsage.agent == agent, AiUsage.ok.is_(True),
                                                     AiUsage.created_at > last_bad))).scalar_one())
            entry["last_error"] = (await db.execute(
                select(AiUsage.error).where(AiUsage.agent == agent, AiUsage.ok.is_(False),
                                            AiUsage.created_at == last_bad).limit(1))).scalar_one_or_none()
        out[agent] = entry
    return out


class AgentSwitchIn(BaseModel):
    enabled: bool


@router.put("/agents/{key}")
async def ai_agent_switch(key: str, payload: AgentSwitchIn,
                          db: AsyncSession = Depends(get_db), user: User = _super_admin):
    """One agent on or off, without touching the others."""
    if key not in llm.AGENTS:
        raise HTTPException(status_code=404, detail="چنین ایجنتی وجود ندارد")
    await secret_box.put(db, llm.agent_key(key), "true" if payload.enabled else "false", user.username)
    if key == "assistant":
        # the assistant's own older key, so one switch means one thing
        from app.ai import assistant as _assistant
        await secret_box.put(db, _assistant.KEY_ENABLED, "true" if payload.enabled else "false", user.username)
    logger.info(f"[ai] agent {key} switched {'on' if payload.enabled else 'off'} by {user.username}")
    return {"key": key, "enabled": payload.enabled}


class AgentCapIn(BaseModel):
    cap_usd: float = Field(..., ge=0, le=100)


@router.put("/agents/{key}/cap")
async def ai_agent_cap(key: str, payload: AgentCapIn,
                       db: AsyncSession = Depends(get_db), user: User = _super_admin):
    """One agent's own daily ceiling — the shared cap still applies on top."""
    if key not in llm.AGENTS:
        raise HTTPException(status_code=404, detail="چنین ایجنتی وجود ندارد")
    await secret_box.put(db, llm.agent_cap_key(key), f"{payload.cap_usd:.4f}", user.username)
    logger.info(f"[ai] agent {key} cap set to ${payload.cap_usd:.2f} by {user.username}")
    return {"key": key, "cap_usd": payload.cap_usd}


@router.get("/log")
async def ai_log(agent: Optional[str] = Query(None), failed_only: bool = False,
                 limit: int = Query(50, ge=1, le=300),
                 db: AsyncSession = Depends(get_db), _: User = _super_admin):
    """The ledger, filtered — every call the agents made, newest first."""
    from app.models.ai_usage import AiUsage
    q = select(AiUsage).order_by(AiUsage.created_at.desc())
    if agent:
        q = q.where(AiUsage.agent == agent)
    if failed_only:
        q = q.where(AiUsage.ok.is_(False))
    rows = (await db.execute(q.limit(limit))).scalars().all()
    return {"items": [r.to_dict() for r in rows],
            "agents": sorted({a["agent"] for a in (await llm.usage_summary(db))["by_agent"]})}


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
