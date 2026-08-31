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
    # Which route the code actually took. The portal shows "کد به ایمیل شما
    # ارسال شد" or "...به شمارهٔ شما", and guessing wrong sends someone to
    # stare at the wrong inbox.
    channel: str = "sms"


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
                     message_template: str | None = None,
                     email: str | None = None,
                     channel: str | None = None,
                     db=None) -> IssuedCode:
    """Create, store and send a verification code.

    Raises VerificationError when the caller is asking too often — the message
    is safe to show the user verbatim.

    `channel` picks the delivery route: "sms", "email", or None to decide
    automatically — email when an address is known and SMS is not configured,
    SMS otherwise. Everything above the delivery line is channel-agnostic: the
    Redis keys hash on `identifier`, not on how the code travelled, so a code
    sent by email verifies through exactly the same path as one sent by SMS.

    The returned IssuedCode carries `channel` so the portal can tell the person
    where to go looking for it.
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

    used = await _deliver(code, phone=phone, email=email, channel=channel,
                          message_template=message_template, ttl=ttl, db=db)
    if not used:
        # Burn the code rather than leave one alive that nobody received. This
        # is why _deliver returns a channel-or-None instead of raising: the
        # cleanup must happen whichever route failed.
        await r.delete(keys["code"], keys["cooldown"])
        logger.error(f"[verification] delivery failed for {purpose}")
        raise VerificationError("ارسال کد ناموفق بود. کمی بعد دوباره تلاش کنید")

    logger.info(f"[verification] code sent purpose={purpose} via={used} ttl={ttl}s")
    # Only ever populated outside production, so a developer can finish the
    # flow without a live SMS panel.
    debug = code if settings.environment != "production" else None
    return IssuedCode(ttl=ttl, cooldown=settings.auth_code_resend_cooldown,
                      debug_code=debug, channel=used)


async def _deliver(code: str, *, phone: str, email: str | None,
                   channel: str | None, message_template: str | None,
                   ttl: int, db=None) -> str | None:
    """Send the code. Returns the channel that worked, or None.

    Order matters when nothing is specified. Email is tried first only when SMS
    has no credentials at all — otherwise SMS stays primary, because a phone is
    verified at sign-up and an address is not.
    """
    from app.services import email_service, email_templates

    text = (message_template or "کد ورود شما به سورین‌فلو: {code}").format(code=code)

    # Each leg is wrapped. A provider that raises — a bad credential, a
    # library that changed its signature, a socket error nobody predicted —
    # must degrade to "this route did not work" so the other one is still
    # tried and the code is burned. Letting it escape turns a delivery problem
    # into a 500 on the sign-up form, which is the worst place to have one.
    async def _sms() -> bool:
        if not phone:
            return False
        try:
            res = await send_sms(phone, text, provider=settings.auth_sms_provider, db=db)
        except Exception as e:
            logger.warning(f"[verification] sms leg raised: {type(e).__name__}: {e}")
            return False
        if not res.get("success"):
            logger.warning(f"[verification] sms leg failed: {res.get('response')}")
        return bool(res.get("success"))

    async def _email() -> bool:
        if not email or not email_service.valid_email(email):
            return False
        try:
            subject, html, plain = email_templates.login_code(
                code, minutes=max(1, ttl // 60))
            res = await email_service.send(email, subject, html, plain, db=db)
        except Exception as e:
            logger.warning(f"[verification] email leg raised: {type(e).__name__}: {e}")
            return False
        if not res.get("success"):
            logger.warning(f"[verification] email leg failed: {res.get('error')}")
        return bool(res.get("success"))

    if channel == "email":
        return "email" if await _email() else None
    if channel == "sms":
        return "sms" if await _sms() else None

    # Automatic. Prefer whichever is actually configured, and fall through to
    # the other rather than failing outright — a sign-up that dies because one
    # provider is down is a lost customer.
    sms_ready = bool((settings.kavenegar_api_key or "").strip()) or \
        settings.auth_sms_provider == "console"
    order = ["sms", "email"] if sms_ready else ["email", "sms"]
    for leg in order:
        if leg == "sms" and await _sms():
            return "sms"
        if leg == "email" and await _email():
            return "email"
    return None


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


# ── per-IP throttling ───────────────────────────────────────────────────────
#
# The existing budgets key on the phone number, so rotating the number resets
# them to zero. That is fine against someone locked out of one account and
# useless against a script: unlimited `users` rows, and one SMS per request
# billed to the owner. Nothing else stands in the way — the api-key middleware
# explicitly exempts /api/public/auth/*, and the ingress has no limiter.

# registrations per IP per hour, and codes sent per IP per hour
IP_SIGNUP_LIMIT = 5
IP_CODE_LIMIT = 10
IP_WINDOW = 3600


def client_ip(request) -> str:
    """The caller's address, as trustworthy as this topology allows.

    Traefik APPENDS the real peer to any X-Forwarded-For the client supplied,
    so the rightmost entry is the one our own proxy wrote and the only one a
    caller cannot forge. Taking the leftmost — the common mistake — would let
    anyone reset their own budget by sending a made-up header.
    """
    if request is None:
        return "unknown"
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        parts = [p.strip() for p in fwd.split(",") if p.strip()]
        if parts:
            return parts[-1]
    real = request.headers.get("x-real-ip", "").strip()
    if real:
        return real
    return getattr(getattr(request, "client", None), "host", None) or "unknown"


async def check_ip_budget(request, bucket: str, limit: int) -> None:
    """Raise VerificationError once this address has spent its hourly budget.

    Fails OPEN when Redis is unavailable, matching check_login_rate: a Redis
    blip must not close public sign-up altogether. The per-phone budgets still
    apply underneath, so failing open is degraded, not absent.
    """
    ip = client_ip(request)
    if ip == "unknown":
        return
    key = f"{_NS}:ip:{bucket}:{ip}"
    try:
        r = await get_redis()
        used = int(await r.get(key) or 0)
        if used >= limit:
            ttl = await r.ttl(key)
            raise VerificationError(
                "تعداد درخواست‌ها از این دستگاه بیش از حد مجاز است. "
                f"{max(ttl // 60, 1)} دقیقهٔ دیگر تلاش کنید",
                retry_after=max(ttl, 60))
    except VerificationError:
        raise
    except Exception as e:
        logger.warning(f"[verification] ip budget unavailable, allowing: {e}")
        return


async def spend_ip_budget(request, bucket: str) -> None:
    """Count one against this address. Called only after the work succeeded, so
    a request refused for another reason does not consume the budget."""
    ip = client_ip(request)
    if ip == "unknown":
        return
    key = f"{_NS}:ip:{bucket}:{ip}"
    try:
        r = await get_redis()
        pipe = r.pipeline()
        pipe.incr(key)
        pipe.expire(key, IP_WINDOW)
        await pipe.execute()
    except Exception:
        pass


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
