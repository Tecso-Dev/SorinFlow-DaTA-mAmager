"""
SorinFlow — SMS verification codes and login throttling.

State lives in Redis, not in a module dict. The scraper's OTP store learned
this the hard way: a process-local dict loses every pending code on restart and
is invisible to a second pod, so a code issued by one worker cannot be verified
by another. Redis also gives the expiry for free.

Codes are stored hashed. A Redis dump, a MONITOR session or a stray log line
should not hand over a live credential.
"""
import asyncio
import hashlib
import hmac
import secrets
from dataclasses import dataclass

from loguru import logger

from app.config import get_settings
from app.database import get_redis
from app.services.sms_service import send_sms

settings = get_settings()

_NS = "sf:auth"


class VerificationError(Exception):
    """Raised with a Persian message meant for the end user."""

    def __init__(self, message: str, retry_after: int = 0):
        super().__init__(message)
        self.message = message
        self.retry_after = retry_after


@dataclass
class IssuedCode:
    ttl: int
    cooldown: int
    debug_code: str | None = None


def _norm(identifier: str) -> str:
    return (identifier or "").strip().lower()


def _hash(code: str, identifier: str) -> str:
    """Salted with the app secret and the identifier, so a hash is useless
    against a different phone even if the table leaks."""
    msg = f"{_norm(identifier)}:{code}".encode()
    return hmac.new(settings.secret_key.encode(), msg, hashlib.sha256).hexdigest()


def _keys(purpose: str, identifier: str):
    ident = _norm(identifier)
    base = f"{_NS}:{purpose}:{ident}"
    return {
        "code": f"{base}:code",
        "attempts": f"{base}:attempts",
        "cooldown": f"{base}:cooldown",
        "sends": f"{base}:sends",
    }


def generate_code() -> str:
    n = max(4, min(8, settings.auth_code_length))
    return "".join(secrets.choice("0123456789") for _ in range(n))


async def issue_code(purpose: str, identifier: str, phone: str,
                     message_template: str | None = None) -> IssuedCode:
    """Create, store and send a verification code.

    Raises VerificationError when the caller is asking too often — the message
    is safe to show the user verbatim.
    """
    keys = _keys(purpose, identifier)
    try:
        r = await get_redis()
        cooldown_left = await r.ttl(keys["cooldown"])
        if cooldown_left and cooldown_left > 0:
            raise VerificationError(
                f"برای ارسال دوباره کد، {cooldown_left} ثانیه صبر کنید",
                retry_after=cooldown_left)

        sends = int(await r.get(keys["sends"]) or 0)
        if sends >= settings.auth_code_max_sends_per_hour:
            raise VerificationError(
                "تعداد درخواست کد بیش از حد مجاز است. یک ساعت دیگر تلاش کنید",
                retry_after=3600)
    except VerificationError:
        raise
    except Exception as e:
        # Redis is the only store for codes — without it a code could be sent
        # but never verifiable, which is worse than refusing outright.
        logger.error(f"[verification] redis unavailable, refusing to issue: {e}")
        raise VerificationError("سرویس احراز هویت در دسترس نیست. کمی بعد تلاش کنید")

    code = generate_code()
    ttl = settings.auth_code_ttl_seconds

    pipe = r.pipeline()
    pipe.setex(keys["code"], ttl, _hash(code, identifier))
    pipe.delete(keys["attempts"])
    pipe.setex(keys["cooldown"], settings.auth_code_resend_cooldown, "1")
    pipe.incr(keys["sends"])
    pipe.expire(keys["sends"], 3600)
    await pipe.execute()

    text = (message_template or "کد ورود شما به سورین‌فلو: {code}").format(code=code)
    result = await send_sms(phone, text, provider=settings.auth_sms_provider)
    if not result.get("success"):
        # Burn the code rather than leave one alive that nobody received.
        await r.delete(keys["code"], keys["cooldown"])
        logger.error(f"[verification] SMS send failed for {purpose}: {result.get('response')}")
        raise VerificationError("ارسال پیامک ناموفق بود. کمی بعد دوباره تلاش کنید")

    logger.info(f"[verification] code sent purpose={purpose} ttl={ttl}s")
    # Only ever populated outside production, so a developer can finish the
    # flow without a live SMS panel.
    debug = code if settings.environment != "production" else None
    return IssuedCode(ttl=ttl, cooldown=settings.auth_code_resend_cooldown, debug_code=debug)


async def verify_code(purpose: str, identifier: str, code: str) -> bool:
    """Check a code, consuming it on success and counting failures.

    A wrong guess costs an attempt; running out burns the code entirely, so a
    5-digit code cannot be walked through at leisure.
    """
    keys = _keys(purpose, identifier)
    try:
        r = await get_redis()
        stored = await r.get(keys["code"])
    except Exception as e:
        logger.error(f"[verification] redis unavailable on verify: {e}")
        raise VerificationError("سرویس احراز هویت در دسترس نیست. کمی بعد تلاش کنید")

    if not stored:
        raise VerificationError("کد منقضی شده است. دوباره درخواست کنید")

    attempts = await r.incr(keys["attempts"])
    await r.expire(keys["attempts"], settings.auth_code_ttl_seconds)
    if attempts > settings.auth_code_max_attempts:
        await r.delete(keys["code"], keys["attempts"])
        raise VerificationError("تعداد تلاش‌های نادرست بیش از حد مجاز است. کد جدید بگیرید")

    if not hmac.compare_digest(stored, _hash((code or "").strip(), identifier)):
        left = settings.auth_code_max_attempts - attempts
        raise VerificationError(
            f"کد وارد شده اشتباه است. {max(left, 0)} تلاش باقی مانده است")

    await r.delete(keys["code"], keys["attempts"], keys["sends"])
    return True


async def check_login_rate(identifier: str) -> None:
    """Throttle password guessing. Raises VerificationError when locked out."""
    key = f"{_NS}:login:{_norm(identifier)}"
    try:
        r = await get_redis()
        fails = int(await r.get(key) or 0)
    except Exception as e:
        # Deliberately fails open. A Redis blip must not lock the office out of
        # a live panel; the wrong-password path still costs a bcrypt round.
        logger.warning(f"[verification] login throttle unavailable, allowing: {e}")
        return
    if fails >= settings.auth_login_max_attempts:
        ttl = await r.ttl(key)
        raise VerificationError(
            f"تلاش‌های ناموفق بیش از حد مجاز. {max(ttl, 1)} ثانیه دیگر تلاش کنید",
            retry_after=max(ttl, 1))


async def record_login_failure(identifier: str) -> None:
    key = f"{_NS}:login:{_norm(identifier)}"
    try:
        r = await get_redis()
        pipe = r.pipeline()
        pipe.incr(key)
        pipe.expire(key, 900)
        await pipe.execute()
    except Exception:
        pass


async def clear_login_failures(identifier: str) -> None:
    try:
        r = await get_redis()
        await r.delete(f"{_NS}:login:{_norm(identifier)}")
    except Exception:
        pass
