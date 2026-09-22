"""
برچسب‌زن عکس — the panel's side of app/ai/photo_tagger.py.

Where the pass stands, one pass on demand, one listing on demand, and a
listing's tags with their Persian labels. root and super_admin only, like
the AI card: a pass spends money.
"""
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import photo_tagger
from app.auth.dependencies import _role_dep
from app.database import get_db
from app.models.property import Property
from app.models.user import User
from app.services import llm

router = APIRouter()
_super_admin = Depends(_role_dep("root", "super_admin"))


def _shape(prop: Property) -> Dict[str, Any]:
    tags = prop.ai_photo_tags or None
    return {"id": prop.id, "tags": tags, "labels": photo_tagger.tags_fa(tags),
            "tagged_at": prop.ai_photos_at.isoformat() if prop.ai_photos_at else None}


@router.get("/status")
async def photo_status(db: AsyncSession = Depends(get_db), _: User = _super_admin):
    """Where the pass stands: the cursor, how many are tagged, how many had
    no photos, how many wait, and which prompt and model."""
    return await photo_tagger.status(db)


@router.post("/run")
async def photo_run(limit: int = Query(30, ge=1, le=100),
                    db: AsyncSession = Depends(get_db), user: User = _super_admin):
    """One pass now, at most `limit` listings. The daily cap still applies."""
    res = await photo_tagger.run_once(db, limit=limit)
    logger.info(f"[photo] pass run by {user.username}: {res}")
    return res


@router.get("/{property_id}")
async def photo_tags(property_id: int, db: AsyncSession = Depends(get_db), _: User = _super_admin):
    """The stored tags and their labels — the modal's chips."""
    prop = await db.get(Property, property_id)
    if prop is None:
        raise HTTPException(status_code=404, detail="آگهی پیدا نشد")
    return _shape(prop)


@router.post("/{property_id}")
async def photo_retag(property_id: int, db: AsyncSession = Depends(get_db), user: User = _super_admin):
    """«برچسب‌زنی دوباره»: one listing, cursor or not."""
    try:
        stored = await photo_tagger.retag(db, property_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="آگهی پیدا نشد")
    except llm.LLMError as e:          # unconfigured, switched off, or over the cap — say which
        raise HTTPException(status_code=502, detail=str(e))
    if stored is None:
        raise HTTPException(status_code=502, detail="مدل پاسخی نداد؛ کمی بعد دوباره امتحان کنید")
    logger.info(f"[photo] listing {property_id} retagged by {user.username}")
    return _shape(await db.get(Property, property_id))
