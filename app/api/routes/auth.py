"""
SorinFlow Divar Scraper - Authentication API Routes
"""
import logging
from datetime import datetime
from fastapi import Request, APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Any, List, Optional

from app.database import get_db
from app.auth.dependencies import get_current_user
from app.models.cookie import Cookie
from app.models.user import User
from app.scraper.auth import DivarAuth
from app.config import get_settings
from app.auth.dependencies import get_current_user_optional
from app.schemas import (
    LoginRequest,
    OTPVerifyRequest,
    CookieStatusResponse,
    AuthResponse
)

logger = logging.getLogger(__name__)

router = APIRouter()
settings = get_settings()

# A live Chromium per in-flight Divar login, kept between the phone step and
# the OTP step because the session lives in that browser.
#
# It only ever got cleaned up on a SUCCESSFUL OTP. Every abandoned login — a
# wrong code, a closed tab, a timeout — left a headless Chromium running for
# the life of the pod, and starting a second attempt for the same number
# overwrote the dict entry, dropping the only handle to the previous one. On a
# single-replica box a handful of those is most of the CPU.
auth_instances = {}
_auth_started = {}          # phone -> monotonic time the browser was launched

# A login nobody finished. Generous: the OTP itself has a 30s wait and people
# go and find their phone.
AUTH_INSTANCE_TTL = 600


async def _discard_auth_instance(phone_number: str, why: str):
    """Close and forget one login browser. Never raises — a browser that is
    already gone must not turn into a 500 on somebody else's request."""
    auth = auth_instances.pop(phone_number, None)
    _auth_started.pop(phone_number, None)
    if auth is None:
        return
    try:
        await auth.close_browser()
        logger.info(f"[auth] closed login browser for {phone_number} ({why})")
    except Exception as e:
        logger.warning(f"[auth] could not close browser for {phone_number}: "
                       f"{type(e).__name__}: {e}")


async def _sweep_auth_instances():
    """Close logins nobody came back to finish."""
    import time as _time
    now = _time.monotonic()
    stale = [p for p, t in _auth_started.items() if now - t > AUTH_INSTANCE_TTL]
    for phone in stale:
        await _discard_auth_instance(phone, f"abandoned for {AUTH_INSTANCE_TTL}s")


