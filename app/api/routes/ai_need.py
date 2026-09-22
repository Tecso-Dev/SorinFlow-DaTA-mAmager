"""
خوانندهٔ نیاز مشتری — the panel's door to it.

One endpoint: the consultant's note in, the intake form's fields out. Any CRM
user may use it (mounted under the «crm» permission); nothing is written —
the consultant fills the form from the answer and saves as always. The text
itself is never logged: it is a customer's words.
"""
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import need_parser
from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.user import User
from app.services import llm

router = APIRouter()


class NeedIn(BaseModel):
    text: str = Field(..., max_length=2000)
    hint: Optional[Dict[str, Any]] = None   # what the form already knows, e.g. {"city": "ارومیه"}


@router.post("/parse")
async def parse_need(payload: NeedIn, db: AsyncSession = Depends(get_db),
                     user: User = Depends(get_current_user)):
    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="متنی برای خواندن نیست")
    try:
        out = await need_parser.parse_need(db, text, hint=payload.hint)
    except llm.LLMError as e:
        raise HTTPException(status_code=502, detail=str(e))
    filled = sum(1 for v in out["customer"].values() if v not in (None, ""))
    logger.info(f"[ai:need] {user.username}: {len(text)} chars → {filled} field(s) on {out['model']}")
    return out
