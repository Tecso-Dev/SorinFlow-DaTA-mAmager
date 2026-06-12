"""
SorinFlow Divar Scraper - API Routes
"""
from fastapi import APIRouter, Depends
from app.api.routes import properties, scraper, auth, stats, proxies, crm, users
from app.auth.dependencies import get_current_user

router = APIRouter()

# /users — login is public; all other /users routes guard themselves internally
router.include_router(users.router, prefix="/users", tags=["Users"])

# All remaining routers require a valid JWT (any role)
_auth = [Depends(get_current_user)]

router.include_router(properties.router, prefix="/properties", tags=["Properties"], dependencies=_auth)
router.include_router(scraper.router, prefix="/scraper", tags=["Scraper"], dependencies=_auth)
router.include_router(auth.router, prefix="/auth", tags=["Authentication"], dependencies=_auth)
router.include_router(stats.router, prefix="/stats", tags=["Statistics"], dependencies=_auth)
router.include_router(proxies.router, prefix="/proxies", tags=["Proxies"], dependencies=_auth)
router.include_router(crm.router, prefix="/crm", tags=["CRM"], dependencies=_auth)