@router.post("/login", response_model=AuthResponse)
async def initiate_login(
    request: LoginRequest,
    db: AsyncSession = Depends(get_db)
):
    """Initiate login with phone number"""
    
    phone_number = request.phone_number
    
    # Anything left from a previous attempt for this number is finished with;
    # replacing the dict entry without closing it leaks the browser.
    await _discard_auth_instance(phone_number, "superseded by a new login")
    await _sweep_auth_instances()

    import time as _time
    auth = DivarAuth(db)
    auth_instances[phone_number] = auth
    _auth_started[phone_number] = _time.monotonic()
    
    try:
        result = await auth.login_with_phone(phone_number)
        
        return AuthResponse(
            success=result.get("success", False),
            message=result.get("message", ""),
            requires_code=result.get("requires_code", False)
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/verify", response_model=AuthResponse)
async def verify_otp(
    request: OTPVerifyRequest,
    phone_number: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_optional),
):
    """Verify OTP code and complete login"""
    
    if phone_number not in auth_instances:
        raise HTTPException(
            status_code=400,
            detail="No login session found. Please initiate login first."
        )
    
    auth = auth_instances[phone_number]
    
    try:
        result = await auth.submit_otp_code(request.code, phone_number)
        
        if result.get("success"):
            # The first number somebody logs in becomes their primary — the
            # one the header pill and «خودکار» start from. A second or third
            # number joins their pool without replacing it: which one is
            # primary is chosen on the profile page, not by login order.
            if current_user and not (current_user.divar_phone or "").strip():
                current_user.divar_phone = phone_number
                # flush so the cookie-save below sees the updated user
                await db.flush()

            # Ensure cookies are saved to database
            cookies = result.get("cookies", [])
            if cookies:
                # Look for token cookie first, then other auth cookies
                from app.services.divar_session import auth_cookie as _auth_cookie
                token_cookie = _auth_cookie(cookies)
                if not token_cookie:
                    # Look for other auth cookies
                    auth_cookies = [c for c in cookies if any(keyword in c.get("name", "").lower() for keyword in ["auth", "session", "user", "login", "jwt", "bearer"])]
                    token_cookie = auth_cookies[0] if auth_cookies else None
                
                token_value = token_cookie.get("value") if token_cookie else None
                
                # Check if cookie already exists
                existing = await db.execute(
                    select(Cookie).where(Cookie.phone_number == phone_number)
                )
                existing_cookie = existing.scalar_one_or_none()
                
                from datetime import datetime
                from app.services.divar_session import derive_expiry
                expires_at = derive_expiry(cookies)
                
                if existing_cookie:
                    existing_cookie.cookies = cookies
                    existing_cookie.token = token_value
                    existing_cookie.is_valid = True
                    existing_cookie.expires_at = expires_at
                    existing_cookie.updated_at = datetime.now()
                    # Whoever answered Divar's code holds the phone; the
                    # session it bought is theirs even if a previous owner
                    # had logged this number in before.
                    if current_user and existing_cookie.owner_user_id != current_user.id:
                        if existing_cookie.owner_user_id:
                            logger.warning(
                                f"[auth] session {phone_number} changes hands: "
                                f"user {existing_cookie.owner_user_id} -> {current_user.id}")
                        existing_cookie.owner_user_id = current_user.id
                else:
                    new_cookie = Cookie(
                        phone_number=phone_number,
                        cookies=cookies,
                        token=token_value,
                        is_valid=True,
                        expires_at=expires_at,
                        # Whoever answered Divar's code owns the session it
                        # bought — the same rule the import path follows.
                        owner_user_id=current_user.id if current_user else None,
                    )
                    db.add(new_cookie)
                
                await db.commit()
            
            # Cleanup auth instance
            await _discard_auth_instance(phone_number, "login completed")
        
        return AuthResponse(
            success=result.get("success", False),
            message=result.get("message", ""),
            requires_code=False
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status", response_model=CookieStatusResponse)
async def get_cookie_status(
    phone_number: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_optional),
):
    """The session the header pill shows and the scraper's «خودکار» uses.

    Scoped to the caller's own numbers, like the list. This used to fall back
    to «any valid session in the DB», which handed one user another user's
    number — and because the scrape form takes what this returns, the run
    then logged that number in and spent its reveals. Ownership enforced on
    the list and the pool but not here was ownership with a side door.
    """
    phone = phone_number or (current_user.divar_phone if current_user else None) \
        or settings.divar_phone_number

    auth = DivarAuth(db)

    def _mine(q):
        return _usable_by(q, current_user)

    # The named number counts only if the session behind it is the caller's
    # own. For every role: root can see and reassign everybody's sessions,
    # but the pill, the scraper and refresh only ever touch what root owns.
    named = None
    if phone:
        named = (await db.execute(
            _mine(select(Cookie).where(Cookie.phone_number == phone))
        )).scalar_one_or_none()
        if named:
            status = await auth.get_cookie_status(phone)
            if status.get("is_valid"):
                return CookieStatusResponse(**status)

    # Otherwise the caller's most recently used valid session — never
    # somebody else's.
    fallback = (await db.execute(
        _mine(select(Cookie).where(Cookie.is_valid == True))
        .order_by(Cookie.updated_at.desc())
        .limit(1)
    )).scalar_one_or_none()
    if fallback:
        return CookieStatusResponse(**await auth.get_cookie_status(fallback.phone_number))
    if named:
        # Their own number, expired: say that, not «nothing configured».
        return CookieStatusResponse(**await auth.get_cookie_status(phone))
    return CookieStatusResponse(
        has_cookies=False,
        is_valid=False,
        phone_number="",
        message="No phone number configured"
    )


