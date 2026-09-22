"""
خوانندهٔ آگهی — the panel's side of it.

Where the reader is (the cursor, how many listings carry facts, how many
wait), one pass on demand, and «بازخوانی» for a single listing from the
property modal. root and super_admin only, like the AI card: every call
here spends money.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import listing_reader
from app.auth.dependencies import _role_dep
from app.database import get_db
from app.models.user import User
from app.services import llm

router = APIRouter()
_super_admin = Depends(_role_dep("root", "super_admin"))


@router.get("/status")
async def reader_status(db: AsyncSession = Depends(get_db), _: User = _super_admin):
    return await listing_reader.status(db)


@router.post("/run")
async def reader_run(limit: int = Query(listing_reader.BATCH, ge=1, le=200),
                     db: AsyncSession = Depends(get_db), user: User = _super_admin):
    """One pass now, the same one the loop runs."""
    out = await listing_reader.run_once(db, limit=limit)
    logger.info(f"[reader] pass run by {user.username}: {out}")
    return out


@router.post("/{property_id}")
async def reader_reread(property_id: int, db: AsyncSession = Depends(get_db), user: User = _super_admin):
    """Read one listing again, whatever the cursor says."""
    try:
        facts = await listing_reader.reread(db, property_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="ملک پیدا نشد")
    except llm.LLMError as e:
        raise HTTPException(status_code=503, detail=str(e))
    if facts is None:
        raise HTTPException(status_code=502, detail="مدل این آگهی را نخواند؛ کمی بعد دوباره تلاش کنید")
    logger.info(f"[reader] listing {property_id} reread by {user.username}")
    return {"id": property_id, "facts": facts}
