"""
SorinFlow Divar Scraper - API Routes
"""
from fastapi import APIRouter, Depends
from app.api.routes import (
    properties, scraper, auth, stats, proxies, crm, users, filing,
    public_auth, portal, gcp, monitoring, sms, email, forwarder, backup, ai,
    ai_reader, ai_embed, ai_photo, ai_need, ai_assistant, telegram_link, audit
)
from app.auth.dependencies import require_permission, get_staff_user

router = APIRouter()

# /users — login is public; all other /users routes guard themselves internally
router.include_router(users.router, prefix="/users", tags=["Users"])
# /users/me/telegram — one's own Telegram account for the assistant «سورین»;
# staff only, since what it links to is panel data.
router.include_router(telegram_link.router, prefix="/users", tags=["Users"],
                      dependencies=[Depends(get_staff_user)])

# /public/auth — visitor sign-up. Unauthenticated by design, rate-limited
# inside, and 404 while PUBLIC_AUTH_ENABLED is off.
router.include_router(public_auth.router, prefix="/public/auth", tags=["Portal Auth"])

# /portal — mixed audience, so the router itself stays open and each route
# names who it is for (visitor, «portal» permission, or super_admin).
router.include_router(portal.router, prefix="/portal", tags=["Portal"])


# Every dashboard router below is staff-only and permission-gated.
#
# Two things changed here when visitors arrived. The routers used to ask only
# for a valid token, which was fine while the only accounts were staff — but a
# public sign-up also holds a valid token, so «authenticated» stopped meaning
# «belongs in the panel». require_permission refuses visitors first, then checks
# the key.
#
# Existing accounts were backfilled with the areas they already had (see
# _migrate_auth_v2), so switching these on does not take access from anyone who
# has it today; super_admin narrowing someone down is what does.
def _perm(key: str):
    return [Depends(require_permission(key))]


router.include_router(properties.router, prefix="/properties", tags=["Properties"], dependencies=_perm("properties"))
router.include_router(scraper.router, prefix="/scraper", tags=["Scraper"], dependencies=_perm("scraper"))
# Machine-authenticated (HMAC in the route), so no permission dependency:
# the SMS forwarder is a phone, not a user. See scraper.machine_router.
router.include_router(scraper.machine_router, prefix="/scraper", tags=["Scraper"])
router.include_router(auth.router, prefix="/auth", tags=["Authentication"], dependencies=_perm("divar_auth"))
router.include_router(stats.router, prefix="/stats", tags=["Statistics"], dependencies=_perm("stats"))
router.include_router(proxies.router, prefix="/proxies", tags=["Proxies"], dependencies=_perm("proxies"))
router.include_router(crm.router, prefix="/crm", tags=["CRM"], dependencies=_perm("crm"))
router.include_router(filing.router, prefix="/filing", tags=["Filing"], dependencies=_perm("filing"))
router.include_router(monitoring.router, prefix="/monitoring", tags=["Monitoring"], dependencies=_perm("monitoring"))
router.include_router(gcp.router, prefix="/gcp", tags=["Google Cloud"], dependencies=_perm("monitoring"))
router.include_router(sms.router, prefix="/sms", tags=["SMS"], dependencies=_perm("sms"))
# Each route is already scoped to the caller's own rows; the permission
# decides whether the SECTION exists for them at all. divar_auth, because a
# forwarder exists to serve a Divar session and the two are owned together.
router.include_router(forwarder.router, prefix="/forwarder", tags=["SMS Forwarder"], dependencies=_perm("forwarder"))
router.include_router(email.router, prefix="/email", tags=["Email"], dependencies=_perm("email"))
# root and super_admin only, checked inside: it is the whole database.
router.include_router(backup.router, prefix="/backup", tags=["Backup"])
# Same: the audit trail names who did what, not something every admin sees.
router.include_router(audit.router, prefix="/audit", tags=["Audit"])
# Same: the AI card spends money and switches the agents off.
router.include_router(ai.router, prefix="/ai", tags=["AI"])
# The agents' own controls (a pass spends money): root and super_admin, checked inside.
router.include_router(ai_reader.router, prefix="/ai/reader", tags=["AI"])
router.include_router(ai_embed.router, prefix="/ai/embed", tags=["AI"])
router.include_router(ai_photo.router, prefix="/ai/photo", tags=["AI"])
router.include_router(ai_assistant.router, prefix="/ai/assistant", tags=["AI"])
# What a consultant uses: «جستجوی معنایی», similar-by-text, a duplicate's
# explanation, and «پر کردن از متن» on the customer form — behind the CRM key.
router.include_router(ai_embed.crm_router, prefix="/ai/embed", tags=["AI"], dependencies=_perm("crm"))
router.include_router(ai_need.router, prefix="/ai/need", tags=["AI"], dependencies=_perm("crm"))
