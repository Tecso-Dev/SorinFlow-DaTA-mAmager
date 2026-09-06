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
from sqlalchemy import select, or_, func
from typing import Optional

from loguru import logger
import pyotp

from app.database import get_db
from app.models.user import User
from app.auth.jwt import (
    verify_password, get_password_hash, create_access_token, decode_token,
    TOKEN_TOTP_PENDING,
    TOKEN_SMS_PENDING,
)
from app.auth.dependencies import get_current_user, _role_dep
from app.auth.permissions import (
    PERMISSIONS, ALL_PERMISSIONS, ROLE_ROOT, ASSIGNABLE_BY_SUPER_ADMIN,
    DEFAULT_ADMIN_PERMISSIONS, normalize_permissions, user_permissions,
)
from app.schemas import (
    UserResponse, UserCreate, UserRegister, UserUpdate, UserPasswordReset, TokenResponse, UserList,
    TotpSetupResponse, TotpEnableRequest, TotpDisableRequest, TotpLoginRequest,
    EmailCodeVerifyRequest, PasswordResetRequest, PasswordResetConfirm,
)

router = APIRouter()

_super_admin = Depends(_role_dep(ROLE_ROOT, "super_admin"))


def _guard_root_target(actor: User, target: User) -> None:
    """Only root may touch a root account.

    Without this, super_admin — who can already edit any row — could reset the
    developer's password or delete the account that oversees them.
    """
    if target.role == ROLE_ROOT and actor.role != ROLE_ROOT:
        raise HTTPException(status_code=403, detail="دسترسی به این حساب مجاز نیست")


def _guard_role_assignment(actor: User, role: str | None) -> None:
    """root is never handed out from the panel; super_admin may only assign the
    roles it is allowed to supervise."""
    if role is None:
        return
    if actor.role == ROLE_ROOT:
        return
    if role not in ASSIGNABLE_BY_SUPER_ADMIN:
        raise HTTPException(
            status_code=403,
            detail=f"اختصاص نقش «{role}» از پنل مجاز نیست")


# ── Public ────────────────────────────────────────────────────────────────────

PURPOSE_EMAIL_2FA = "email_2fa"
PURPOSE_PWD_RESET = "pwd_reset"


def _mask_email(addr: str) -> str:
    """s***n@gmail.com — enough to know which inbox, not enough to read out."""
    addr = (addr or "").strip()
    if "@" not in addr:
        return ""
    local, _, domain = addr.partition("@")
    if len(local) <= 2:
        return f"{local[:1]}***@{domain}"
    return f"{local[0]}***{local[-1]}@{domain}"


