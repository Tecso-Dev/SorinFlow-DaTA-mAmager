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
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, or_, func
from typing import Optional

from loguru import logger
from pydantic import BaseModel, Field
import pyotp

from app.database import get_db
from app.models.user import User
from app.auth.jwt import (
    verify_password, get_password_hash, create_access_token, decode_token,
    access_claims, DUMMY_PASSWORD_HASH,
    TOKEN_TOTP_PENDING,
    TOKEN_SMS_PENDING,
)
from app.auth.dependencies import get_current_user, _role_dep
from app.auth.permissions import (
    PERMISSIONS, ALL_PERMISSIONS, ROLE_ROOT, ASSIGNABLE_BY_SUPER_ADMIN,
    DEFAULT_ADMIN_PERMISSIONS, STAFF_ROLES, normalize_permissions, user_permissions,
)
from app.services import audit
from app.schemas import (
    UserResponse, UserCreate, UserRegister, UserUpdate, UserPasswordReset, TokenResponse, UserList,
    TotpSetupResponse, TotpEnableRequest, TotpDisableRequest, TotpLoginRequest,
    EmailCodeVerifyRequest, PasswordResetRequest, PasswordResetConfirm,
    PhoneVerifyRequest, PhoneChangeRequest,
    ProfileUpdate, PasswordChangeRequest, EmailChangeRequest, EmailVerifyRequest,
    PRESENCE_VALUES,
)

router = APIRouter()

_super_admin = Depends(_role_dep(ROLE_ROOT, "super_admin"))


async def _guard_name_is_free(db: AsyncSession, name, exclude_id=None) -> None:
    """A display name another account already goes by is refused.

    Ownership is by account (app/auth/visibility.py), but a colleague is
    still named in a form — a task's assignee, a customer's consultant — and
    a typed name is resolved to the one account that goes by it. Two
    accounts with one name would leave every such row nobody's, and two
    people the panel shows the same would be told apart by nothing.
    """
    name = (name or "").strip()
    if not name:
        return
    q = select(User.id).where(or_(func.lower(func.trim(User.full_name)) == name.lower(),
                                  func.lower(User.username) == name.lower()))
    if exclude_id is not None:
        q = q.where(User.id != exclude_id)
    if (await db.execute(q)).scalars().first() is not None:
        raise HTTPException(409, "این نام را حساب دیگری دارد؛ نامی بنویسید که با همکاران یکی نباشد")


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
PURPOSE_PHONE = "phone_verify"
PURPOSE_EMAIL = "email_verify"


def _totp_step(secret: str, code: str) -> int | None:
    """The 30-second step `code` belongs to, or None.

    Its own step or one either side, for a phone clock that drifts — the
    same leeway valid_window=1 gave. The step, not a yes, because the caller
    has to record which one was spent.
    """
    totp = pyotp.TOTP(secret)
    now = totp.timecode(datetime.now(timezone.utc))
    for step in (now - 1, now, now + 1):
        if pyotp.utils.strings_equal(str(code or ""), totp.generate_otp(step)):
            return step
    return None


async def _claim_totp_step(db: AsyncSession, user: User, step: int) -> bool:
    """Spend `step` for this account; False if it, or a later one, already was.

    One conditional UPDATE rather than read-then-write, so two requests racing
    with the same code cannot both see «not used yet»: the second waits on the
    first's row lock and then matches nothing.
    """
    res = await db.execute(
        update(User)
        .where(User.id == user.id,
               or_(User.totp_last_step.is_(None), User.totp_last_step < step))
        .values(totp_last_step=step)
        .execution_options(synchronize_session=False))
    return res.rowcount == 1


TOTP_REUSED = "این کد قبلاً استفاده شده است — کد بعدی برنامه را وارد کنید"


def _account_key(user: User) -> str:
    # The key portal_login charges too: one budget per account, whichever
    # door and whichever spelling (username or email) is tried.
    return f"uid:{user.id}"


async def _login_attempt(request: Optional[Request], key: str) -> None:
    """Count this attempt, or 429 once this address or account has spent its.

    Before the password or code is looked at, so the right one is refused as
    well until the window passes — otherwise the lock would only slow a
    guesser down. A wrong answer is already counted; a right one calls
    _login_passed. Redis down: allowed, with a warning (verification.py).

    request is None for a re-verify-my-password action (totp_disable): there
    is no address to also throttle, only the account's own budget.
    """
    from app.services.verification import take_login_attempt, VerificationError
    try:
        await take_login_attempt(request, key)
    except VerificationError as e:
        raise HTTPException(status_code=429, detail=e.message,
                            headers={"Retry-After": str(e.retry_after)})


async def _login_passed(request: Request, key: str, done: bool) -> None:
    """The answer was right: the address gets its attempt back. `done` once a
    token is issued: only then is the account's count cleared — clearing it on
    the password alone would let whoever holds it reset the count between
    code guesses."""
    from app.services.verification import login_attempt_passed, clear_login_failures
    await login_attempt_passed(request)
    if done:
        await clear_login_failures(key)


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
    request: Request,
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

    # A name that does not exist is charged to what was typed, so it runs out
    # exactly like one that does and the 429 says nothing about which is real.
    # Prefixed, or typing «uid:1» would spend account 1's budget: every
    # account locked without knowing a single username.
    key = _account_key(user) if user else f"name:{ident}"
    await _login_attempt(request, key)

    # One bcrypt round whether or not the name exists. An inactive account
    # is checked in full too; it is only told so after its password is right.
    ok = verify_password(form.password,
                         user.hashed_password if user else DUMMY_PASSWORD_HASH)
    if not user or not ok:
        # An unknown name is not stored as typed: a password put in the
        # username box would sit in the trail for a year. A known one is a
        # username the table already holds.
        await audit.record(
            "login_failed", actor=user, target_type="user",
            target_id=user.id if user else None,
            summary=(f"تلاش ورود ناموفق برای «{ident}»" if user
                     else f"تلاش ورود ناموفق با نام کاربری ناشناس ({len(ident)} نویسه)"),
            detail={"reason": "wrong_password" if user else "unknown_user"},
            request=request)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="نام کاربری یا رمز عبور اشتباه است",
            headers={"WWW-Authenticate": "Bearer"},
        )
    await _login_passed(request, key, done=False)
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

    # no second factor owed: the login is done
    from app.services.verification import clear_login_failures
    await clear_login_failures(key)
    user.last_login = datetime.now(timezone.utc)
    await db.commit()
    await audit.record("login_success", actor=user, target_type="user", target_id=user.id,
                       summary=f"ورود موفق: {user.username}", request=request)

    return TokenResponse(
        access_token=create_access_token(access_claims(user)),
        token_type="bearer",
        role=user.role,
        username=user.username,
        full_name=user.full_name,
    )


