"""
The office's public identity: brand, office name, domain and contact details.

Read by app/api/routes/site.py (the panel's settings page and the public
GET /api/public/site) and by anything else that would otherwise have to
hard-code the brand — app/services/email_templates.py in particular
(CLAUDE.md, multi-office readiness: brand/office/domain are never
hard-coded and always come from settings).

Kept in app_settings as one JSON row, so there is no table to migrate;
until someone saves them the environment's SITE_* values (or the defaults
below) stand in. When offices arrive (phase 6) this becomes one row per
agency, chosen by the request's host; the shape stays the same.
"""
import json
import os
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_setting import AppSetting

SITE_KEY = "site.config"

# camelCase on the wire: the panel's SiteConfig type (frontend-next/src/lib/site.ts)
FIELDS = ("brandName", "brandNameLatin", "tagline", "agencyName", "domain", "phone", "email",
          "telegram", "instagram", "address", "seoTitle", "seoDescription")


def defaults() -> dict:
    env = os.environ.get
    return {
        "brandName": env("SITE_BRAND_NAME", "سورین‌فلو"),
        "brandNameLatin": env("SITE_BRAND_NAME_LATIN", "SorinFlow"),
        "tagline": env("SITE_TAGLINE", "CRM املاک و اسکرپر دیوار"),
        "agencyName": env("SITE_AGENCY_NAME", ""),
        "domain": env("SITE_DOMAIN", env("DOMAIN", "sorinflow.com")),
        "phone": "", "email": "", "telegram": "", "instagram": "", "address": "",
        "seoTitle": "", "seoDescription": "",
    }


async def read_site(db: AsyncSession) -> dict:
    """The effective site config: saved values over the env/defaults."""
    raw = (await db.execute(select(AppSetting.value).where(AppSetting.key == SITE_KEY))).scalar()
    out = defaults()
    try:
        saved = json.loads(raw or "{}")
    except ValueError:
        saved = {}
    if isinstance(saved, dict):
        # an emptied brand falls back to the default rather than going blank
        out.update({k: str(v) for k, v in saved.items() if k in FIELDS and v is not None
                    and (str(v).strip() or k not in ("brandName", "brandNameLatin"))})
    return out