@router.post("/token", response_model=TokenResponse)
async def login(
    form: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    # Username OR email, because the username is often neither chosen nor
    # memorable.
    #
    # A portal sign-up assigns the phone number as the username, so an account
    # promoted to staff logs in as «09058432452» while its owner thinks of
    # themselves as their email address. The panel remembers the last username
    # it saw, so the box arrives prefilled with somebody else's — and typing
    # your own password against it fails in a way that looks like a wrong
    # password rather than a wrong account.
    #
    # first(), not scalar_one_or_none(): a legacy row sharing an address with
    # another would otherwise raise MultipleResultsFound and surface as a 500
    # on the login page.
    ident = (form.username or "").strip()
    user = (await db.execute(
        select(User).where(or_(
            User.username == ident,
            func.lower(User.email) == ident.lower(),
        )).limit(1)
    )).scalars().first()

    if not user or not verify_password(form.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="نام کاربری یا رمز عبور اشتباه است",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(status_code=403, detail="حساب کاربری غیرفعال است")

    # Email second factor — only when TOTP is NOT the account's factor.
    #
    # This branch used to run first, so that an account with both was not asked
    # for two codes. That let the WEAKER factor decide: turning this toggle on
    # silently retired the authenticator, and from then on read access to the
    # mailbox was read access to the panel — the same mailbox that can now also
    # reset the password. Mailbox compromise alone became root.
    #
    # TOTP wins where it is set up. The escape hatch the old ordering was for
    # still exists and is deliberate: somebody locked out of their authenticator
    # turns TOTP off and leaves this on.
    if (getattr(user, "email_2fa_enabled", False)
            and (user.email or "").strip()
            and not (user.totp_enabled and user.totp_secret)):
        from app.services.verification import issue_code, VerificationError
        try:
            await issue_code(PURPOSE_EMAIL_2FA, user.username, "",
                             email=user.email, channel="email", db=db)
        except VerificationError as e:
            # A code we could not send is not a reason to let somebody past the
            # second factor, but it must say so rather than looking like a
            # wrong password.
            raise HTTPException(status_code=503, detail=e.message)

        return TokenResponse(
            requires_email_code=True,
            email_session=create_access_token(
                {"sub": user.username, "email_pending": True},
                expires_minutes=10,
                token_type=TOKEN_SMS_PENDING,
            ),
            email_hint=_mask_email(user.email),
        )

    if user.totp_enabled and user.totp_secret:
        # Return a short-lived TOTP session token — no full JWT yet
        # typ marks this as a half-finished login. get_current_user refuses
        # any token that is not an access token, which is what stops this one
        # from being replayed as a full credential without the second factor.
        totp_session = create_access_token(
            {"sub": user.username, "totp_pending": True},
            expires_minutes=5,
            token_type=TOKEN_TOTP_PENDING,
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


@router.post("/token/verify-email", response_model=TokenResponse)
async def verify_email_login(
    data: EmailCodeVerifyRequest,
    db: AsyncSession = Depends(get_db),
):
    """Finish a login that owed an emailed code.

    Mirrors verify-totp: the session token proves the password was already
    accepted, and is refused as a full credential by get_current_user because
    its `typ` is not an access token.
    """
    from app.auth.jwt import decode_token
    from app.services.verification import verify_code, VerificationError

    try:
        payload = decode_token(data.email_session)
    except Exception:
        raise HTTPException(status_code=401, detail="نشست ورود نامعتبر یا منقضی است")
    if payload.get("typ") != TOKEN_SMS_PENDING or not payload.get("email_pending"):
        raise HTTPException(status_code=401, detail="نشست ورود نامعتبر است")

    username = payload.get("sub")
    user = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="حساب کاربری در دسترس نیست")

    try:
        await verify_code(PURPOSE_EMAIL_2FA, username, data.code)
    except VerificationError as e:
        raise HTTPException(status_code=400, detail=e.message)

    user.last_login = datetime.now(timezone.utc)
    await db.commit()
    return TokenResponse(
        access_token=create_access_token({"sub": user.username, "role": user.role}),
        token_type="bearer",
        role=user.role,
        username=user.username,
        full_name=user.full_name,
    )


@router.post("/password-reset/request")
async def password_reset_request(
    data: PasswordResetRequest,
    db: AsyncSession = Depends(get_db),
):
    """Send a reset code to the address on file.

    Always answers the same, whether or not the account exists. Saying «no such
    user» here turns this endpoint into a way to ask which addresses have
    accounts, and this panel's users are named after their phone numbers.

    Until now there was no self-service reset at all: the only recovery was a
    super_admin, and when the account locked out IS the super_admin the only
    way back was the database.
    """
    from app.services.verification import issue_code, VerificationError

    ident = (data.identifier or "").strip()
    user = (await db.execute(
        select(User).where(or_(
            User.username == ident,
            func.lower(User.email) == ident.lower(),
        )).limit(1)
    )).scalars().first()

    same_answer = {"sent": True,
                   "message": "اگر این حساب وجود داشته باشد، کد بازنشانی به ایمیلش فرستاده شد"}

    if not user or not (user.email or "").strip() or not user.is_active:
        return same_answer

    try:
        await issue_code(PURPOSE_PWD_RESET, user.username, "",
                         email=user.email, channel="email", db=db)
    except VerificationError as e:
        # Throttling is the one thing worth saying out loud: silence would have
        # somebody pressing the button until they are locked out for an hour.
        raise HTTPException(status_code=429, detail=e.message)
    except Exception as e:
        logger.warning(f"[reset] could not send to {user.username}: {e}")
    return same_answer


@router.post("/password-reset/confirm")
async def password_reset_confirm(
    data: PasswordResetConfirm,
    db: AsyncSession = Depends(get_db),
):
    """Set a new password against a code from the reset email."""
    from app.services.verification import verify_code, VerificationError

    ident = (data.identifier or "").strip()
    user = (await db.execute(
        select(User).where(or_(
            User.username == ident,
            func.lower(User.email) == ident.lower(),
        )).limit(1)
    )).scalars().first()

    # The code is keyed on the username, so a wrong identifier cannot verify —
    # but answer identically either way, for the same reason as the request.
    if not user:
        raise HTTPException(status_code=400, detail="کد نادرست یا منقضی است")

    try:
        await verify_code(PURPOSE_PWD_RESET, user.username, data.code)
    except VerificationError as e:
        raise HTTPException(status_code=400, detail=e.message)

    user.hashed_password = get_password_hash(data.new_password)
    await db.commit()
    logger.info(f"[reset] password changed for {user.username}")
    return {"success": True, "message": "رمز عبور تغییر کرد — حالا وارد شوید"}


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


# ── Registration — restricted to super_admin (no public sign-up) ──────────────

@router.post("/register", response_model=UserResponse, status_code=201)
async def register_user(
    data: UserRegister,
    db: AsyncSession = Depends(get_db),
    _: User = _super_admin,
):
    """Only a super_admin may create accounts — public sign-up is disabled."""
    existing = await db.execute(select(User).where(User.username == data.username))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="نام کاربری قبلاً ثبت شده است")

    if data.email:
        dup = await db.execute(select(User).where(User.email == data.email))
        if dup.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="ایمیل قبلاً ثبت شده است")

    if data.divar_phone:
        dup = await db.execute(select(User).where(User.divar_phone == data.divar_phone))
        if dup.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="این شماره دیوار قبلاً برای یک حساب ثبت شده است")

    user = User(
        username=data.username,
        email=data.email,
        full_name=data.full_name,
        hashed_password=get_password_hash(data.password),
        # 'user' was retired when the four roles landed. An account created with
        # it would pass no staff check and be locked out of the whole panel, so
        # this creates an admin with the starter permission set instead; the
        # owner narrows it from the user editor.
        role="admin",
        permissions=list(DEFAULT_ADMIN_PERMISSIONS),
        divar_phone=data.divar_phone or None,
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


# ── Authenticated ─────────────────────────────────────────────────────────────

@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    # Serialise through the effective list, so root/super_admin report the full
    # set instead of the empty column they actually store.
    data = UserResponse.model_validate(current_user, from_attributes=True)
    data.permissions = user_permissions(current_user)
    return data


@router.get("/permissions/catalog")
async def permissions_catalog(_: User = _super_admin):
    """key -> Persian label, for rendering the toggle list."""
    return {"items": [{"key": k, "label": v} for k, v in PERMISSIONS.items()],
            "defaults": DEFAULT_ADMIN_PERMISSIONS}


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


@router.post("/me/email-2fa")
async def set_email_2fa(
    data: dict,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Turn the emailed second factor on or off for the caller's own account.

    Refused without an address on file: enabling it otherwise locks the account
    out of its own panel, and the only way back would be the database — which
    is precisely the hole this feature exists to close.
    """
    want = bool(data.get("enabled"))
    if want and not (current_user.email or "").strip():
        raise HTTPException(
            status_code=400,
            detail="برای این کار باید ایمیل حسابتان ثبت شده باشد")

    current_user.email_2fa_enabled = want
    await db.commit()
    return {
        "enabled": want,
        "email": _mask_email(current_user.email or ""),
        "message": ("ورود دو مرحله‌ای با ایمیل فعال شد"
                    if want else "ورود دو مرحله‌ای با ایمیل غیرفعال شد"),
    }


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
    users = list(result.scalars().all())
    if _.role != ROLE_ROOT:
        users = [u for u in users if u.role != ROLE_ROOT]
    items = []
    for u in users:
        row = UserResponse.model_validate(u, from_attributes=True)
        row.permissions = user_permissions(u)
        items.append(row)
    return UserList(items=items, total=len(items))


@router.post("", response_model=UserResponse, status_code=201)
async def create_user(
    data: UserCreate,
    _: User = _super_admin,
    db: AsyncSession = Depends(get_db),
):
    _guard_role_assignment(_, data.role)
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
        permissions=normalize_permissions(data.permissions),
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
    _guard_root_target(_, user)
    _guard_role_assignment(_, data.role)

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
    if data.phone is not None:
        user.phone = data.phone or None
    if data.permissions is not None:
        user.permissions = normalize_permissions(data.permissions)

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
    _guard_root_target(_, user)

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
    _guard_root_target(current_user, user)

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
    _guard_root_target(_, user)

    user.totp_enabled = False
    user.totp_secret = None
    await db.commit()
    return {"success": True, "message": "احراز هویت دو مرحله‌ای کاربر غیرفعال شد"}
