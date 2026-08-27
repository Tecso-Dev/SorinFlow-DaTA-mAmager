"""
SorinFlow Divar Scraper - API Routes
"""
from fastapi import APIRouter, Depends
from app.api.routes import (
    properties, scraper, auth, stats, proxies, crm, users, filing,
    public_auth, portal,
)
from app.auth.dependencies import require_permission, get_staff_user

router = APIRouter()

# /users — login is public; all other /users routes guard themselves internally
router.include_router(users.router, prefix="/users", tags=["Users"])

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
router.include_router(auth.router, prefix="/auth", tags=["Authentication"], dependencies=_perm("divar_auth"))
router.include_router(stats.router, prefix="/stats", tags=["Statistics"], dependencies=_perm("stats"))
router.include_router(proxies.router, prefix="/proxies", tags=["Proxies"], dependencies=_perm("proxies"))
router.include_router(crm.router, prefix="/crm", tags=["CRM"], dependencies=_perm("crm"))
router.include_router(filing.router, prefix="/filing", tags=["Filing"], dependencies=_perm("filing"))
