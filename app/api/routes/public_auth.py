"""
SorinFlow — public portal authentication (visitor sign-up and login).

Every route here is unauthenticated by design, so each one is rate-limited and
none of them can produce anything above a visitor. Staff keep using
/api/users/token: routing an admin through here would hand them a token without
ever asking for their second factor.

The whole module answers 404 while PUBLIC_AUTH_ENABLED is off, which is the
state it ships in. Codes go by SMS — email is collected for marketing only,
because delivery from Iran is not dependable enough to stand between a user and
their own account.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, or_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models.user import User
from app.auth.jwt import (
    verify_password, get_password_hash, create_access_token, TOKEN_ACCESS,
)
from app.auth.permissions import ROLE_VISITOR, STAFF_ROLES
from app.services.verification import (
    VerificationError, issue_code, verify_code,
    check_login_rate, record_login_failure, clear_login_failures,
)
from app.schemas import (
    PortalRegisterRequest, PortalVerifyRequest, PortalResendRequest,
    PortalLoginRequest, PortalPendingResponse, TokenResponse,
)

router = APIRouter()
settings = get_settings()

PURPOSE_SIGNUP = "signup"


async def _require_enabled():
    """404 rather than 403 while the feature is off — an endpoint nobody can
    use should not advertise that it exists."""
    if not settings.public_auth_enabled:
        raise HTTPException(status_code=404, detail="Not found")


_enabled = Depends(_require_enabled)


def _normalize_email(email: str | None) -> str | None:
    email = (email or "").strip().lower()
    return email or None


def _issued_response(issued, message: str | None = None,
                     phone: str | None = None) -> PortalPendingResponse:
    """Wrap an IssuedCode for the portal.

    The message names the channel the code actually took. Telling someone to
    check their SMS when it went to their inbox is how a sign-up is abandoned
    thirty seconds in.
    """
    if message is None:
        message = ("کد تأیید به ایمیل شما ارسال شد"
                   if issued.channel == "email"
                   else "کد تأیید برای شما پیامک شد")
    return PortalPendingResponse(
        ttl=issued.ttl, cooldown=issued.cooldown, channel=issued.channel,
        message=message, phone=phone, debug_code=issued.debug_code,
    )


def _token_for(user: User) -> TokenResponse:
    return TokenResponse(
        access_token=create_access_token(
            {"sub": user.username, "role": user.role}, token_type=TOKEN_ACCESS),
        token_type="bearer",
        role=user.role,
        username=user.username,
        full_name=user.full_name,
    )



# ── notification email ──────────────────────────────────────────────────────
#
# Fire-and-forget by design: a welcome message that fails must not roll back a
# registration that succeeded, and an approval email that bounces must not stop
# someone becoming an admin. Every call is wrapped, and failures are logged and
# recorded, never raised.

async def notify_by_email(db, to: str | None, rendered, *, template: str,
                          actor: str = "") -> None:
    from app.services import email_service as _mail
    from app.api.routes.email import log_email as _log
    from loguru import logger as _log_out

    if not to or not _mail.valid_email(to):
        return
    try:
        subject, html, text = rendered
        result = await _mail.send(to, subject, html, text, db=db)
        await _log(db, to, subject, template, result, actor)
        if not result.get("success"):
            _log_out.warning(f"[notify] {template} to {to} failed: {result.get('error')}")
    except Exception as e:
        _log_out.warning(f"[notify] {template} to {to} raised: {e}")


@router.post("/register", response_model=PortalPendingResponse, dependencies=[_enabled])
async def portal_register(data: PortalRegisterRequest, db: AsyncSession = Depends(get_db)):
    """Create (or refresh) an unverified visitor and send them a code.

    A phone that registered but never verified may register again — otherwise a
    mistyped digit or an SMS that never arrived would strand that number
    forever, since the row already occupies the unique index.
    """
    email = _normalize_email(data.email)

    existing = (await db.execute(
        select(User).where(User.phone == data.phone))).scalar_one_or_none()

    if existing and (existing.phone_verified or existing.role in STAFF_ROLES):
        raise HTTPException(status_code=400,
                            detail="این شماره قبلاً ثبت شده است. وارد شوید")

    if email:
        dup = (await db.execute(select(User).where(
            func.lower(User.email) == email,
            User.phone != data.phone))).scalar_one_or_none()
        if dup:
            raise HTTPException(status_code=400, detail="این ایمیل قبلاً ثبت شده است")

    if existing:
        user = existing
        user.full_name = data.full_name
        user.hashed_password = get_password_hash(data.password)
        user.email = email
        user.marketing_opt_in = bool(data.marketing_opt_in)
    else:
        dup_username = (await db.execute(
            select(User).where(User.username == data.phone))).scalar_one_or_none()
        if dup_username:
            raise HTTPException(status_code=400,
                                detail="این شماره قبلاً ثبت شده است. وارد شوید")
        user = User(
            username=data.phone,
            full_name=data.full_name,
            email=email,
            hashed_password=get_password_hash(data.password),
            role=ROLE_VISITOR,
            phone=data.phone,
            phone_verified=False,
            marketing_opt_in=bool(data.marketing_opt_in),
            permissions=[],
            is_active=True,
        )
        db.add(user)

    await db.flush()

    try:
        issued = await issue_code(PURPOSE_SIGNUP, data.phone, data.phone,
                                  email=data.email, db=db)
    except VerificationError as e:
        raise HTTPException(status_code=429 if e.retry_after else 503, detail=e.message)

    await db.commit()
    return _issued_response(issued)


@router.post("/resend", response_model=PortalPendingResponse, dependencies=[_enabled])
async def portal_resend(data: PortalResendRequest, db: AsyncSession = Depends(get_db)):
    user = (await db.execute(
        select(User).where(User.phone == data.phone))).scalar_one_or_none()
    # Same answer whether or not the number exists, so this cannot be used to
    # discover which phone numbers hold accounts.
    if not user or user.phone_verified:
        raise HTTPException(status_code=400, detail="درخواست نامعتبر است")

    try:
        issued = await issue_code(PURPOSE_SIGNUP, data.phone, data.phone,
                                  email=user.email, db=db)
    except VerificationError as e:
        raise HTTPException(status_code=429 if e.retry_after else 503, detail=e.message)
    return _issued_response(issued, "کد تأیید دوباره ارسال شد")


@router.post("/verify", response_model=TokenResponse, dependencies=[_enabled])
async def portal_verify(data: PortalVerifyRequest, db: AsyncSession = Depends(get_db)):
    user = (await db.execute(
        select(User).where(User.phone == data.phone))).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=400, detail="درخواست نامعتبر است")
    if user.role in STAFF_ROLES:
        raise HTTPException(status_code=400, detail="درخواست نامعتبر است")

    try:
        await verify_code(PURPOSE_SIGNUP, data.phone, data.code)
    except VerificationError as e:
        raise HTTPException(status_code=400, detail=e.message)

    was_unverified = not user.phone_verified
    user.phone_verified = True
    user.last_login = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(user)
    await clear_login_failures(data.phone)

    # Only on the first verification — re-verifying later must not re-welcome
    # someone who has been a customer for months.
    if was_unverified:
        from app.services import email_templates as _t
        await notify_by_email(db, user.email, _t.welcome(user.full_name or "کاربر"),
                              template="welcome")
    return _token_for(user)


@router.post("/login", dependencies=[_enabled])
async def portal_login(data: PortalLoginRequest, db: AsyncSession = Depends(get_db)):
    """Visitor login by phone or email.

    Staff are turned away on purpose: this path has no TOTP step, so letting an
    admin through here would be a way around their second factor.
    """
    identifier = data.identifier.strip()
    try:
        await check_login_rate(identifier)
    except VerificationError as e:
        raise HTTPException(status_code=429, detail=e.message)

    # Matched on phone or email only. Matching on username too would let a
    # visitor whose email is "admin" collide with a staff account's username,
    # and first() would pick between them arbitrarily.
    user = (await db.execute(select(User).where(or_(
        User.phone == identifier,
        func.lower(User.email) == identifier.lower(),
    )))).scalars().first()

    # The throttle above is keyed on what was typed, so an account reachable by
    # both a phone and an email would otherwise get one budget per spelling.
    # Once the row is known, charge the account itself as well.
    account_key = f"uid:{user.id}" if user else None
    if account_key:
        try:
            await check_login_rate(account_key)
        except VerificationError as e:
            raise HTTPException(status_code=429, detail=e.message)

    if not user or not verify_password(data.password, user.hashed_password):
        await record_login_failure(identifier)
        if account_key:
            await record_login_failure(account_key)
        raise HTTPException(status_code=401, detail="شماره/ایمیل یا رمز عبور اشتباه است")

    if user.role in STAFF_ROLES:
        raise HTTPException(
            status_code=403,
            detail="این حساب کاربر پنل است. از صفحه ورود پنل استفاده کنید")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="حساب کاربری غیرفعال است")

    if not user.phone_verified:
        try:
            issued = await issue_code(PURPOSE_SIGNUP, user.phone, user.phone,
                                      email=user.email, db=db)
        except VerificationError as e:
            raise HTTPException(status_code=429 if e.retry_after else 503, detail=e.message)
        # phone echoed back because the caller may have signed in with an email
        return _issued_response(
            issued, "شماره شما تأیید نشده است. کد تأیید ارسال شد", phone=user.phone)

    await clear_login_failures(identifier)
    await clear_login_failures(account_key)
    user.last_login = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(user)
    return _token_for(user)


@router.get("/status")
async def portal_auth_status():
    """Lets the login page decide whether to show the sign-up tab at all."""
    return {"enabled": bool(settings.public_auth_enabled)}