@router.post("/token/verify-email", response_model=TokenResponse)
async def verify_email_login(
    data: EmailCodeVerifyRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Finish a login that owed an emailed code.

    Mirrors verify-totp: the session token proves the password was already
    accepted, and is refused as a full credential by get_current_user because
    its `typ` is not an access token.

    Per-IP budget first, like every other unauthenticated route: the code has
    a per-identifier attempt cap, but one host could otherwise walk many
    identifiers at the cap each.
    """
    from app.auth.jwt import decode_token
    from app.services.verification import (
        verify_code, VerificationError, check_ip_budget, spend_ip_budget,
        IP_VERIFY_LIMIT)

    try:
        await check_ip_budget(request, "verify", IP_VERIFY_LIMIT)
    except VerificationError as e:
        raise HTTPException(status_code=429, detail=e.message)

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
    key = _account_key(user)
    await _login_attempt(request, key)

    try:
        await verify_code(PURPOSE_EMAIL_2FA, username, data.code)
    except VerificationError as e:
        # A wrong guess is the thing the budget exists to count.
        await spend_ip_budget(request, "verify")
        await audit.record(
            "login_failed", actor=user, target_type="user", target_id=user.id,
            summary=f"کد ایمیل نادرست هنگام ورود: {user.username}",
            detail={"reason": "wrong_email_code"}, request=request)
        raise HTTPException(status_code=400, detail=e.message)

    await _login_passed(request, key, done=True)
    user.last_login = datetime.now(timezone.utc)
    await db.commit()
    await audit.record("login_success", actor=user, target_type="user", target_id=user.id,
                       summary=f"ورود موفق (کد ایمیل): {user.username}", request=request)
    return TokenResponse(
        access_token=create_access_token(access_claims(user)),
        token_type="bearer",
        role=user.role,
        username=user.username,
        full_name=user.full_name,
    )


@router.post("/password-reset/request")
async def password_reset_request(
    data: PasswordResetRequest,
    request: Request,
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
    from app.services.verification import (
        issue_code, VerificationError, check_ip_budget, spend_ip_budget,
        IP_CODE_LIMIT)

    # Before the lookup, so the refusal says nothing about any account: a
    # host that has asked too often is told so whether the name it sent is
    # real or not. This is the only 429 this endpoint ever returns.
    try:
        await check_ip_budget(request, "code", IP_CODE_LIMIT)
    except VerificationError as e:
        raise HTTPException(status_code=429, detail=e.message)

    ident = (data.identifier or "").strip()
    user = (await db.execute(
        select(User).where(or_(
            User.username == ident,
            func.lower(User.email) == ident.lower(),
        )).limit(1)
    )).scalars().first()

    same_answer = {"sent": True,
                   "message": "اگر این حساب وجود داشته باشد، کد بازنشانی به ایمیلش فرستاده شد"}

    # Counted for every identifier, known or not. The threat this budget
    # meets is probing — a script walking phone numbers — and probing an
    # unknown name is still probing. Counting only real hits would let the
    # walk continue for free between them, and would make the moment the
    # budget runs out depend on how many real accounts the list held.
    await spend_ip_budget(request, "code")

    if not user or not (user.email or "").strip() or not user.is_active:
        return same_answer

    try:
        await issue_code(PURPOSE_PWD_RESET, user.username, "",
                         email=user.email, channel="email", db=db)
    except VerificationError as e:
        # Swallowed, deliberately. This branch is only reachable for a REAL
        # account — an unknown name returned same_answer above and never got
        # here — so a 429 from it was an oracle: six requests for a stranger
        # answered 200 every time, six for a real user answered 200 ×5 then
        # 429. Panel usernames are phone numbers, so that enumerated which
        # numbers have accounts, which is the one thing same_answer exists to
        # prevent.
        #
        # The worry that motivated the 429 — silence leaving somebody pressing
        # the button until they are locked out — is now met by the per-IP
        # budget above, which refuses them out loud and says nothing about any
        # account. The per-identifier throttle keeps doing its job quietly.
        logger.info(f"[reset] throttled {user.username}: {e.message}")
    except Exception as e:
        logger.warning(f"[reset] could not send to {user.username}: {e}")
    return same_answer


@router.post("/password-reset/confirm")
async def password_reset_confirm(
    data: PasswordResetConfirm,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Set a new password against a code from the reset email."""
    from app.services.verification import (
        verify_code, VerificationError, check_ip_budget, spend_ip_budget,
        IP_VERIFY_LIMIT)

    try:
        await check_ip_budget(request, "verify", IP_VERIFY_LIMIT)
    except VerificationError as e:
        raise HTTPException(status_code=429, detail=e.message)

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
        await spend_ip_budget(request, "verify")
        raise HTTPException(status_code=400, detail="کد نادرست یا منقضی است")

    try:
        await verify_code(PURPOSE_PWD_RESET, user.username, data.code)
    except VerificationError as e:
        await spend_ip_budget(request, "verify")
        raise HTTPException(status_code=400, detail=e.message)

    user.hashed_password = get_password_hash(data.new_password)
    await db.commit()
    logger.info(f"[reset] password changed for {user.username}")
    return {"success": True, "message": "رمز عبور تغییر کرد — حالا وارد شوید"}


@router.post("/token/verify-totp", response_model=TokenResponse)
async def verify_totp_login(
    data: TotpLoginRequest,
    request: Request,
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
    # The session lives five minutes and takes any number of codes; this is
    # what stops it being a million-guess ticket.
    key = _account_key(user)
    await _login_attempt(request, key)

    step = _totp_step(user.totp_secret, data.code) if user.totp_secret else None
    if step is None:
        await audit.record(
            "login_failed", actor=user, target_type="user", target_id=user.id,
            summary=f"کد TOTP نادرست هنگام ورود: {user.username}",
            detail={"reason": "wrong_totp_code"}, request=request)
        raise HTTPException(status_code=401, detail="کد احراز هویت اشتباه است")
    if not await _claim_totp_step(db, user, step):
        await audit.record(
            "login_failed", actor=user, target_type="user", target_id=user.id,
            summary=f"کد TOTP تکراری هنگام ورود: {user.username}",
            detail={"reason": "totp_code_reused"}, request=request)
        raise HTTPException(status_code=401, detail=TOTP_REUSED)

    await _login_passed(request, key, done=True)
    user.last_login = datetime.now(timezone.utc)
    await db.commit()
    await audit.record("login_success", actor=user, target_type="user", target_id=user.id,
                       summary=f"ورود موفق (TOTP): {user.username}", request=request)

    return TokenResponse(
        access_token=create_access_token(access_claims(user)),
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
    actor: User = _super_admin,
    request: Request = None,
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

    await _guard_name_is_free(db, data.full_name)
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
    await audit.record("user_create", actor=actor, target_type="user", target_id=user.id,
                       summary=f"کاربر «{user.username}» ساخته شد (register)", request=request)
    return user


# ── Authenticated ─────────────────────────────────────────────────────────────

@router.get("/me/phone-gate")
async def my_phone_gate(current_user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_db)):
    """Whether the actions that lean on my number are closed to me until I
    verify it — the same answer those actions would give, asked up front, so
    the panel can open the verification popup on arrival instead of after a
    refused click."""
    from app.auth.dependencies import phone_gate_reason
    why = await phone_gate_reason(current_user, db)
    return {"required": bool(why), "message": why, "phone": current_user.phone or None,
            "phone_verified": bool(current_user.phone_verified)}


@router.post("/me/phone/request")
async def request_phone_code(data: PhoneChangeRequest,
                             current_user: User = Depends(get_current_user),
                             db: AsyncSession = Depends(get_db)):
    """Text a code to the caller's own number, so they can prove they hold it.

    SMS only, never email. A code that arrives in an inbox says nothing
    whatsoever about who holds the phone, and «تأیید شده» next to a number has
    to mean the number was answered — otherwise the badge is decoration and the
    panel is lying, which is the thing it was added to stop doing.

    Passing a number changes the one on file first. Without that an account
    whose number is wrong can never be corrected: the code would go to whoever
    actually owns the mistyped number.
    """
    from app.api.routes.sms import normalize_mobile
    from app.services.verification import issue_code, VerificationError

    target = (current_user.phone or "").strip()
    changing = False
    if data.phone:
        number = normalize_mobile(data.phone)
        if not number:
            raise HTTPException(400, "شمارهٔ موبایل معتبر نیست")
        # unique=True on the column, so a clash is a 500 at flush time unless
        # it is caught here.
        clash = (await db.execute(
            select(User).where(User.phone == number, User.id != current_user.id)
        )).scalars().first()
        if clash:
            raise HTTPException(409, "این شماره قبلاً برای حساب دیگری ثبت شده است")
        changing = number != target
        target = number

    if not target:
        raise HTTPException(400, "ابتدا شمارهٔ موبایل خود را وارد کنید")
    if current_user.phone_verified and not changing:
        return {"sent": False, "verified": True,
                "message": "این شماره قبلاً تأیید شده است"}

    # The number changes only once a code has actually gone to it.
    #
    # It used to be saved first and the code sent after. Inside the resend
    # cooldown the send was refused — but the new number was already on the
    # row, and the code still waiting was the one texted to the OLD number.
    # Typing that code «verified» a number that had never received anything,
    # and the phone gate now leans on that tick.
    try:
        issued = await issue_code(
            PURPOSE_PHONE, current_user.username, target,
            message_template="کد تأیید شمارهٔ شما در سورین‌فلو: {code}",
            channel="sms", db=db)
    except VerificationError as e:
        raise HTTPException(status_code=429, detail=e.message)

    if not issued.channel:
        # The code was created and burned but never travelled. Say so plainly:
        # «ارسال شد» over a message that did not send is how somebody ends up
        # waiting for an SMS that is never coming.
        raise HTTPException(
            status_code=503,
            detail="پیامک ارسال نشد — تنظیمات پیامک را در پنل بررسی کنید")

    if changing:
        current_user.phone = target
        current_user.phone_verified = False
        await db.commit()

    return {"sent": True, "verified": False, "phone": current_user.phone,
            "message": "کد تأیید پیامک شد"}


@router.post("/me/phone/verify")
async def confirm_phone_code(data: PhoneVerifyRequest,
                             current_user: User = Depends(get_current_user),
                             db: AsyncSession = Depends(get_db)):
    """Confirm the texted code and mark the number verified."""
    from app.services.verification import verify_code, VerificationError

    try:
        used = await verify_code(PURPOSE_PHONE, current_user.username, data.code)
    except VerificationError as e:
        raise HTTPException(status_code=400, detail=e.message)

    # verify_code returns the route the code actually travelled, and this is
    # the one place that has to care. The request asks for SMS, but a code that
    # somehow arrived by email proves the inbox and says nothing about who
    # holds the phone — and «تأیید شده» beside a number has to mean the number
    # was answered. verification.py warns about precisely this: it is how a
    # phone nobody had ever answered ended up flagged verified here before.
    if used and used != "sms":
        raise HTTPException(
            status_code=400,
            detail="این کد از راه پیامک نرسیده بود، پس تأیید شماره نیست")

    current_user.phone_verified = True
    await db.commit()
    logger.info(f"[phone] {current_user.username} verified their number")
    return {"verified": True, "message": "شمارهٔ موبایل تأیید شد"}


# ── My profile ────────────────────────────────────────────────────────────────

_USERNAME_RE = r"[A-Za-z0-9_.@+\-]{3,100}"


def _clean_links(links) -> dict:
    """Three optional links, each either a real https URL or nothing.

    Instagram may be typed as a handle — «@sorinflow» — and is stored as the
    URL it means, so the page can render every link the same way.
    """
    import re
    out = {}
    raw = links.model_dump() if hasattr(links, "model_dump") else dict(links or {})
    for key in ("website", "instagram", "linkedin"):
        v = (raw.get(key) or "").strip()
        if not v:
            continue
        if key == "instagram" and not v.lower().startswith(("http://", "https://")):
            handle = v.lstrip("@")
            if not re.fullmatch(r"[A-Za-z0-9_.]{1,30}", handle):
                raise HTTPException(400, "نام کاربری اینستاگرام معتبر نیست")
            v = f"https://instagram.com/{handle}"
        if not re.fullmatch(r"https?://\S{3,190}", v):
            raise HTTPException(400, "آدرس باید با https:// شروع شود و فاصله نداشته باشد")
        out[key] = v
    return out


def _me_response(user: User) -> UserResponse:
    data = UserResponse.model_validate(user, from_attributes=True)
    data.permissions = user_permissions(user)
    return data


@router.patch("/me")
async def update_me(data: ProfileUpdate,
                    current_user: User = Depends(get_current_user),
                    db: AsyncSession = Depends(get_db)):
    """Edit my own profile. A changed username comes back with a fresh token,
    because the token names the user by username and the old one would stop
    resolving on the very next request."""
    import re
    renamed = False
    if data.username is not None:
        u = data.username.strip()
        if not re.fullmatch(_USERNAME_RE, u):
            raise HTTPException(
                400, "نام کاربری فقط می‌تواند حرف انگلیسی، عدد و . _ @ + - داشته باشد (۳ تا ۱۰۰ نویسه)")
        if u != current_user.username:
            clash = (await db.execute(
                select(User).where(func.lower(User.username) == u.lower(),
                                   User.id != current_user.id))).scalars().first()
            if clash:
                raise HTTPException(409, "این نام کاربری قبلاً گرفته شده است")
            # the username is the owner-name of anyone without a full_name,
            # so it must not be another account's full name either
            await _guard_name_is_free(db, u, exclude_id=current_user.id)
            logger.warning(f"[profile] {current_user.username} renamed to {u}")
            current_user.username = u
            renamed = True
    if data.full_name is not None:
        await _guard_name_is_free(db, data.full_name, exclude_id=current_user.id)
        current_user.full_name = data.full_name.strip() or None
    if data.headline is not None:
        current_user.headline = data.headline.strip() or None
    if data.bio is not None:
        current_user.bio = data.bio.strip() or None
    if data.presence is not None:
        if data.presence not in PRESENCE_VALUES:
            raise HTTPException(400, "وضعیت نامعتبر است")
        current_user.presence = data.presence
    if data.links is not None:
        current_user.links = _clean_links(data.links)
    await db.commit()
    await db.refresh(current_user)
    return {"user": _me_response(current_user),
            "access_token": create_access_token(access_claims(current_user)) if renamed else None}


@router.post("/me/password")
async def change_my_password(data: PasswordChangeRequest,
                             current_user: User = Depends(get_current_user),
                             db: AsyncSession = Depends(get_db),
                             request: Request = None):
    """Change my password. Every other device is signed out: token_version
    moves, and a token minted before it is refused from then on. This
    device gets a fresh token in the response so it stays in."""
    from app.services.verification import (
        take_login_attempt, login_attempt_passed, clear_login_failures, VerificationError)

    if data.new_password == data.current_password:
        raise HTTPException(400, "رمز تازه نباید با رمز فعلی یکی باشد")
    # The same throttle as the login form. Somebody holding a stolen session
    # must not get unlimited guesses at the one thing that would let them
    # keep it — counted before the password is compared, in one Redis
    # transaction, so guesses sent together meet the cap one by one.
    key = f"name:{current_user.username}"
    try:
        await take_login_attempt(request, key)
    except VerificationError as e:
        raise HTTPException(status_code=429, detail=e.message) from None
    if not verify_password(data.current_password, current_user.hashed_password):
        raise HTTPException(400, "رمز فعلی درست نیست")
    await login_attempt_passed(request)

    current_user.hashed_password = get_password_hash(data.new_password)
    current_user.token_version = (current_user.token_version or 0) + 1
    await db.commit()
    await db.refresh(current_user)
    await clear_login_failures(key)
    logger.warning(f"[profile] {current_user.username} changed their password; "
                   f"other sessions signed out")
    await audit.record("password_change", actor=current_user, target_type="user",
                       target_id=current_user.id,
                       summary=f"{current_user.username} رمز عبور خود را تغییر داد",
                       request=request)
    return {"success": True,
            "message": "رمز عوض شد و دستگاه‌های دیگر از حساب خارج شدند",
            "access_token": create_access_token(access_claims(current_user))}


def _pending_email_key(user: User) -> str:
    return f"sorinflow:email_change:{user.id}"


@router.post("/me/email/request")
async def request_email_code(data: EmailChangeRequest,
                             current_user: User = Depends(get_current_user),
                             db: AsyncSession = Depends(get_db)):
    """Email a code to prove an address.

    With an address in the body, the code goes to THAT address and the
    account switches to it only once the code comes back. The current address
    keeps working meanwhile — it is the second factor and the recovery route,
    and a typo must not be able to replace it with something nobody reads.
    """
    from app.database import get_redis
    from app.services.email_service import valid_email
    from app.services.verification import issue_code, VerificationError

    target = (data.email or "").strip().lower()
    if target:
        if not valid_email(target):
            raise HTTPException(400, "ایمیل معتبر نیست")
        if target == (current_user.email or "").lower() and current_user.email_verified:
            return {"sent": False, "verified": True, "message": "این ایمیل قبلاً تأیید شده است"}
        clash = (await db.execute(
            select(User).where(func.lower(User.email) == target,
                               User.id != current_user.id))).scalars().first()
        if clash:
            raise HTTPException(409, "این ایمیل قبلاً برای حساب دیگری ثبت شده است")
    else:
        target = (current_user.email or "").strip().lower()
        if not target:
            raise HTTPException(400, "ابتدا یک ایمیل وارد کنید")
        if current_user.email_verified:
            return {"sent": False, "verified": True, "message": "این ایمیل قبلاً تأیید شده است"}

    try:
        issued = await issue_code(PURPOSE_EMAIL, current_user.username, "",
                                  email=target, channel="email", db=db)
    except VerificationError as e:
        raise HTTPException(status_code=429, detail=e.message)
    if issued.channel != "email":
        raise HTTPException(status_code=503,
                            detail="ایمیل ارسال نشد — تنظیمات ایمیل را در پنل بررسی کنید")

    # Remembered only until the code would have expired anyway, plus slack
    # for a slow inbox. Verifying reads it back; a new request overwrites it.
    try:
        r = await get_redis()
        await r.setex(_pending_email_key(current_user), 900, target)
    except Exception as e:
        logger.warning(f"[profile] could not remember pending email: {e}")
        raise HTTPException(status_code=503, detail="سرویس موقتاً در دسترس نیست")
    return {"sent": True, "verified": False, "email": _mask_email(target),
            "message": "کد تأیید به ایمیل فرستاده شد"}


@router.post("/me/email/verify")
async def confirm_email_code(data: EmailVerifyRequest,
                             current_user: User = Depends(get_current_user),
                             db: AsyncSession = Depends(get_db)):
    """Confirm the emailed code: the pending address becomes the address,
    verified."""
    from app.database import get_redis
    from app.services.verification import verify_code, VerificationError

    try:
        used = await verify_code(PURPOSE_EMAIL, current_user.username, data.code)
    except VerificationError as e:
        raise HTTPException(status_code=400, detail=e.message)
    if used and used != "email":
        raise HTTPException(400, "این کد از راه ایمیل نرسیده بود، پس تأیید ایمیل نیست")

    pending = None
    try:
        r = await get_redis()
        key = _pending_email_key(current_user)
        pending = await r.get(key)
        await r.delete(key)
    except Exception as e:
        logger.warning(f"[profile] could not read pending email: {e}")
    if isinstance(pending, bytes):
        pending = pending.decode()
    target = (pending or current_user.email or "").strip().lower()
    if not target:
        raise HTTPException(400, "ایمیلی برای تأیید ثبت نشده است")
    if target != (current_user.email or "").lower():
        clash = (await db.execute(
            select(User).where(func.lower(User.email) == target,
                               User.id != current_user.id))).scalars().first()
        if clash:
            raise HTTPException(409, "این ایمیل در این فاصله برای حساب دیگری ثبت شد")
        current_user.email = target
    current_user.email_verified = True
    await db.commit()
    logger.info(f"[profile] {current_user.username} verified email {_mask_email(target)}")
    return {"verified": True, "email": target, "message": "ایمیل تأیید شد"}


# ── avatar ──
MAX_AVATAR_BYTES = 5 * 1024 * 1024
AVATAR_SIZE = 400


def _avatar_dir():
    from pathlib import Path
    from app.config import get_settings
    return Path(get_settings().images_path) / "avatars"


def _drop_avatar_file(token: str | None) -> None:
    if not token:
        return
    try:
        (_avatar_dir() / f"{token}.jpg").unlink(missing_ok=True)
    except Exception as e:
        logger.warning(f"[profile] could not remove old avatar {token}: {e}")


@router.post("/me/avatar")
async def upload_my_avatar(file: UploadFile = File(...),
                           current_user: User = Depends(get_current_user),
                           db: AsyncSession = Depends(get_db)):
    """Set my picture.

    Whatever arrives is decoded and re-encoded: a 400×400 JPEG, centre-cropped,
    orientation applied, no metadata carried over. A crafted file that is not
    an image is refused at decode; one that is gets flattened into pixels and
    nothing else. Stored under a random token, so the URL is not guessable and
    changes on every upload — which is also what makes a browser drop the
    cached old picture.
    """
    import io
    import secrets
    from PIL import Image, ImageOps

    raw = await file.read(MAX_AVATAR_BYTES + 1)
    if len(raw) > MAX_AVATAR_BYTES:
        raise HTTPException(413, "حجم تصویر باید کمتر از ۵ مگابایت باشد")
    if not raw:
        raise HTTPException(400, "فایلی دریافت نشد")
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
        img = ImageOps.exif_transpose(img)
        img = ImageOps.fit(img.convert("RGB"), (AVATAR_SIZE, AVATAR_SIZE),
                           method=Image.LANCZOS, centering=(0.5, 0.5))
    except Exception:
        raise HTTPException(400, "فایل تصویر معتبر نیست (JPG یا PNG بفرستید)")

    token = secrets.token_hex(12)
    folder = _avatar_dir()
    folder.mkdir(parents=True, exist_ok=True)
    img.save(folder / f"{token}.jpg", "JPEG", quality=85, optimize=True)

    old = current_user.avatar_token
    current_user.avatar_token = token
    await db.commit()
    _drop_avatar_file(old)
    logger.info(f"[profile] {current_user.username} changed their avatar")
    return {"avatar_url": current_user.avatar_url}


@router.delete("/me/avatar")
async def delete_my_avatar(current_user: User = Depends(get_current_user),
                           db: AsyncSession = Depends(get_db)):
    old = current_user.avatar_token
    current_user.avatar_token = None
    await db.commit()
    _drop_avatar_file(old)
    return {"avatar_url": None}


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


@router.get("/me/ip")
async def get_my_ip(request: Request, current_user: User = Depends(get_current_user)):
    """The address the server sees you coming from. How to check, without a
    shell on the server, that the per-address limits see real callers — before
    externalTrafficPolicy: Local every request came from k3s's own 10.42.x.x."""
    from app.services.verification import client_ip
    return {"ip": client_ip(request)}


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
    request: Request = None,
):
    if not current_user.totp_secret:
        raise HTTPException(status_code=400, detail="ابتدا TOTP را راه‌اندازی کنید")
    # Spent here too: otherwise the code that switched TOTP on also finishes
    # the next login, for the same minute and a half.
    step = _totp_step(current_user.totp_secret, data.code)
    if step is None:
        raise HTTPException(status_code=400, detail="کد احراز هویت اشتباه است")
    if not await _claim_totp_step(db, current_user, step):
        raise HTTPException(status_code=400, detail=TOTP_REUSED)

    current_user.totp_enabled = True
    await db.commit()
    await audit.record("totp_enable", actor=current_user, target_type="user",
                       target_id=current_user.id,
                       summary=f"{current_user.username} احراز هویت دومرحله‌ای (برنامه) را فعال کرد",
                       request=request)
    return {"success": True, "message": "احراز هویت دو مرحله‌ای فعال شد"}


class Email2faIn(BaseModel):
    enabled: bool = False


@router.post("/me/email-2fa")
async def set_email_2fa(
    data: Email2faIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    request: Request = None,
):
    """Turn the emailed second factor on or off for the caller's own account.

    Refused without an address on file: enabling it otherwise locks the account
    out of its own panel, and the only way back would be the database — which
    is precisely the hole this feature exists to close.
    """
    want = data.enabled
    if want and not (current_user.email or "").strip():
        raise HTTPException(
            status_code=400,
            detail="برای این کار باید ایمیل حسابتان ثبت شده باشد")

    current_user.email_2fa_enabled = want
    await db.commit()
    await audit.record(
        "email_2fa_enable" if want else "email_2fa_disable",
        actor=current_user, target_type="user", target_id=current_user.id,
        summary=f"{current_user.username} احراز هویت دومرحله‌ای (ایمیل) را "
                f"{'فعال' if want else 'غیرفعال'} کرد",
        request=request)
    return {
        "enabled": want,
        "email": _mask_email(current_user.email or ""),
        "message": ("ورود دو مرحله‌ای با ایمیل فعال شد"
                    if want else "ورود دو مرحله‌ای با ایمیل غیرفعال شد"),
    }


class DivarPhoneIn(BaseModel):
    divar_phone: Optional[str] = Field(None, max_length=20)


@router.patch("/me/divar-phone", response_model=UserResponse)
async def update_my_divar_phone(
    data: DivarPhoneIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Set or clear my primary Divar number.

    Validated and normalised (Persian digits, +98, spaces) to the same
    09xxxxxxxxx the sessions table stores — the pill and the pool compare
    on it. It used to take any string at all, into the field that scopes
    what a person's runs use (roadmap #3).
    """
    from app.api.routes.sms import normalize_mobile
    raw = (data.divar_phone or "").strip()
    phone = None
    if raw:
        phone = normalize_mobile(raw)
        if not phone:
            raise HTTPException(400, "شمارهٔ دیوار معتبر نیست (مثل 09123456789)")
        # Not somebody else's. This field is «this number is mine», and the
        # boot-time backfill hands an unowned session to whoever's field
        # names it — so claiming a colleague's number here was a slow way of
        # getting it.
        from app.models.cookie import Cookie
        want = "".join(ch for ch in phone if ch.isdigit())[-10:]
        for ph, owner in (await db.execute(
                select(Cookie.phone_number, Cookie.owner_user_id))).all():
            if owner and owner != current_user.id \
                    and "".join(ch for ch in str(ph) if ch.isdigit())[-10:] == want:
                raise HTTPException(403, "این شمارهٔ دیوار متعلق به کاربر دیگری است")
    if phone != current_user.divar_phone:
        logger.info(f"[profile] {current_user.username} primary Divar number: "
                    f"{current_user.divar_phone or '—'} -> {phone or '—'}")
    current_user.divar_phone = phone
    await db.commit()
    await db.refresh(current_user)
    return _me_response(current_user)


@router.post("/me/totp/disable")
async def totp_disable(
    data: TotpDisableRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    request: Request = None,
):
    from app.services.verification import clear_login_failures

    # The same budget «change password» uses (keyed on the username): a
    # stolen session must not get unlimited guesses at the password here
    # instead. One key for both halves — spending and clearing different
    # keys left the real counter never reset, so it grew a little on every
    # legitimate use and eventually locked the account out over nothing but
    # correct passwords.
    key = f"name:{current_user.username}"
    await _login_attempt(None, key)
    if not verify_password(data.password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="رمز عبور اشتباه است")
    await clear_login_failures(key)

    current_user.totp_enabled = False
    current_user.totp_secret = None
    await db.commit()
    await audit.record("totp_disable", actor=current_user, target_type="user",
                       target_id=current_user.id,
                       summary=f"{current_user.username} احراز هویت دومرحله‌ای (برنامه) را غیرفعال کرد",
                       request=request)
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
    actor: User = _super_admin,
    db: AsyncSession = Depends(get_db),
    request: Request = None,
):
    _guard_role_assignment(actor, data.role)
    # Check duplicate username / email
    existing = await db.execute(select(User).where(User.username == data.username))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="نام کاربری قبلاً ثبت شده است")

    if data.email:
        dup_email = await db.execute(select(User).where(User.email == data.email))
        if dup_email.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="ایمیل قبلاً ثبت شده است")

    await _guard_name_is_free(db, data.full_name)
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
    await audit.record("user_create", actor=actor, target_type="user", target_id=user.id,
                       summary=f"کاربر «{user.username}» ساخته شد (نقش {user.role})",
                       request=request)
    return user


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: int,
    data: UserUpdate,
    actor: User = _super_admin,
    db: AsyncSession = Depends(get_db),
    request: Request = None,
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="کاربر یافت نشد")
    _guard_root_target(actor, user)
    _guard_role_assignment(actor, data.role)

    # Field names only, for the audit trail — never the values themselves,
    # since one of them is the permission list and another the phone number.
    changed = [name for name, v in (
        ("ایمیل", data.email), ("نام", data.full_name), ("نقش", data.role),
        ("فعال/غیرفعال", data.is_active), ("شمارهٔ دیوار", data.divar_phone),
        ("موبایل", data.phone), ("دسترسی‌ها", data.permissions))
        if v is not None]

    if data.email is not None:
        new_email = (data.email or "").strip() or None
        if new_email and new_email.lower() != (user.email or "").lower():
            clash = (await db.execute(select(User).where(
                func.lower(User.email) == new_email.lower(), User.id != user.id))).scalars().first()
            if clash:
                raise HTTPException(409, "این ایمیل قبلاً برای حساب دیگری ثبت شده است")
        # A different address is an unproven one. Keeping the tick would
        # vouch for an inbox nobody has opened a code in.
        if (new_email or "").lower() != (user.email or "").lower():
            user.email_verified = False
        user.email = new_email
    if data.full_name is not None:
        await _guard_name_is_free(db, data.full_name, exclude_id=user.id)
        user.full_name = data.full_name
    elif data.role in STAFF_ROLES and user.role not in STAFF_ROLES:
        # a visitor made staff joins the colleagues a form names, with the
        # name they picked at sign-up — the same check as the portal ticket
        await _guard_name_is_free(db, user.full_name or user.username, exclude_id=user.id)
    if data.role is not None:
        user.role = data.role
    if data.is_active is not None:
        user.is_active = data.is_active
    if data.divar_phone is not None:
        dp = (data.divar_phone or "").strip() or None
        if dp:
            # The boot backfill hands an unowned session to whoever's
            # divar_phone names it; naming somebody else's number here was a
            # slow way of moving it. Same rule as /me/divar-phone.
            from app.models.cookie import Cookie
            want = "".join(ch for ch in dp if ch.isdigit())[-10:]
            for ph, owner in (await db.execute(
                    select(Cookie.phone_number, Cookie.owner_user_id))).all():
                if owner and owner != user.id \
                        and "".join(ch for ch in str(ph) if ch.isdigit())[-10:] == want:
                    raise HTTPException(403, "این شمارهٔ دیوار متعلق به کاربر دیگری است")
        user.divar_phone = dp
    if data.phone is not None:
        from app.api.routes.sms import normalize_mobile
        raw = (data.phone or "").strip()
        new_phone = normalize_mobile(raw) if raw else None
        if raw and not new_phone:
            raise HTTPException(400, "شمارهٔ موبایل معتبر نیست (مثل 09123456789)")
        if new_phone and new_phone != user.phone:
            # Unique index on the column: a clash is a 500 at flush time
            # unless it is caught here.
            clash = (await db.execute(select(User).where(
                User.phone == new_phone, User.id != user.id))).scalars().first()
            if clash:
                raise HTTPException(409, "این شماره قبلاً برای حساب دیگری ثبت شده است")
        # Same as the address: a changed number has not answered a code.
        if new_phone != user.phone:
            user.phone_verified = False
        user.phone = new_phone
    if data.permissions is not None:
        user.permissions = normalize_permissions(data.permissions)

    await db.commit()
    await db.refresh(user)
    if changed:
        await audit.record(
            "user_update", actor=actor, target_type="user", target_id=user.id,
            summary=f"کاربر «{user.username}» ویرایش شد: " + "، ".join(changed),
            detail={"fields": changed}, request=request)
    return user


class VerificationFlagsIn(BaseModel):
    phone_verified: Optional[bool] = None
    email_verified: Optional[bool] = None


@router.patch("/{user_id}/verification", response_model=UserResponse)
async def set_verification_flags(
    user_id: int,
    data: VerificationFlagsIn,
    request: Request,
    current_user: User = Depends(_role_dep(ROLE_ROOT)),
    db: AsyncSession = Depends(get_db),
):
    """Mark a user's phone or email verified — or not — by hand.

    «فقط اکانت root می‌تواند به صورت دستی و با تاگل، شماره و ایمیل کاربران را
    تأیید کند.» root only; super_admin cannot, because a verified tick is a
    statement the whole panel then relies on (the phone gate, SMS audiences,
    «قابل بازیابی»), and it should have exactly one author besides the code
    itself.

    Only for something that exists: a tick next to an empty phone or address
    would verify nothing. Every change is written to the log at WARNING —
    who, from where, for whom — because it is the one way a tick appears
    without a code being answered.
    """
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="کاربر یافت نشد")
    if data.phone_verified is None and data.email_verified is None:
        raise HTTPException(status_code=400, detail="چیزی برای تغییر فرستاده نشد")
    if data.phone_verified and not (user.phone or "").strip():
        raise HTTPException(status_code=400, detail="این کاربر شمارهٔ موبایلی ثبت نکرده است")
    if data.email_verified and not (user.email or "").strip():
        raise HTTPException(status_code=400, detail="این کاربر ایمیلی ثبت نکرده است")

    changes = []
    if data.phone_verified is not None and bool(user.phone_verified) != data.phone_verified:
        user.phone_verified = data.phone_verified
        changes.append(f"phone {user.phone} -> {'verified' if data.phone_verified else 'unverified'}")
    if data.email_verified is not None and bool(user.email_verified) != data.email_verified:
        user.email_verified = data.email_verified
        changes.append(f"email {user.email} -> {'verified' if data.email_verified else 'unverified'}")
    await db.commit()
    await db.refresh(user)
    if changes:
        _ip = request.client.host if request.client else "?"
        logger.warning(f"[audit] {current_user.username} (root) from {_ip} set "
                       f"{user.username}: {'; '.join(changes)}")
        await audit.record(
            "user_verification_set", actor=current_user, target_type="user", target_id=user.id,
            summary=f"تأیید دستی برای «{user.username}»: " + "، ".join(changes),
            request=request)
    return user


@router.post("/{user_id}/password")
async def reset_password(
    user_id: int,
    data: UserPasswordReset,
    actor: User = _super_admin,
    db: AsyncSession = Depends(get_db),
    request: Request = None,
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="کاربر یافت نشد")
    _guard_root_target(actor, user)

    user.hashed_password = get_password_hash(data.new_password)
    await db.commit()
    await audit.record(
        "user_password_reset_admin", actor=actor, target_type="user", target_id=user.id,
        summary=f"رمز عبور «{user.username}» توسط «{actor.username}» بازنشانی شد",
        request=request)
    return {"success": True, "message": "رمز عبور با موفقیت تغییر کرد"}


@router.delete("/{user_id}")
async def delete_user(
    user_id: int,
    current_user: User = _super_admin,
    db: AsyncSession = Depends(get_db),
    request: Request = None,
):
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="نمی‌توانید حساب خودتان را حذف کنید")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="کاربر یافت نشد")
    _guard_root_target(current_user, user)

    # Captured before the delete: the row (and the id/username it would
    # otherwise carry into the audit record) is gone after commit.
    target_id, target_username = user.id, user.username
    await db.delete(user)
    await db.commit()
    await audit.record("user_delete", actor=current_user, target_type="user",
                       target_id=target_id, summary=f"کاربر «{target_username}» حذف شد",
                       request=request)
    return {"success": True, "message": "کاربر حذف شد"}


@router.post("/{user_id}/totp/disable")
async def admin_disable_totp(
    user_id: int,
    actor: User = _super_admin,
    db: AsyncSession = Depends(get_db),
    request: Request = None,
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="کاربر یافت نشد")
    _guard_root_target(actor, user)

    user.totp_enabled = False
    user.totp_secret = None
    await db.commit()
    await audit.record(
        "user_totp_disable_admin", actor=actor, target_type="user", target_id=user.id,
        summary=f"احراز هویت دومرحله‌ای «{user.username}» توسط «{actor.username}» غیرفعال شد",
        request=request)
    return {"success": True, "message": "احراز هویت دو مرحله‌ای کاربر غیرفعال شد"}


@router.post("/{user_id}/verification-request")
async def request_verification(user_id: int,
                               actor: User = _super_admin,
                               db: AsyncSession = Depends(get_db)):
    """Ask a person to verify what is still unverified on their account.

    Not a code: the code lives three minutes and would be dead before most
    people open the email. A message saying what to do and a link to the
    profile page, where «ارسال کد» mints a fresh one when they are actually
    looking. Once an hour per person, so a stuck badge cannot become spam.
    """
    from app.config import get_settings
    from app.database import get_redis
    from app.services import email_service, email_templates
    from app.services.sms_service import send_sms

    target = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="کاربر یافت نشد")
    _guard_root_target(actor, target)

    need_email = bool((target.email or "").strip()) and not target.email_verified
    need_phone = bool((target.phone or "").strip()) and not target.phone_verified
    if not (need_email or need_phone):
        raise HTTPException(400, "چیزی برای تأیید نمانده است")

    try:
        r = await get_redis()
        if not await r.set(f"sorinflow:verify_nudge:{target.id}", "1", ex=3600, nx=True):
            raise HTTPException(429, "در یک ساعت گذشته برای این کاربر درخواست فرستاده شده است")
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"[profile] nudge limiter unavailable, allowing: {e}")

    settings = get_settings()
    who = actor.full_name or actor.username
    profile_url = f"https://{(settings.domain or 'sorinflow.com')}/dashboard/#/profile"
    sent = {"email": False, "sms": False}

    if need_email:
        try:
            what = "ایمیل" + (" و شمارهٔ موبایل" if need_phone else "")
            subj, html, text = email_templates.notification(
                f"لطفاً {what} خود را تأیید کنید",
                f"{who} از شما خواسته {what} خود را در سورین‌فلو تأیید کنید. "
                "وارد پنل شوید، به «پروفایل» بروید و کنار هر مورد «ارسال کد» را بزنید.",
                cta_label="باز کردن پروفایل", cta_url=profile_url)
            res = await email_service.send(target.email, subj, html, text, db=db)
            sent["email"] = bool(res.get("success"))
        except Exception as e:
            logger.warning(f"[profile] nudge email to user {target.id} failed: {e}")
    if need_phone:
        try:
            res = await send_sms(
                target.phone,
                f"سورین‌فلو: {who} از شما خواسته شمارهٔ موبایل خود را تأیید کنید. "
                f"پنل ← پروفایل ← «ارسال کد». {profile_url}",
                provider=settings.auth_sms_provider, db=db)
            sent["sms"] = bool(res.get("success"))
        except Exception as e:
            logger.warning(f"[profile] nudge sms to user {target.id} failed: {e}")

    if not (sent["email"] or sent["sms"]):
        try:
            await (await get_redis()).delete(f"sorinflow:verify_nudge:{target.id}")
        except Exception:
            pass
        raise HTTPException(503, "هیچ پیامی ارسال نشد — تنظیمات ایمیل و پیامک را بررسی کنید")

    logger.info(f"[profile] {actor.username} asked user {target.id} to verify: {sent}")
    channels = [n for n, ok in (("ایمیل", sent["email"]), ("پیامک", sent["sms"])) if ok]
    return {"sent": sent, "message": "درخواست از راه " + " و ".join(channels) + " فرستاده شد"}
