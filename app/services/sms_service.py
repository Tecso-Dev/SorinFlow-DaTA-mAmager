"""
SorinFlow — SMS.

Two audiences share this module:

  * the plumbing — login codes and reminders, which only ever call send_sms()
    and want a boolean back;
  * the panel — «پیامک» in the dashboard, which needs credit, delivery
    receipts, a send log and bulk sending.

Kavenegar is the provider this is built around (kavenegar.com); Melipayamak
remains as a fallback sender because it was already wired in and removing it
would take a working path away for no gain.

## The one Kavenegar detail that matters

Kavenegar answers *every* call with HTTP 200 and puts the real outcome in the
body:

    {"return": {"status": 411, "message": "receptor is invalid"}, "entries": null}

The previous version of this file checked `resp.status_code == 200` and nothing
else, so an invalid number, an empty credit balance, or a rejected sender line
were all recorded as delivered. Every response now goes through _unwrap(),
which reads `return.status` and treats anything but 200 as a failure.

## Where the API key lives

`KAVENEGAR_API_KEY` in the environment (the Kubernetes Secret) wins, always.
If it is absent, the key saved from the dashboard is used — stored encrypted
with a Fernet key derived from SECRET_KEY, never returned to the browser, and
shown only as a masked hint. The environment is still the right place for it in
production; the dashboard exists so the panel can be set up without a deploy.
"""
import asyncio
import json
from datetime import datetime, timezone
from typing import Iterable, Optional

import httpx
from loguru import logger
from sqlalchemy import select

from app.config import get_settings

settings = get_settings()

_BASE = "https://api.kavenegar.com/v1"
_TIMEOUT = 20.0

# How many receptors go into one sendarray call. Kavenegar accepts more, but a
# smaller batch means a failure costs less and the progress line moves.
BULK_CHUNK = 100

# ── settings kept in the database, editable from the panel ──────────────────
KEY_API_KEY = "sms_kavenegar_api_key"     # encrypted
KEY_SENDER = "sms_kavenegar_sender"
KEY_OTP_TEMPLATE = "sms_otp_template"     # verify/lookup template name
KEY_ENABLED = "sms_enabled"
KEY_SIGNATURE = "sms_signature"           # appended to panel-composed messages

# Kavenegar's own result codes. Worth spelling out: "خطا" with a bare number is
# the difference between "top up your account" and "your sender line is wrong",
# and the panel should say which.
STATUS_FA = {
    200: "موفق",
    400: "پارامترها ناقص است",
    401: "حساب کاربری غیرفعال شده است",
    402: "عملیات ناموفق بود",
    403: "کلید API نامعتبر است",
    404: "متد نامشخص است",
    405: "متد GET/POST اشتباه است",
    406: "پارامترهای اجباری خالی ارسال شده‌اند",
    407: "دسترسی به اطلاعات مورد نظر برای شما امکان‌پذیر نیست",
    409: "سرور قادر به پاسخگویی نیست، بعدا تلاش کنید",
    411: "شماره گیرنده نامعتبر است",
    412: "شماره فرستنده نامعتبر است",
    413: "متن پیام خالی یا بیش از حد طولانی است",
    414: "حجم درخواست بیشتر از حد مجاز است",
    415: "ایندکس مورد نظر خارج از محدوده است",
    416: "IP سرور با تنظیمات حساب هم‌خوانی ندارد",
    417: "تاریخ ارسال نامعتبر است",
    418: "اعتبار شما کافی نیست",
    419: "طول آرایه‌ها با هم برابر نیست",
    420: "درج لینک در متن پیام برای حساب شما محدود شده است",
    422: "داده‌ها به دلیل وجود کاراکتر نامناسب قابل پردازش نیستند",
    424: "الگوی مورد نظر یافت نشد",
    426: "این متد نیازمند «سرویس پیشرفته» است — آن را در پنل کاوه‌نگار فعال کنید",
    427: "این خط نیازمند ایجاد سطح دسترسی است",
    428: "ارسال کد از طریق تماس تلفنی مقدور نیست",
    429: "IP شما محدود شده است",
    # Not in the published table, but Kavenegar returns it and it is the first
    # thing a new account hits: identity verification (احراز هویت) is
    # incomplete, so only the account holder's own number can be messaged.
    430: "حساب کاربری کاوه‌نگار هنوز احراز هویت نشده است — تا تکمیل آن فقط "
         "می‌توانید به شمارهٔ خودِ صاحب حساب پیامک بفرستید.",
    431: "ساختار کد صحیح نیست",
    432: "پارامتر کد در متن پیام یافت نشد",
    451: "تعداد درخواست در بازهٔ زمانی بیش از حد مجاز — IP موقتاً محدود شده است",
    501: "فقط امکان ارسال پیام آزمایشی به شمارهٔ صاحب حساب وجود دارد — "
         "حساب کاربری هنوز احراز هویت نشده است.",
}