async def _own_session_or_403(db, user, phone: Optional[str]) -> str:
    """Resolve the number an action is about, and refuse it unless the
    session behind it belongs to the caller. Refresh and logout used to take
    any number at all — a valid way for one person to log another out."""
    phone = phone or (user.divar_phone if user else None) or settings.divar_phone_number
    if not phone:
        raise HTTPException(status_code=400, detail="No phone number provided")
    row = (await db.execute(
        select(Cookie).where(Cookie.phone_number == phone))).scalar_one_or_none()
    if row and (not user or row.owner_user_id != user.id):
        raise HTTPException(
            status_code=403, detail="این شماره به حساب کاربری دیگری تعلق دارد")
    return phone


@router.post("/refresh")
async def refresh_session(
    phone_number: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Attempt to refresh/validate session"""

    phone = await _own_session_or_403(db, user, phone_number)
    
    auth = DivarAuth(db)
    
    try:
        success = await auth.restore_session(phone)
        await auth.close_browser()
        
        if success:
            return {"success": True, "message": "Session refreshed successfully"}
        else:
            return {"success": False, "message": "Session expired. Please login again."}
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/logout")
async def logout(
    phone_number: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Invalidate stored cookies and logout"""

    phone = await _own_session_or_403(db, user, phone_number)
    
    auth = DivarAuth(db)
    success = await auth.invalidate_cookies(phone)
    
    if success:
        return {"success": True, "message": "Logged out successfully"}
    else:
        return {"success": False, "message": "Failed to logout"}


def _sees_every_session(user) -> bool:
    """root and super_admin see the whole pool; everyone else sees their own.

    Not a convenience: somebody has to be able to reassign a session when a
    person leaves, and to notice a number nobody has claimed.
    """
    return bool(user) and (user.role or "") in ("root", "super_admin")


def _usable_by(query, user):
    """Narrow a cookies query to what `user` may USE — their own, whatever the
    role. root sees and reassigns everybody's sessions; root does not scrape
    on somebody else's number."""
    if not user:
        return query.where(Cookie.id == -1)
    return query.where(Cookie.owner_user_id == user.id)


def _own_sessions_only(query, user):
    """Narrow a cookies query to what `user` may see."""
    if _sees_every_session(user):
        return query
    if not user:
        # No identity, no sessions. Safer than «all» for a query whose rows
        # are live Divar credentials.
        return query.where(Cookie.id == -1)
    return query.where(Cookie.owner_user_id == user.id)


@router.get("/cookies")
async def list_cookies(
    mine: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The Divar sessions this user may see — theirs, or all of them for an
    admin who has to be able to reassign one.

    `mine=1` is what the panel asks for: the sessions this person may USE,
    whatever their role. Without it, root's header pill, status box, saved
    list and scraper picker were handed the whole pool and presented a
    colleague's number as «شمارهٔ فعال» — while the run itself would have
    refused that number. Sessions are personal; the display is now too.
    """
    scope = _usable_by if mine else _own_sessions_only
    result = await db.execute(scope(select(Cookie), current_user))
    cookies = result.scalars().all()
    owners = {}
    if _sees_every_session(current_user) and not mine:
        ids = {c.owner_user_id for c in cookies if c.owner_user_id}
        if ids:
            owners = {u.id: (u.full_name or u.username) for u in (await db.execute(
                select(User).where(User.id.in_(ids)))).scalars().all()}

    return {
        "can_reassign": _sees_every_session(current_user) and not mine,
        "cookies": [
            {
                "id": c.id,
                "phone_number": c.phone_number,
                "is_valid": c.is_valid,
                "expires_at": c.expires_at.isoformat() if c.expires_at else None,
                # When Divar last actually answered about this session. The
                # header pill reads it so it can stop presenting an untested
                # belief as a confirmed fact.
                "last_checked_at": c.last_checked_at.isoformat() if c.last_checked_at else None,
                "created_at": c.created_at.isoformat() if c.created_at else None,
                # What the choice should be made on: a number Divar has been
                # asked 200 times is not interchangeable with a fresh one.
                "reveals": c.reveals or 0,
                "challenged_at": c.challenged_at.isoformat() if c.challenged_at else None,
                "last_used_at": c.last_used_at.isoformat() if c.last_used_at else None,
                "owner_user_id": c.owner_user_id,
                "identity_required_at": c.identity_required_at.isoformat() if c.identity_required_at else None,
                # Only filled for an admin — nobody else is shown a list that
                # could include somebody else's row in the first place.
                "owner_name": owners.get(c.owner_user_id),
            }
            for c in cookies
        ]
    }


class CookieImportRequest(BaseModel):
    phone_number: str
    cookies: List[Any]


@router.post("/cookies/import")
async def import_cookies(
    request: CookieImportRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_optional),
):
    """Import cookies exported from a browser (e.g. the EditThisCookie extension).

    The import asks Divar whether what was pasted actually works, and reports
    that answer. It used to store the row with is_valid=True without checking
    anything — so the panel said the session was fine, and the person only found
    out it was not when a different screen contradicted it minutes later and
    sent them back to the login form.

    A jar with no `token` cookie is refused outright. That is the usual mistake:
    the extension was copied while on the wrong domain, or only part of the list
    was selected, and no amount of retrying the login will fix it.
    """
    if not request.cookies:
        raise HTTPException(status_code=400, detail="هیچ کوکی‌ای ارسال نشد")

    # Find token cookie to extract expiry
    from app.services.divar_session import auth_cookie as _auth_cookie
    token_cookie = _auth_cookie(request.cookies)
    token_value = token_cookie.get("value") if token_cookie else None

    if not token_value:
        from app.services.divar_session import AUTH_COOKIE_NAMES
        names = [str(c.get("name")) for c in request.cookies if c.get("name")][:10]
        wanted = "، ".join(AUTH_COOKIE_NAMES)
        raise HTTPException(
            status_code=400,
            detail=(f"کوکی نشست ({wanted}) در آنچه وارد کردید وجود ندارد و بدون آن "
                    "نشست کار نمی‌کند. مطمئن شوید هنگام کپی، در دامنهٔ divar.ir وارد "
                    "حساب بوده‌اید و همهٔ کوکی‌ها را انتخاب کرده‌اید. "
                    f"آنچه دریافت شد: {'، '.join(names) or 'خالی'}"))

    from app.services.divar_session import derive_expiry
    expires_at = derive_expiry(request.cookies)

    result = await db.execute(select(Cookie).where(Cookie.phone_number == request.phone_number))
    existing = result.scalar_one_or_none()

    if existing:
        existing.cookies = request.cookies
        if (existing.owner_user_id and current_user
                and existing.owner_user_id != current_user.id
                and not _sees_every_session(current_user)):
            raise HTTPException(
                status_code=403,
                detail="این شماره به حساب کاربری دیگری تعلق دارد")
        existing.token = token_value
        existing.is_valid = True
        existing.expires_at = expires_at
        existing.updated_at = datetime.now()
        if not existing.owner_user_id and current_user:
            existing.owner_user_id = current_user.id
    else:
        db.add(Cookie(
            phone_number=request.phone_number,
            cookies=request.cookies,
            token=token_value,
            is_valid=True,
            expires_at=expires_at,
            # Whoever pasted it owns it. A session with no owner is one nobody
            # can be asked about.
            owner_user_id=current_user.id if current_user else None,
        ))

    # Auto-link this Divar phone to the current dashboard user
    if current_user:
        current_user.divar_phone = request.phone_number

    await db.commit()

    # Now actually ask Divar, and let the answer be the answer.
    from app.services import divar_session
    row = (await db.execute(
        select(Cookie).where(Cookie.phone_number == request.phone_number)
        .order_by(Cookie.updated_at.desc().nullslast()).limit(1)
    )).scalar_one_or_none()

    check = {}
    if row is not None:
        try:
            # confirm=True: a single 403 is not proof. Telling someone their
            # freshly-pasted cookies were rejected, when Divar's edge simply
            # refused one request, sends them back to re-copy a jar that was
            # fine.
            check = await divar_session.check_and_record(db, row, confirm=True)
        except Exception as e:
            # A failed probe must not lose the import — the row is already
            # saved, and the verifier loop will test it shortly.
            logger.warning(f"[cookies] import saved but probe failed: {type(e).__name__}: {e}")

    alive = check.get("alive")
    if alive is True:
        msg = f"کوکی‌ها وارد و توسط دیوار تأیید شدند ({request.phone_number})"
    elif alive is False:
        msg = ("کوکی‌ها ذخیره شدند اما دیوار آن‌ها را نپذیرفت — این نشست کار نمی‌کند. "
               "دوباره از مرورگری که همان لحظه در دیوار وارد شده کپی بگیرید.")
    else:
        msg = "کوکی‌ها ذخیره شدند، اما دیوار پاسخ نداد — وضعیت هنوز نامشخص است"

    return {"success": True, "alive": alive, "message": msg,
            "expires_at": expires_at.isoformat() if expires_at else None}


@router.post("/cookies/{cookie_id}/identity-cleared")
async def identity_cleared(
    cookie_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """A person has verified this account on Divar; let rotation use it again.

    The flag is not cleared by time and not cleared by a code: only by
    somebody saying they did it. Own accounts only, or an admin's.
    """
    cookie = (await db.execute(select(Cookie).where(Cookie.id == cookie_id))).scalar_one_or_none()
    if not cookie:
        raise HTTPException(status_code=404, detail="نشست پیدا نشد")
    if not _sees_every_session(user) and cookie.owner_user_id != user.id:
        raise HTTPException(status_code=403, detail="این شماره به حساب کاربری دیگری تعلق دارد")
    cookie.identity_required_at = None
    await db.commit()
    from app.scraper import otp_store
    otp_store.clear_identity_required(cookie.phone_number)
    return {"success": True, "phone_number": cookie.phone_number}


@router.delete("/cookies/{cookie_id}")
async def delete_cookie(
    cookie_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Delete a stored cookie session, in the database and on disk.

    Every session is stored twice: a row here and a cookies_<phone>.json
    file on the data volume. Dropping only the row left the file behind,
    holding the Divar session token in plain text -- so a session deleted
    from the panel was not actually gone. Remove both.
    """

    result = await db.execute(
        select(Cookie).where(Cookie.id == cookie_id)
    )
    cookie = result.scalar_one_or_none()
    # A session is a live Divar credential. Deleting somebody else's is not a
    # listing mistake to be tolerated — it logs them out.
    if cookie and not _sees_every_session(user) and cookie.owner_user_id != user.id:
        raise HTTPException(
            status_code=403, detail="این شماره به حساب کاربری دیگری تعلق دارد")

    if not cookie:
        raise HTTPException(status_code=404, detail="Cookie not found")

    phone = cookie.phone_number
    # Who, from where, deleted which session — at WARNING, so it survives a
    # quiet log level. Nine sessions vanished on 2026-09-12 at 15:22 and this
    # route had recorded nothing: five accounts could have pressed it and the
    # only pod that saw the request had been replaced. A Divar session costs
    # an SMS to recreate; its deletion is worth one line.
    _ip = request.client.host if request.client else "?"
    logger.warning(f"[audit] {user.username} ({user.role}) from {_ip} deleted "
                   f"Divar session {phone} (cookie row {cookie_id})")
    file_removed = False
    file_error = None
    try:
        cookie_file = DivarAuth(db).get_cookie_file_path(phone)
        if cookie_file.exists():
            cookie_file.unlink()
        file_removed = True
    except Exception as e:
        # Report it rather than swallowing it: the caller is deleting this
        # session on purpose, and a file left behind still holds the token.
        file_error = str(e)
        logger.error(f"Failed to remove cookie file for {phone}: {e}")

    await db.delete(cookie)
    await db.commit()

    if not file_removed:
        return {
            "success": True,
            "file_removed": False,
            "message": (
                "نشست از پایگاه داده حذف شد، اما فایل کوکی روی سرور باقی ماند "
                f"و باید دستی پاک شود: {file_error}"
            ),
        }

    return {"success": True, "file_removed": True, "message": "Cookie deleted successfully"}
