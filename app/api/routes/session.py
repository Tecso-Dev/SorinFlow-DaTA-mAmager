"""
The new panel's login, on an httpOnly cookie instead of a token in localStorage.

Every step is the /api/users/token flow itself (same lookup, attempt caps,
constant-time check, second factors, audit trail); this router only moves the
finished token out of the response body and into the cookie. A half-finished
login (TOTP or email code owed) returns its short-lived session in the body as
before: it cannot authenticate anything, and the page needs it for the next
step.
"""
from types import SimpleNamespace
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes import users as users_routes
from app.auth.dependencies import _user_from_token, get_current_user
from app.auth.permissions import user_permissions
from app.auth.session_cookie import clear_session, csrf_for, session_token, set_session
from app.database import get_db
from app.models.user import User
from app.schemas import EmailCodeVerifyRequest, TokenResponse, TotpLoginRequest, UserResponse

router = APIRouter()

Db = Annotated[AsyncSession, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]


class SessionLogin(BaseModel):
    username: str = Field(..., max_length=255)
    password: str = Field(..., max_length=1024)
    remember: bool = False


class SessionTotp(TotpLoginRequest):
    remember: bool = False


class SessionEmail(EmailCodeVerifyRequest):
    remember: bool = False


def _user_body(user: User) -> dict:
    data = UserResponse.model_validate(user, from_attributes=True)
    data.permissions = user_permissions(user)
    return data.model_dump(mode="json")


async def _finish(request: Request, response: Response, reply: TokenResponse,
                  remember: bool, db: AsyncSession) -> dict:
    if not reply.access_token:
        # A second factor is owed; only what the next step needs goes back.
        if reply.requires_totp:
            return {"requires_totp": True, "totp_session": reply.totp_session}
        if reply.requires_email_code:
            return {"requires_email_code": True, "email_session": reply.email_session,
                    "email_hint": reply.email_hint}
        raise HTTPException(status_code=500, detail="ورود نیمه‌کاره ماند")
    user = await _user_from_token(reply.access_token, db)
    if user is None:
        raise HTTPException(status_code=401, detail="حساب کاربری در دسترس نیست")
    if user.role == "visitor":
        # A customer's portal account. Every staff API would refuse it anyway,
        # but it gets no panel cookie to begin with.
        raise HTTPException(status_code=403, detail="این حساب برای پورتال مشتریان است؛ از صفحهٔ پورتال وارد شوید.")
    csrf = set_session(request, response, reply.access_token, remember)
    return {"user": _user_body(user), "csrf_token": csrf}


@router.post("/login")
async def session_login(data: SessionLogin, request: Request, response: Response,
                        db: Db):
    form = SimpleNamespace(username=data.username, password=data.password)
    reply = await users_routes.login(request, form, db)  # type: ignore[arg-type]
    return await _finish(request, response, reply, data.remember, db)


@router.post("/verify-totp")
async def session_verify_totp(data: SessionTotp, request: Request, response: Response,
                              db: Db):
    reply = await users_routes.verify_totp_login(data, request, db)
    return await _finish(request, response, reply, data.remember, db)


@router.post("/verify-email")
async def session_verify_email(data: SessionEmail, request: Request, response: Response,
                               db: Db):
    reply = await users_routes.verify_email_login(data, request, db)
    return await _finish(request, response, reply, data.remember, db)


@router.get("")
async def session_read(request: Request, current_user: CurrentUser):
    token: Optional[str] = session_token(request)
    return {"user": _user_body(current_user), "csrf_token": csrf_for(token) if token else None}


@router.post("/logout")
async def session_logout(request: Request, response: Response):
    """Clears the cookies. Needs no session, so a stale one can always be cleared."""
    clear_session(request, response)
    return {"ok": True}
