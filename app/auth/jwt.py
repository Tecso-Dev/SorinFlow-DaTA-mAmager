"""
SorinFlow — JWT helpers + password hashing
"""
from datetime import datetime, timedelta, timezone
from jose import JWTError, jwt
import bcrypt as _bcrypt
from app.config import get_settings

settings = get_settings()

ALGORITHM = "HS256"

# Token kinds. Only ACCESS may authenticate a request; the others are
# intermediate credentials that must never satisfy get_current_user.
TOKEN_ACCESS = "access"
TOKEN_TOTP_PENDING = "totp_pending"   # password accepted, second factor owed
TOKEN_SMS_PENDING = "sms_pending"     # password accepted, SMS code owed


def verify_password(plain: str, hashed: str) -> bool:
    return _bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def get_password_hash(plain: str) -> str:
    return _bcrypt.hashpw(plain.encode("utf-8"), _bcrypt.gensalt()).decode("utf-8")


def create_access_token(data: dict, expires_minutes: int = None,
                        token_type: str = TOKEN_ACCESS) -> str:
    """Mint a JWT. `typ` states what the token is allowed to do.

    Every caller that mints a half-authenticated token must pass token_type, so
    that a credential handed out before the second factor cannot be replayed as
    a full one — see get_current_user.
    """
    minutes = expires_minutes or settings.access_token_expire_minutes
    now = datetime.now(timezone.utc)
    payload = {
        **data,
        "iat": now,
        "exp": now + timedelta(minutes=minutes),
        "typ": token_type,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    """Decode and validate JWT. Raises JWTError on failure."""
    return jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])


def is_access_token(payload: dict) -> bool:
    """True only for a token that finished the whole login.

    Two independent checks, on purpose. `typ` is the rule going forward; the
    explicit refusal of the intermediate markers also covers tokens minted by
    the previous build, which carried no `typ` at all — those are still honoured
    as access tokens so that a deploy does not log the whole office out, but a
    pre-existing totp_pending token can never slip through on that allowance.
    """
    if payload.get("totp_pending") or payload.get("sms_pending"):
        return False
    typ = payload.get("typ")
    return typ is None or typ == TOKEN_ACCESS