# Delivery states Kavenegar reports for a sent message.
DELIVERY_FA = {
    1: "در صف ارسال",
    2: "زمان‌بندی شده",
    4: "ارسال شده به مخابرات",
    5: "ارسال شده به مخابرات",
    6: "خطا در ارسال",
    10: "رسیده به گیرنده",
    11: "نرسیده به گیرنده",
    13: "لغو شده",
    14: "در لیست سیاه",
    100: "نامعتبر یا خارج از بازهٔ گزارش‌گیری ۴۸ ساعته",
}
# Which of those are final, so the poller stops asking.
DELIVERY_FINAL = {6, 10, 11, 13, 14, 100}


class SmsError(Exception):
    """A Kavenegar call that came back with a non-200 `return.status`."""

    def __init__(self, status: int, message: str = ""):
        self.status = status
        # Our own Persian wins over Kavenegar's English. The panel is read by
        # people who need to know whether to top up the account or fix the
        # sender line, and "credit is not enough" does not tell them that in a
        # language they are reading the rest of the screen in. The provider's
        # own text is kept only when the code is one we have no wording for.
        self.message = STATUS_FA.get(status) or message or "خطای نامشخص"
        self.provider_message = message
        super().__init__(f"[{status}] {self.message}")


# ── credential storage ─────────────────────────────────────────────────────
#
# Shared with the email panel — see app/services/secret_box.py for why the
# Fernet key is derived from SECRET_KEY rather than stored separately. The
# names below are kept as thin aliases so existing callers and tests do not
# have to care that the implementation moved.

from app.services import secret_box

_encrypt = secret_box.encrypt
_decrypt = secret_box.decrypt
mask_key = secret_box.mask
_get_settings_rows = secret_box.get_many
put_setting = secret_box.put


async def resolve_credentials(db=None) -> tuple:
    """(api_key, sender) — environment first, then the panel.

    db=None is the plumbing path: config only, no query in front of a login
    code. It means a key saved from the dashboard does not apply until a
    request that has a session, which is every panel call and every portal
    sign-up, so in practice only the very first send after saving is affected.
    """
    api_key = (settings.kavenegar_api_key or "").strip()
    sender = (settings.kavenegar_sender or "").strip()
    if api_key and sender:
        return api_key, sender

    if db is not None:
        try:
            v = await _get_settings_rows(db, [KEY_API_KEY, KEY_SENDER])
            if not api_key and v.get(KEY_API_KEY):
                api_key = _decrypt(v[KEY_API_KEY])
            if not sender and v.get(KEY_SENDER):
                sender = (v.get(KEY_SENDER) or "").strip()
        except Exception as e:
            logger.warning(f"[sms] could not read saved credentials: {e}")
    return api_key, sender


# ── the Kavenegar wire ──────────────────────────────────────────────────────

def _unwrap(resp: httpx.Response) -> list:
    """Return `entries`, or raise SmsError carrying Kavenegar's own code.

    Kavenegar reports failure inside a 200 body. Checking the HTTP status alone
    is why a message to an invalid number used to be logged as delivered.
    """
    try:
        body = resp.json()
    except Exception:
        raise SmsError(resp.status_code or 402,
                       f"پاسخ نامعتبر از کاوه‌نگار: {resp.text[:120]}")

    ret = (body or {}).get("return") or {}
    status = int(ret.get("status") or 0)
    if status != 200:
        raise SmsError(status, ret.get("message") or "")
    entries = body.get("entries")
    if entries is None:
        return []
    return entries if isinstance(entries, list) else [entries]


async def _call(api_key: str, action: str, method: str, params: dict) -> list:
    if not api_key:
        raise SmsError(403, "کلید API تنظیم نشده است")
    url = f"{_BASE}/{api_key}/{action}/{method}.json"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(url, data=params)
    except Exception as e:
        # Never let the key reach a log line or an error shown in the panel.
        raise SmsError(409, f"ارتباط با کاوه‌نگار برقرار نشد: {type(e).__name__}") from None
    return _unwrap(resp)


