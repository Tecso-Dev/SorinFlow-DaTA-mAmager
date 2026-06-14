"""
SorinFlow — Dashboard User Management API
POST /token              — login (public)
POST /token/verify-totp  — 2FA step (public)
GET  /me                 — current user info
GET  /me/totp/status     — 2FA status
POST /me/totp/setup      — generate 2FA secret + QR URI
POST /me/totp/enable     — enable 2FA after verifying code
POST /me/totp/disable    — disable 2FA
GET  /                   — list users (super_admin)
POST /                   — create user (super_admin)
PATCH/{id}               — update user (super_admin)
DELETE/{id}              — delete user (super_admin)
POST /{id}/password      — reset password (super_admin)
POST /{id}/totp/disable  — force-disable 2FA (super_admin)
"""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional

import pyotp

from app.database import get_db
from app.models.user import User
from app.auth.jwt import verify_password, get_password_hash, create_access_token, decode_token
from app.auth.dependencies import get_current_user, _role_dep
from app.schemas import (
    UserResponse, UserCreate, UserUpdate, UserPasswordReset, TokenResponse, UserList,
    TotpSetupResponse, TotpEnableRequest, TotpDisableRequest, TotpLoginRequest,
)

router = APIRouter()

_super_admin = Depends(_role_dep("super_admin"))


# ── Public ────────────────────────────────────────────────────────────────────

@router.post("/token", response_model=TokenResponse)
async def login(
    form: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.username == form.username))
    user = result.scalar_one_or_none()

    if not user or not verify_password(form.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="نام کاربری یا رمز عبور اشتباه است",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(status_code=403, detail="حساب کاربری غیرفعال است")

    if user.totp_enabled and user.totp_secret:
        # Return a short-lived TOTP session token — no full JWT yet
        totp_session = create_access_token(
            {"sub": user.username, "totp_pending": True},
            expires_minutes=5,
        )
        return TokenResponse(requires_totp=True, totp_session=totp_session)

    user.last_login = datetime.now(timezone.utc)
    await db.commit()

    return TokenResponse(
        access_token=create_access_token({"sub": user.username, "role": user.role}),
        token_type="bearer",
        role=user.role,
        username=user.username,
        full_name=user.full_name,
    )


@router.post("/token/verify-totp", response_model=TokenResponse)
async def verify_totp_login(
    data: TotpLoginRequest,
    db: AsyncSession = Depends(get_db),
):
    from jose import JWTError
    try:
        payload = decode_token(data.totp_session)
    except JWTError:
        raise HTTPException(status_code=401, detail="نشست TOTP نامعتبر یا منقضی شده است")

    if not payload.get("totp_pending"):
        raise HTTPException(status_code=401, detail="نشست TOTP نامعتبر است")

    username = payload.get("sub")
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="کاربر یافت نشد")

    if not user.totp_secret or not pyotp.TOTP(user.totp_secret).verify(data.code, valid_window=1):
        raise HTTPException(status_code=401, detail="کد احراز هویت اشتباه است")

    user.last_login = datetime.now(timezone.utc)
    await db.commit()

    return TokenResponse(
        access_token=create_access_token({"sub": user.username, "role": user.role}),
        token_type="bearer",
        role=user.role,
        username=user.username,
        full_name=user.full_name,
    )


# ── Authenticated ─────────────────────────────────────────────────────────────

@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    return current_user


@router.get("/me/totp/status")
async def totp_status(current_user: User = Depends(get_current_user)):
    return {"enabled": bool(current_user.totp_enabled)}


