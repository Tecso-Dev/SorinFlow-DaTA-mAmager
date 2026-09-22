"""
یابندهٔ معنایی — the routes over app/ai/embeddings.py.

Two routers, because two audiences:

  * `router` — status and «اجرای یک دور»: root and super_admin, like the AI
    card, because a pass spends money. Mounted at /ai/embed.
  * `crm_router` — search, similar, duplicates: any CRM user. A consultant
    typing a customer's words into «جستجوی معنایی» is the point of the
    feature; a search costs one small embed call under the same daily cap.
    Mounted at /ai/embed with the «crm» permission (see EMBED.INTEGRATION.md).
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import embeddings as emb
from app.auth.dependencies import _role_dep, get_current_user
from app.database import get_db
from app.models.property import Property
from app.models.user import User
from app.services import llm
from app.services.match_service import _brief

router = APIRouter()          # status, run — root / super_admin
crm_router = APIRouter()      # search, similar, duplicates — any CRM user
_super_admin = Depends(_role_dep("root", "super_admin"))
_crm_user = Depends(get_current_user)

REASON = "شباهت متن"


async def _load(db: AsyncSession, property_id: int) -> Property:
    prop = (await db.execute(select(Property).where(Property.id == property_id))).scalar_one_or_none()
    if not prop:
        raise HTTPException(status_code=404, detail="ملک پیدا نشد")
    return prop


async def _briefs(db: AsyncSession, scored) -> list:
    """(id, cosine) pairs → the same brief the match cards render, score as
    a percentage, in the order given."""
    ids = [pid for pid, _ in scored]
    if not ids:
        return []
    rows = {p.id: p for p in (await db.execute(select(Property).where(Property.id.in_(ids)))).scalars().all()}
    out = []
    for pid, s in scored:
        p = rows.get(pid)
        if p is not None:
            b = _brief(p, round(max(0.0, s) * 100), [REASON])
            b["similarity"] = round(s, 4)
            b["ai_duplicate_of"] = p.ai_duplicate_of
            out.append(b)
    return out


# ── root / super_admin ───────────────────────────────────────────────────────

@router.get("/status")
async def embed_status(db: AsyncSession = Depends(get_db), _: User = _super_admin):
    """Where the pass is: the cursor, how many listings carry a vector, how
    many are still waiting, how many are flagged as duplicates."""
    cursor = await emb._cursor(db)
    active = Property.is_active == True     # noqa: E712

    async def count(*where) -> int:
        return int((await db.execute(select(func.count(Property.id)).where(active, *where))).scalar_one() or 0)

    return {
        "cursor": cursor,
        "version": emb.EMBED_VERSION,
        "embedded": await count(Property.ai_embedding.isnot(None), Property.ai_embed_version == emb.EMBED_VERSION),
        "behind": await count(Property.id > cursor, Property.title.isnot(None), Property.title != ""),
        "duplicates": await count(Property.ai_duplicate_of.isnot(None)),
        "threshold": emb.DUPLICATE_THRESHOLD,
        "interval_seconds": emb.TICK_SECONDS,
        "enabled": bool(getattr(emb.settings, "match_engine", True)),
        "configured": llm.configured(),
    }


@router.post("/run")
async def embed_run(limit: int = Query(emb.LIMIT, ge=1, le=500),
                    db: AsyncSession = Depends(get_db), _: User = _super_admin):
    """One pass now, at most `limit` listings — the loop's tick, by hand."""
    return await emb.run_once(db, limit=limit)


# ── any CRM user ─────────────────────────────────────────────────────────────

class SearchIn(BaseModel):
    text: str = Field(..., min_length=2, max_length=2000)
    city: Optional[str] = Field(None, max_length=100)
    listing_type: Optional[str] = Field(None, pattern="^(buy|rent)$")
    limit: int = Field(12, ge=1, le=50)


@crm_router.post("/search")
async def embed_search(payload: SearchIn, db: AsyncSession = Depends(get_db), _: User = _crm_user):
    """«جستجوی معنایی»: a customer's words → the listings that read closest.
    The text is masked by the gateway door before it leaves (llm.embed)."""
    try:
        scored = await emb.semantic_candidates(db, payload.text, city=payload.city,
                                               listing_type=payload.listing_type, limit=payload.limit)
    except llm.LLMError as e:
        raise HTTPException(status_code=502, detail=str(e))
    items = await _briefs(db, scored)
    return {"items": items, "total": len(items), "source": {"name": payload.text[:80]}}


@crm_router.get("/similar/{property_id}")
async def embed_similar(property_id: int, limit: int = Query(10, ge=1, le=30),
                        db: AsyncSession = Depends(get_db), _: User = _crm_user):
    """The listings whose text reads closest to this one, same city and
    deal type — the «شباهت متن» view next to «ملک‌های مشابه»."""
    prop = await _load(db, property_id)
    if not prop.ai_embedding:
        return {"items": [], "total": 0, "source": {"id": prop.id, "title": prop.title},
                "pending": True, "message": "این آگهی هنوز برداری ندارد؛ چند دقیقهٔ دیگر دوباره ببینید"}
    index = await emb.load_index(db, city=prop.city_name, listing_type=prop.listing_type, exclude_id=prop.id)
    items = await _briefs(db, emb.nearest(prop.ai_embedding, index, limit))
    return {"items": items, "total": len(items), "source": {"id": prop.id, "title": prop.title, "serial_no": prop.serial_no}}


@crm_router.get("/duplicates/{property_id}")
async def embed_duplicates(property_id: int, db: AsyncSession = Depends(get_db), _: User = _crm_user):
    """What this listing seems to be a repeat of — the flag, explained."""
    prop = await _load(db, property_id)
    items = await emb.find_duplicates(db, prop)
    return {"items": items, "total": len(items), "duplicate_of": prop.ai_duplicate_of,
            "source": {"id": prop.id, "title": prop.title, "serial_no": prop.serial_no}}
