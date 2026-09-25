"""
The office's public identity: brand, office name, domain and contact details.

Pages never spell these themselves (CLAUDE.md, multi-office readiness): the
new panel, the portal and the landing page read GET /api/public/site, and
root or super_admin edit them in the panel. The storage and read side
(app_settings as one JSON row, env/default fallback) live in
app/services/site_settings.py so other services — email_templates.py in
particular — can read the same config without importing a route module.

When offices arrive (phase 6) this becomes one row per agency, chosen by the
request's host; the shape stays the same.
"""
import json
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.app_setting import AppSetting
from app.models.user import User
from app.services import audit
from app.services.site_settings import SITE_KEY, defaults, read_site

public_router = APIRouter()
router = APIRouter()


class SiteIn(BaseModel):
    brandName: Optional[str] = Field(None, max_length=80)
    brandNameLatin: Optional[str] = Field(None, max_length=80)
    tagline: Optional[str] = Field(None, max_length=160)
    agencyName: Optional[str] = Field(None, max_length=120)
    domain: Optional[str] = Field(None, max_length=120, pattern=r"^$|^[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
    phone: Optional[str] = Field(None, max_length=40)
    email: Optional[str] = Field(None, max_length=120, pattern=r"^$|^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
    telegram: Optional[str] = Field(None, max_length=120)
    instagram: Optional[str] = Field(None, max_length=120)
    address: Optional[str] = Field(None, max_length=300)
    seoTitle: Optional[str] = Field(None, max_length=120)
    seoDescription: Optional[str] = Field(None, max_length=300)


@public_router.get("/site")
async def public_site(db: Annotated[AsyncSession, Depends(get_db)]):
    """Public: every page may show these, and nothing here is private."""
    return await read_site(db)


@router.get("/site")
async def get_site(db: Annotated[AsyncSession, Depends(get_db)],
                   current_user: Annotated[User, Depends(get_current_user)]):
    if current_user.role not in ("root", "super_admin"):
        raise HTTPException(status_code=403, detail="تنظیمات سایت را فقط مدیر ارشد می‌بیند")
    return {"site": await read_site(db), "defaults": defaults()}


@router.put("/site")
async def put_site(data: SiteIn, db: Annotated[AsyncSession, Depends(get_db)],
                   current_user: Annotated[User, Depends(get_current_user)]):
    if current_user.role not in ("root", "super_admin"):
        raise HTTPException(status_code=403, detail="تنظیمات سایت را فقط مدیر ارشد تغییر می‌دهد")
    current = await read_site(db)
    changes = {k: v.strip() for k, v in data.model_dump(exclude_unset=True).items() if v is not None}
    current.update(changes)
    await db.merge(AppSetting(key=SITE_KEY, value=json.dumps(current, ensure_ascii=False),
                              updated_by=current_user.username))
    await db.commit()
    await audit.record("site_settings_save", actor=current_user, target_type="setting",
                       summary="تنظیمات هویت سایت (برند و تماس) ذخیره شد",
                       detail={"fields": sorted(changes)})
    return await read_site(db)