async def account_info(db=None) -> dict:
    """Remaining credit and the account's expiry date.

    The number the panel leads with: an SMS system that silently stopped
    working because the balance ran out looks identical to one that is broken.
    """
    api_key, _ = await resolve_credentials(db)
    entries = await _call(api_key, "account", "info", {})
    info = entries[0] if entries else {}
    return {
        "remaining_credit": info.get("remaincredit"),
        "expire_date": info.get("expiredate"),
        "type": info.get("type"),
    }


# sms/status accepts at most 500 ids per call — «در هر بار اجرای این متد
# می‌توانید از وضعیت ۵۰۰ پیامک با خبر شوید», and 414 is the error when you
# exceed it. Today's only caller is capped at 200 by its own query parameter,
# so this is latent; but the function takes a plain list and a longer one
# would lose the status of every id in the batch, not just the excess.
STATUS_CHUNK = 500


async def delivery_status(message_ids: list, db=None) -> list:
    """Delivery state for messages already sent."""
    if not message_ids:
        return []
    api_key, _ = await resolve_credentials(db)

    out = []
    for start in range(0, len(message_ids), STATUS_CHUNK):
        chunk = message_ids[start:start + STATUS_CHUNK]
        entries = await _call(api_key, "sms", "status",
                              {"messageid": ",".join(str(m) for m in chunk)})
        for e in entries:
            code = int(e.get("status") or 0)
            out.append({
                "messageid": e.get("messageid"),
                "status": code,
                "status_text": DELIVERY_FA.get(code, e.get("statustext") or "نامشخص"),
                "final": code in DELIVERY_FINAL,
            })
    return out


async def send_verify(phone: str, token: str, template: str, db=None) -> dict:
    """Send a login code through a Kavenegar template (verify/lookup).

    Templates are the correct channel for one-time codes: they are delivered on
    a dedicated route, they do not need an approved sender line, and they reach
    numbers that have opted out of advertising messages — which a login code is
    not, but a plain send is treated as.
    """
    api_key, _ = await resolve_credentials(db)
    entries = await _call(api_key, "verify", "lookup",
                          {"receptor": phone, "token": token, "template": template})
    first = entries[0] if entries else {}
    return {"success": True, "provider": "kavenegar",
            "messageid": first.get("messageid"), "response": json.dumps(first, ensure_ascii=False)}


# ── the simple path the rest of the app uses ────────────────────────────────

async def send_sms(to_number: str, message: str, provider: str = "kavenegar",
                   db=None) -> dict:
    """Send one message. Returns {"success", "provider", "response", "messageid"}.

    Signature unchanged — verification, reminders and the CRM all call this and
    only look at `success`.
    """
    if provider == "console":
        return await _send_console(to_number, message)
    if provider == "melipayamak":
        return await _send_melipayamak(to_number, message)
    return await _send_kavenegar(to_number, message, db=db)


async def _send_console(to_number: str, message: str) -> dict:
    """Development only — write the message to the log instead of sending it.

    Without this there is no way to exercise the portal sign-up locally: the
    verification flow deliberately fails closed when delivery fails, so every
    registration 503s until a paid SMS account exists.

    Refuses outright in production. A login code printed into the log of a live
    server is a credential in plain text, and AUTH_SMS_PROVIDER is exactly the
    kind of variable that gets copied between environments by accident — so the
    guard is here, not only in the config.
    """
    if settings.environment == "production":
        logger.error("[sms] console provider requested in production — refusing")
        return {"success": False, "provider": "console",
                "response": "console provider is disabled in production"}
    logger.warning(f"[sms:console] to={to_number} :: {message}")
    return {"success": True, "provider": "console", "response": "logged, not sent"}


async def _send_kavenegar(to_number: str, message: str, db=None) -> dict:
    api_key, sender = await resolve_credentials(db)
    if not api_key:
        return {"success": False, "provider": "kavenegar",
                "response": "KAVENEGAR_API_KEY not set"}

    params = {"receptor": to_number, "message": message}
    if sender:
        params["sender"] = sender
    try:
        entries = await _call(api_key, "sms", "send", params)
    except SmsError as e:
        logger.error(f"[sms] Kavenegar refused a send to {to_number}: {e}")
        return {"success": False, "provider": "kavenegar",
                "response": str(e), "status": e.status}

    first = entries[0] if entries else {}
    logger.info(f"[sms] sent to {to_number} id={first.get('messageid')}")
    return {"success": True, "provider": "kavenegar",
            "messageid": first.get("messageid"),
            "cost": first.get("cost"),
            "response": json.dumps(first, ensure_ascii=False)}