@router.post("/me/totp/setup", response_model=TotpSetupResponse)
async def totp_setup(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate (or return existing) TOTP secret + provisioning URI."""
    if not current_user.totp_secret:
        current_user.totp_secret = pyotp.random_base32()
        await db.commit()
        await db.refresh(current_user)

    qr_uri = pyotp.TOTP(current_user.totp_secret).provisioning_uri(
        name=current_user.username,
        issuer_name="SorinFlow",
    )
    return TotpSetupResponse(
        secret=current_user.totp_secret,
        qr_uri=qr_uri,
        enabled=bool(current_user.totp_enabled),
    )


@router.post("/me/totp/enable")
async def totp_enable(
    data: TotpEnableRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not current_user.totp_secret:
        raise HTTPException(status_code=400, detail="ابتدا TOTP را راه‌اندازی کنید")
    if not pyotp.TOTP(current_user.totp_secret).verify(data.code, valid_window=1):
        raise HTTPException(status_code=400, detail="کد احراز هویت اشتباه است")

    current_user.totp_enabled = True
    await db.commit()
    return {"success": True, "message": "احراز هویت دو مرحله‌ای فعال شد"}


@router.patch("/me/divar-phone", response_model=UserResponse)
async def update_my_divar_phone(
    data: dict,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Set or clear the current user's linked Divar phone number."""
    phone = data.get("divar_phone", "").strip() or None
    current_user.divar_phone = phone
    await db.commit()
    await db.refresh(current_user)
    return current_user


@router.post("/me/totp/disable")
async def totp_disable(
    data: TotpDisableRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not verify_password(data.password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="رمز عبور اشتباه است")

    current_user.totp_enabled = False
    current_user.totp_secret = None
    await db.commit()
    return {"success": True, "message": "احراز هویت دو مرحله‌ای غیرفعال شد"}


# ── Super Admin only ──────────────────────────────────────────────────────────

@router.get("", response_model=UserList)
async def list_users(
    _: User = _super_admin,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).order_by(User.created_at))
    users = result.scalars().all()
    return UserList(items=users, total=len(users))


@router.post("", response_model=UserResponse, status_code=201)
async def create_user(
    data: UserCreate,
    _: User = _super_admin,
    db: AsyncSession = Depends(get_db),
):
    # Check duplicate username / email
    existing = await db.execute(select(User).where(User.username == data.username))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="نام کاربری قبلاً ثبت شده است")

    if data.email:
        dup_email = await db.execute(select(User).where(User.email == data.email))
        if dup_email.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="ایمیل قبلاً ثبت شده است")

    user = User(
        username=data.username,
        email=data.email,
        full_name=data.full_name,
        hashed_password=get_password_hash(data.password),
        role=data.role,
        divar_phone=data.divar_phone or None,
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: int,
    data: UserUpdate,
    _: User = _super_admin,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="کاربر یافت نشد")

    if data.email is not None:
        user.email = data.email
    if data.full_name is not None:
        user.full_name = data.full_name
    if data.role is not None:
        user.role = data.role
    if data.is_active is not None:
        user.is_active = data.is_active
    if data.divar_phone is not None:
        user.divar_phone = data.divar_phone or None

    await db.commit()
    await db.refresh(user)
    return user


@router.post("/{user_id}/password")
async def reset_password(
    user_id: int,
    data: UserPasswordReset,
    _: User = _super_admin,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="کاربر یافت نشد")

    user.hashed_password = get_password_hash(data.new_password)
    await db.commit()
    return {"success": True, "message": "رمز عبور با موفقیت تغییر کرد"}


@router.delete("/{user_id}")
async def delete_user(
    user_id: int,
    current_user: User = _super_admin,
    db: AsyncSession = Depends(get_db),
):
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="نمی‌توانید حساب خودتان را حذف کنید")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="کاربر یافت نشد")

    await db.delete(user)
    await db.commit()
    return {"success": True, "message": "کاربر حذف شد"}


@router.post("/{user_id}/totp/disable")
async def admin_disable_totp(
    user_id: int,
    _: User = _super_admin,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="کاربر یافت نشد")

    user.totp_enabled = False
    user.totp_secret = None
    await db.commit()
    return {"success": True, "message": "احراز هویت دو مرحله‌ای کاربر غیرفعال شد"}
