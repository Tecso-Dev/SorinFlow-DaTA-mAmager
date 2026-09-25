"""
The new panel's session: the access token in an httpOnly cookie, and a CSRF
token derived from it in a cookie the page can read.

JavaScript never sees the access token, so a script injected into the page
cannot carry it off (the old panel keeps it in localStorage). The CSRF token is
an HMAC of the session token under SECRET_KEY: it changes with every login,
needs no storage, and a page on another origin can neither read it nor compute
it.

Over HTTPS both cookies take the __Host- prefix, which the browser only accepts
with Secure, Path=/ and no Domain, so a sibling subdomain cannot plant one. Over
plain HTTP (a laptop) the prefix is impossible and the plain names are used;
each scheme reads only its own names.
"""
import hashlib
import hmac
from typing import Optional

from fastapi import Request, Response

from app.config import get_settings

SESSION_NAME = "sf_session"
CSRF_NAME = "sf_csrf"
CSRF_HEADER = "X-CSRF-Token"


def _secure(request: Request) -> bool:
    # uvicorn's --proxy-headers turns Traefik's X-Forwarded-Proto into the scheme.
    return request.url.scheme == "https"


def _names(request: Request) -> tuple[str, str]:
    if _secure(request):
        return f"__Host-{SESSION_NAME}", f"__Host-{CSRF_NAME}"
    return SESSION_NAME, CSRF_NAME


def csrf_for(token: str) -> str:
    key = get_settings().secret_key.encode("utf-8")
    return hmac.new(key, b"csrf:" + token.encode("utf-8"), hashlib.sha256).hexdigest()


def session_token(request: Request) -> Optional[str]:
    return request.cookies.get(_names(request)[0]) or None


def csrf_ok(request: Request, token: str) -> bool:
    sent = request.headers.get(CSRF_HEADER) or ""
    return bool(sent) and hmac.compare_digest(sent, csrf_for(token))


def set_session(request: Request, response: Response, token: str, remember: bool) -> str:
    """Put the session on the response; returns its CSRF token.

    remember: the cookies live as long as the token. Otherwise they are
    browser-session cookies, gone when the browser closes (the token's own
    expiry still applies).
    """
    session_name, csrf_name = _names(request)
    if remember:
        # the claim reissue() reads to keep «remember me» on a fresh token
        from app.auth.jwt import decode_token, create_access_token
        claims = {k: v for k, v in decode_token(token).items() if k not in ("iat", "exp", "typ")}
        token = create_access_token({**claims, "rm": 1})
    max_age = get_settings().access_token_expire_minutes * 60 if remember else None
    csrf = csrf_for(token)
    secure = _secure(request)
    response.set_cookie(session_name, token, max_age=max_age, path="/", secure=secure,
                        httponly=True, samesite="lax")
    response.set_cookie(csrf_name, csrf, max_age=max_age, path="/", secure=secure,
                        httponly=False, samesite="lax")
    return csrf


def remembered(token: str) -> bool:
    from app.auth.jwt import decode_token
    try:
        return bool(decode_token(token).get("rm"))
    except Exception:
        return False


def reissue(request: Optional[Request], response: Optional[Response], token: str) -> Optional[str]:
    """A route minted a fresh access token (a rename, a password change).

    For a request that came on the session cookie the fresh token replaces the
    cookie, keeping «remember me», and None is returned: the body must not hand
    the page a token it was built never to see. A bearer caller gets its token
    back to store, as before.
    """
    if request is None or response is None:  # called directly, not over HTTP
        return token
    old = session_token(request)
    if request.headers.get("Authorization", "").startswith("Bearer ") or not old:
        return token
    set_session(request, response, token, remembered(old))
    return None


def clear_session(request: Request, response: Response) -> None:
    for name in _names(request):
        response.delete_cookie(name, path="/", secure=_secure(request), samesite="lax")