async def send_bulk(numbers: list, message: str, db=None,
                    progress=None) -> dict:
    """One message to many numbers, in chunks.

    Kavenegar's sendarray takes parallel arrays of receptors and messages, so
    the same text is repeated per receptor. Chunked because a single rejected
    number fails the whole call, and a failed chunk of 100 is recoverable in a
    way that a failed chunk of 5,000 is not.

    Returns per-number results so the panel can show exactly who it reached.
    """
    api_key, sender = await resolve_credentials(db)
    numbers = [n for n in (str(x).strip() for x in numbers) if n]

    # A missing sender is fatal here in a way it is not for a single send.
    #
    # sms/send lists sender as اختیاری and falls back to the account's default
    # line. sendarray does not: it takes parallel arrays and error 419 is
    # «تعداد اعضای آرایه متن و گیرنده و ارسال کننده هم اندازه نیست», so a
    # request carrying receptor and message but no sender is malformed rather
    # than defaulted. Without this guard a broadcast on an unconfigured line
    # failed every chunk and reported the whole audience as failed with a raw
    # provider string, when the actual cause was one empty field in the panel.
    if not api_key or not sender:
        # Per-recipient results even though nothing was attempted: the caller
        # writes one log row per result, and returning an empty list here left
        # a broadcast that failed for everyone with no trace in the history at
        # all — the panel showed "failed: 40" once and then nothing.
        err = ("کلید API تنظیم نشده است" if not api_key
               else "شمارهٔ فرستنده تنظیم نشده است — ارسال گروهی بدون خط ارسال ممکن نیست")
        return {"success": False, "sent": 0, "failed": len(numbers), "error": err,
                "results": [{"receptor": n, "ok": False, "error": err} for n in numbers]}
    results, sent, failed = [], 0, 0

    for start in range(0, len(numbers), BULK_CHUNK):
        chunk = numbers[start:start + BULK_CHUNK]
        params = {
            "receptor": json.dumps(chunk),
            "message": json.dumps([message] * len(chunk)),
        }
        if sender:
            params["sender"] = json.dumps([sender] * len(chunk))

        try:
            entries = await _call(api_key, "sms", "sendarray", params)
            by_receptor = {str(e.get("receptor")): e for e in entries}
            for n in chunk:
                e = by_receptor.get(n)
                if e:
                    sent += 1
                    results.append({"receptor": n, "ok": True,
                                    "messageid": e.get("messageid"),
                                    "cost": e.get("cost")})
                else:
                    failed += 1
                    results.append({"receptor": n, "ok": False,
                                    "error": "بدون پاسخ از کاوه‌نگار"})
        except SmsError as e:
            # The whole chunk failed. Record it per number so the panel's
            # totals stay honest rather than reporting a partial send as done.
            failed += len(chunk)
            for n in chunk:
                results.append({"receptor": n, "ok": False, "error": str(e)})
            logger.error(f"[sms] bulk chunk failed at {start}: {e}")
            # Out of credit, or throttled at the IP level: whatever the
            # remaining chunks do, they will do it too. Stop rather than
            # spend the rest of the run collecting identical failures.
            if e.status in (418, 429, 451):
                for n in numbers[start + BULK_CHUNK:]:
                    failed += 1
                    results.append({"receptor": n, "ok": False,
                                    "error": "اعتبار کافی نیست"})
                break

        if progress:
            progress(min(start + BULK_CHUNK, len(numbers)), len(numbers))
        # Kavenegar is fine with back-to-back calls, but a broadcast is not
        # urgent and a brief pause keeps a large run from looking like a flood.
        await asyncio.sleep(0.2)

    return {"success": failed == 0, "sent": sent, "failed": failed,
            "results": results}


async def _send_melipayamak(to_number: str, message: str) -> dict:
    api_key = settings.melipayamak_api_key
    from_number = settings.melipayamak_from

    if not api_key:
        return {"success": False, "provider": "melipayamak", "response": "MELIPAYAMAK_API_KEY not set"}

    url = f"https://api.melipayamak.com/api/send/simple/{api_key}"
    payload = {"to": to_number, "from": from_number, "text": message}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=payload)
        body = resp.text
        success = resp.status_code == 200
        logger.info(f"Melipayamak SMS → {to_number}: status={resp.status_code}")
        return {"success": success, "provider": "melipayamak", "response": body}
    except Exception as e:
        logger.error(f"Melipayamak SMS error: {e}")
        return {"success": False, "provider": "melipayamak", "response": str(e)}
