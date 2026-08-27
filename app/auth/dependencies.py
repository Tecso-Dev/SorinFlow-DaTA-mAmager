"""
SorinFlow — FastAPI auth dependencies
"""
from typing import Optional
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from jose import JWTError

from app.database import get_db
from app.models.user import User
from app.auth.jwt import decode_token, is_access_token
from app.auth.permissions import (
    PERMISSIONS, STAFF_ROLES, FULL_ACCESS_ROLES,
    ROLE_ROOT, ROLE_SUPER_ADMIN, ROLE_ADMIN, ROLE_VISITOR,
    has_permission,
)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/users/token")
oauth2_scheme_optional = OAuth2PasswordBearer(tokenUrl="/api/users/token", auto_error=False)

# Higher number wins. Kept for callers that compare tiers rather than name them.
ROLES_HIERARCHY = {ROLE_VISITOR: 0, ROLE_ADMIN: 1, ROLE_SUPER_ADMIN: 2, ROLE_ROOT: 3}


async def _user_from_token(token: str, db: AsyncSession) -> Optional[User]:
    """Shared decode path. Returns None for anything that is not a live user
    holding a finished access token."""
    try:
        payload = decode_token(token)
    except Exception:
        return None
    if not is_access_token(payload):
        return None
    username = payload.get("sub")
    if not username:
        return None
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        return None
    return user


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    user = await _user_from_token(token, db)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def get_current_user_optional(
    token: Optional[str] = Depends(oauth2_scheme_optional),
    db: AsyncSession = Depends(get_db),
) -> Optional[User]:
    """Same as get_current_user but returns None instead of raising 401."""
    if not token:
        return None
    return await _user_from_token(token, db)


# Convenience aliases used as FastAPI dependencies
require_authenticated = Depends(get_current_user)


def _role_dep(*allowed_roles: str):
    """Return a dependency that checks the current user's role."""
    async def _check(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{current_user.role}' is not allowed for this action",
            )
        return current_user
    return _check


async def _staff_check(current_user: User = Depends(get_current_user)) -> User:
    """Anyone who belongs in the dashboard.

    This is what stands between a public sign-up and the whole panel. Without
    it a visitor would hold a perfectly valid token and every router that only
    asks for an authenticated user would let them in.
    """
    if current_user.role not in STAFF_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="این بخش برای کاربران پنل است",
        )
    return current_user


def require_permission(permission: str):
    """Gate a router or route on one permission key.

    root and super_admin pass unconditionally; an admin needs the key on their
    own list; a visitor never reaches here because the staff check runs first.
    """
    if permission not in PERMISSIONS:
        raise ValueError(f"unknown permission: {permission}")

    async def _check(current_user: User = Depends(_staff_check)) -> User:
        if not has_permission(current_user, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"دسترسی «{PERMISSIONS[permission]}» برای حساب شما فعال نیست",
            )
        return current_user
    return _check


get_staff_user = _staff_check

require_staff = Depends(_staff_check)
# root can do anything a super_admin can; super_admin can do anything an admin can.
require_admin = Depends(_role_dep(ROLE_ROOT, ROLE_SUPER_ADMIN, ROLE_ADMIN))
require_super_admin = Depends(_role_dep(ROLE_ROOT, ROLE_SUPER_ADMIN))
require_root = Depends(_role_dep(ROLE_ROOT))
