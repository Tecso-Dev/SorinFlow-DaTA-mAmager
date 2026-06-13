"""
SorinFlow Divar Scraper - Authentication API Routes
"""
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Any, List, Optional

from app.database import get_db
from app.models.cookie import Cookie
from app.scraper.auth import DivarAuth
from app.config import get_settings
from app.schemas import (
    LoginRequest,
    OTPVerifyRequest,
    CookieStatusResponse,
    AuthResponse
)

router = APIRouter()
settings = get_settings()

# Store auth instance for session persistence
auth_instances = {}


@router.post("/login", response_model=AuthResponse)
async def initiate_login(
    request: LoginRequest,
    db: AsyncSession = Depends(get_db)
):
    """Initiate login with phone number"""
    
    phone_number = request.phone_number
    
    # Create auth instance
    auth = DivarAuth(db)
    auth_instances[phone_number] = auth
    
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
    db: AsyncSession = Depends(get_db)
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
            # Ensure cookies are saved to database
            cookies = result.get("cookies", [])
            if cookies:
                # Look for token cookie first, then other auth cookies
                token_cookie = next((c for c in cookies if c.get("name") == "token"), None)
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
                expires_at = None
                if token_cookie and "expires" in token_cookie:
                    expires_at = datetime.fromtimestamp(token_cookie["expires"])
                
                if existing_cookie:
                    existing_cookie.cookies = cookies
                    existing_cookie.token = token_value
                    existing_cookie.is_valid = True
                    existing_cookie.expires_at = expires_at
                    existing_cookie.updated_at = datetime.now()
                else:
                    new_cookie = Cookie(
                        phone_number=phone_number,
                        cookies=cookies,
                        token=token_value,
                        is_valid=True,
                        expires_at=expires_at
                    )
                    db.add(new_cookie)
                
                await db.commit()
            
            # Cleanup auth instance
            await auth.close_browser()
            del auth_instances[phone_number]
        
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
    db: AsyncSession = Depends(get_db)
):
    """Get current cookie/session status"""
    
    phone = phone_number or settings.divar_phone_number

    auth = DivarAuth(db)

    # If a specific phone was requested, return its status directly
    if phone:
        status = await auth.get_cookie_status(phone)
        # If that number has no valid session, fall back to any active session in DB
        if not status.get("is_valid"):
            result = await db.execute(
                select(Cookie)
                .where(Cookie.is_valid == True)
                .order_by(Cookie.updated_at.desc())
                .limit(1)
            )
            fallback = result.scalar_one_or_none()
            if fallback:
                status = await auth.get_cookie_status(fallback.phone_number)
        return CookieStatusResponse(**status)

    # No phone configured at all — find any valid session
    result = await db.execute(
        select(Cookie)
        .where(Cookie.is_valid == True)
        .order_by(Cookie.updated_at.desc())
        .limit(1)
    )
    record = result.scalar_one_or_none()
    if not record:
        return CookieStatusResponse(
            has_cookies=False,
            is_valid=False,
            phone_number="",
            message="No phone number configured"
        )
    status = await auth.get_cookie_status(record.phone_number)
    return CookieStatusResponse(**status)


@router.post("/refresh")
async def refresh_session(
    phone_number: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    """Attempt to refresh/validate session"""
    
    phone = phone_number or settings.divar_phone_number
    
    if not phone:
        raise HTTPException(status_code=400, detail="No phone number provided")
    
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
    db: AsyncSession = Depends(get_db)
):
    """Invalidate stored cookies and logout"""
    
    phone = phone_number or settings.divar_phone_number
    
    if not phone:
        raise HTTPException(status_code=400, detail="No phone number provided")
    
    auth = DivarAuth(db)
    success = await auth.invalidate_cookies(phone)
    
    if success:
        return {"success": True, "message": "Logged out successfully"}
    else:
        return {"success": False, "message": "Failed to logout"}


@router.get("/cookies")
async def list_cookies(
    db: AsyncSession = Depends(get_db)
):
    """List all stored cookie sessions"""
    
    result = await db.execute(select(Cookie))
    cookies = result.scalars().all()
    
    return {
        "cookies": [
            {
                "id": c.id,
                "phone_number": c.phone_number,
                "is_valid": c.is_valid,
                "expires_at": c.expires_at.isoformat() if c.expires_at else None,
                "created_at": c.created_at.isoformat() if c.created_at else None
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
):
    """Manually import cookies exported from a browser (e.g. via EditThisCookie extension)."""
    if not request.cookies:
        raise HTTPException(status_code=400, detail="No cookies provided")

    # Find token cookie to extract expiry
    token_cookie = next((c for c in request.cookies if c.get("name") == "token"), None)
    token_value = token_cookie.get("value") if token_cookie else None

    expires_at = None
    if token_cookie:
        exp = token_cookie.get("expirationDate") or token_cookie.get("expires")
        if exp:
            try:
                expires_at = datetime.fromtimestamp(float(exp))
            except Exception:
                pass

    result = await db.execute(select(Cookie).where(Cookie.phone_number == request.phone_number))
    existing = result.scalar_one_or_none()

    if existing:
        existing.cookies = request.cookies
        existing.token = token_value
        existing.is_valid = True
        existing.expires_at = expires_at
        existing.updated_at = datetime.now()
    else:
        db.add(Cookie(
            phone_number=request.phone_number,
            cookies=request.cookies,
            token=token_value,
            is_valid=True,
            expires_at=expires_at,
        ))

    await db.commit()
    return {"success": True, "message": f"Cookies imported for {request.phone_number}"}


@router.delete("/cookies/{cookie_id}")
async def delete_cookie(
    cookie_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Delete a stored cookie session"""
    
    result = await db.execute(
        select(Cookie).where(Cookie.id == cookie_id)
    )
    cookie = result.scalar_one_or_none()
    
    if not cookie:
        raise HTTPException(status_code=404, detail="Cookie not found")
    
    await db.delete(cookie)
    await db.commit()
    
    return {"success": True, "message": "Cookie deleted successfully"}
