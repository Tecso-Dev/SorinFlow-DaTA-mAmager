"""
Redis-backed OTP wait/resolve store for Divar contact-info SMS verification.

Used to be an in-process dict with an asyncio.Event per pending request. That
broke the moment the app split into processes (api replicas, a worker, a
scheduler): a code typed on an api replica has no way to reach the
asyncio.Event sitting in the worker's memory, and a restart of any process
wiped every pending prompt, cancel window, strike count and switch request
with it. Redis is now the one place all of those live, so any process can
register a prompt and any other process can resolve it, and a restart loses
nothing that has not already expired on its own.

Keys, all under `sf:otp:`:

    prompt:{key}     hash    phone_hint, ts, resend, resends, code?, source?
    signal:{key}     list    BLPOP target; RPUSHed once when a code lands
    index            set     every open prompt's key, for get_pending() and
                              find_pending_for_account() without SCANning
    sent:{key}       string  epoch seconds the SMS was sent, popped once
    early:{account}  string  JSON {code, sent, sms?, at?}, a code nothing asked for yet
    login:{account}  string  a forwarded LOGIN code, GETDEL on read
    loginmeta:{acct} string  JSON {sms, at} for that login code, beside it
    clicks:{kind}    zset    account -> ms of its latest click that texts it
    asked:{kind}:{a} string  ms of the click that opened this account's challenge
    used:{sms}       string  this SMS has answered something; its copies are void
    copies:{sms}     set     accounts holding a parked copy of this SMS
    cancel:{job_id}  string  OTP suppressed for this job until the TTL passes
    strikes:{job_id} string  unanswered-prompt counter since the last reveal
    switch:{job_id}  string  JSON {phone, by, reason, from_phone, at}
    identity:{acct}  string  JSON {phone, job_id, at, text} — an identity wall

`request()`/`submit()` still register/resolve a prompt, but the wake-up is a
BLPOP on `signal:{key}` (instant — no polling) instead of an asyncio.Event,
so a code submitted on one process wakes a wait_code() call blocked on a
different one. Every function below is safe to call when Redis is briefly
unreachable: a read returns the same thing it would for "nothing pending"
(so the scraper's hot loop never blocks or crashes on a Redis blip) and a
write returns False/None honestly rather than pretending to have succeeded.
"""
import functools
import hashlib
import hmac
import json
import math
import time
from typing import Any, Dict, Iterable, Tuple, Optional

from loguru import logger
from redis.exceptions import WatchError

from app.database import get_redis

_PREFIX = "sf:otp:"
_INDEX_KEY = f"{_PREFIX}index"


def _prompt_key(key: str) -> str:
    return f"{_PREFIX}prompt:{key}"


def _signal_key(key: str) -> str:
    return f"{_PREFIX}signal:{key}"


def _sent_key(key: str) -> str:
    return f"{_PREFIX}sent:{key}"


def _early_key(acct: str) -> str:
    return f"{_PREFIX}early:{acct}"


def _login_key(acct: str) -> str:
    return f"{_PREFIX}login:{acct}"


def _cancel_key(job_id) -> str:
    return f"{_PREFIX}cancel:{job_id}"


def _strikes_key(job_id) -> str:
    return f"{_PREFIX}strikes:{job_id}"


def _switch_key(job_id) -> str:
    return f"{_PREFIX}switch:{job_id}"


def _identity_key(acct: str) -> str:
    return f"{_PREFIX}identity:{acct}"


def _login_meta_key(acct: str) -> str:
    return f"{_PREFIX}loginmeta:{acct}"


def _clicks_key(kind: str) -> str:
    return f"{_PREFIX}clicks:{kind}"


def _asked_key(kind: str, acct: str) -> str:
    return f"{_PREFIX}asked:{kind}:{acct}"


def _used_key(sms: str) -> str:
    return f"{_PREFIX}used:{sms}"


def _copies_key(sms: str) -> str:
    return f"{_PREFIX}copies:{sms}"


def _redis_safe(default):
    """Every Redis call in this module goes through a function wrapped in
    this, so a Redis outage degrades the OTP flow instead of taking the
    scraper's hot loop or an API route down with it: the wrapped call logs
    and returns `default` (or `default()` for a fresh mutable one) exactly
    as if nothing were pending, rather than raising into the caller.
    """
    def deco(fn):
        @functools.wraps(fn)
        async def wrapper(*a, **kw):
            try:
                return await fn(*a, **kw)
            except Exception as e:
                logger.warning(f"[otp_store] {fn.__name__} failed (Redis unavailable?): {e}")
                return default() if callable(default) else default
        return wrapper
    return deco


async def _redis() -> Any:
    """`await get_redis()`, typed as Any.

    redis-py 5.0.1's command-mixin stubs (get, set, hset, blpop, ...) return
    `Awaitable[T] | T` on every method — shared typing between the sync and
    async clients — which mypy cannot narrow for a plain `await` at any of
    the call sites below. This is the one place that gap is bridged, rather
    than a `# type: ignore` sprinkled at each of them; it changes nothing at
    runtime, since get_redis() already only ever returns the async client.
    """
    return await get_redis()


def job_of(key) -> str:
    """The job a request key belongs to. Keys are «{job_id}:{divar_id}».

    Coerces rather than requiring a string. ScrapingJob.job_id is a UUID
    column with as_uuid=True, so callers holding the row hand over a
    uuid.UUID and `.split` on it raised AttributeError — swallowed by the
    caller's except, which turned a broken check into a silent one. A UUID
    stringifies to exactly the prefix the keys are built from, so accepting
    both is correct rather than merely forgiving.
    """
    return str(key or "").split(":", 1)[0]


def _digits(s: Optional[str]) -> str:
    """Just the digits, Persian and Arabic forms included, last 10 kept — so
    +98912…, 0098912… and 0912… are all the same phone."""
    t = str(s or "").translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
    d = "".join(ch for ch in t if ch.isdigit())
    return d[-10:]


def wait_window() -> int:
    """How long a request stays open — the SAME number the browser waits.

    It was otp_wait_timeout alone, and that stopped being the browser's
    answer when wait-for-human arrived: contact_extractor waits
    max(otp_wait_timeout, otp_wait_max_seconds) when otp_wait_for_human is
    on, which is 6 hours against this function's 5 minutes.

    So get_pending() dropped the prompt after five minutes and the panel
    said «مهلت این کد تمام شد — اسکرپر بدون این شماره ادامه داد» while the
    browser sat parked for another five hours and fifty-five, with the entry
    still in the store and a code still perfectly acceptable. The operator
    was told it was too late and given a disabled button; the run stayed
    paused because of the message, not because of Divar.

    The expression is copied from the extractor deliberately. Two places
    deriving one deadline is what broke it; if this ever moves, move both.
    """
    from app.config import get_settings
    cfg = get_settings()
    base = int(getattr(cfg, "otp_wait_timeout", 300) or 300)
    if bool(getattr(cfg, "otp_wait_for_human", False)):
        return max(base, int(getattr(cfg, "otp_wait_max_seconds", 21600) or 21600))
    return base


# When the user dismisses an OTP prompt, we stop asking for the rest of that
# run so the scraper doesn't block ~300s on every phone that needs a code.
#
# Per job, not global. Up to three scrapes run at once, and a single shared
# flag meant dismissing one prompt silently suppressed phone extraction on
# every other running job for fifteen minutes — with nothing on screen to say
# why those jobs suddenly stopped collecting numbers. cancel:{job_id} carries
# its own TTL now, so the Redis key expiring IS "the window passed" — nothing
# has to notice and clean it up by hand the way the in-memory dict did.
_CANCEL_WINDOW = 900  # 15 min

# Divar accepts a contact code for about two minutes. Past that, typing it
# only earns a rejection and burns the attempt.
EARLY_TTL = 120

# Asking again is worth a hard cap. Each one is a real SMS Divar sends on our
# behalf, and a panel button that can be held down is a way to get an account
# rate-limited by its owner rather than by Divar.
MAX_RESENDS = 3

# Divar's login flow (auth.py) is driven by a person in the panel, so a
# forwarded login code has nowhere to go immediately. It is parked here for a
# short while so the panel can pick it up instead of the person re-typing what
# the phone already sent. Nothing waits on it; unclaimed codes simply expire.
LOGIN_CODE_TTL = 180

# Unanswered prompts, per job, are worth remembering for a day at most — long
# past that and whatever job they belonged to is long finished one way or
# another, so nothing is served by the counter outliving it.
_STRIKES_TTL = 86400

# The sent-stamp survives on its own past pop_code(), which deletes the whole
# prompt: the extractor reads the stamp back AFTER it has already taken the
# code, so it cannot live inside the hash pop_code() just removed. Read once,
# within the same request the code was typed in, so a few minutes is generous.
_SENT_STAMP_TTL = 300


@_redis_safe(0)
async def note_timeout(job_id: Optional[str]) -> int:
    """Record one unanswered prompt. Returns the count for this job."""
    if not job_id:
        return 0
    r = await _redis()
    key = _strikes_key(job_id)
    n = await r.incr(key)
    await r.expire(key, _STRIKES_TTL)
    return n


@_redis_safe(0)
async def strikes(job_id: Optional[str]) -> int:
    """Unanswered prompts for this job since the last successful reveal."""
    if not job_id:
        return 0
    r = await _redis()
    v = await r.get(_strikes_key(job_id))
    return int(v) if v else 0


@_redis_safe(None)
async def clear_timeouts(job_id: Optional[str]) -> None:
    """A reveal succeeded: the accounts are not all challenged after all."""
    if not job_id:
        return
    r = await _redis()
    await r.delete(_strikes_key(job_id))


# ── which number was just clicked ────────────────────────────────────────
#
# The forwarder app puts a number on every code, and on a dual-SIM phone that
# number can be the other SIM's (issue #37). Run 44 had three codes for slot
# 2's number arrive labelled with slot 1's, each four to seven seconds after
# the scraper clicked «اطلاعات تماس» on slot 2 — slot 1 had not been clicked
# at all. The label sent them to slot 1: typed into a prompt they did not
# belong to, or held for slot 1's next challenge to type.
#
# The scraper knows what the phone does not: which number it just asked Divar
# to text. So every click that makes Divar send an SMS is noted here the moment
# before it happens, and otp_inbound weighs the label against it.
#
# clicks:{kind} holds each account's latest such click — the contact button, a
# resend, the number typed into the challenge, a login's confirm — for routing
# a code that has just arrived. asked:{kind}:{acct} holds only the click that
# OPENED the account's current challenge, and guards the claim below: a code
# that arrived before that click cannot be the answer to it.
#
# Contact and login clicks are kept apart because the SMS text already says
# which kind a code is, and a login in the panel must not pull a contact code.

# A code reaches the server two to seven seconds after its click (measured,
# 1405/07). Forty-five is generous without reaching back to the listing before.
CLICK_WINDOW = 45
# Two numbers clicked closer together than this cannot be told apart by time.
CLICK_TIE = 5
_CLICKS_TTL = 600
# An SMS is remembered as used for longer than any copy of it can be held.
_USED_TTL = 600


def _now_ms() -> int:
    return int(time.time() * 1000)


@_redis_safe(None)
async def note_click(account: Optional[str], kind: str = "contact", *,
                     opens: bool = False, at_ms: Optional[int] = None) -> None:
    """A click that makes Divar text `account`, noted just before it happens.

    `opens` marks the click that starts a challenge (the contact button, a
    login's confirm), as opposed to one inside it (a resend, the number typed
    into the challenge's own form). Never raises: losing a click costs the
    routing its evidence, not the scrape its listing.
    """
    acct = _digits(account)
    if not acct:
        return
    t = int(at_ms) if at_ms is not None else _now_ms()
    r = await _redis()
    key = _clicks_key(kind)
    await r.zadd(key, {acct: t}, gt=True)
    await r.zremrangebyscore(key, "-inf", _now_ms() - _CLICKS_TTL * 1000)
    await r.expire(key, _CLICKS_TTL)
    if opens:
        await r.set(_asked_key(kind, acct), t, ex=_CLICKS_TTL)


@_redis_safe(dict)
async def recent_clicks(kind: str = "contact", now_ms: Optional[int] = None) -> Dict[str, int]:
    """{account: ms of its latest click} for clicks inside the window."""
    now = int(now_ms) if now_ms is not None else _now_ms()
    r = await _redis()
    rows = await r.zrangebyscore(_clicks_key(kind), now - CLICK_WINDOW * 1000, now, withscores=True)
    return {a: int(s) for a, s in rows}


def pick_account(label: Optional[str], clicks: Dict[str, int], allowed: Iterable[str],
                 now_ms: Optional[int] = None) -> Tuple[str, str]:
    """Whose code this is: (account, how).

    The label stands («label») unless its own number was not clicked at all
    inside the window while one of `allowed` — the phone owner's own numbers,
    worked out by the caller — was. Then the latest of those takes it
    («click»), unless a second one was clicked within CLICK_TIE of it: two
    clicks that close cannot be told apart, and the label stands («tie»).

    A label whose number WAS clicked is never overruled, even by a later click
    elsewhere (the product decision of 1405/07/07): a correctly labelled code that
    arrives slowly is worth more than catching a wrong label while two runs
    work the same phone — and a wrong one held under its label is still kept
    from the next challenge by the claim guard in request().
    """
    lab = _digits(label)
    now = int(now_ms) if now_ms is not None else _now_ms()
    lo = now - CLICK_WINDOW * 1000
    recent = {a: t for a, t in clicks.items() if lo <= t <= now}
    if lab in recent:
        return lab, "label"
    ok = {_digits(a) for a in allowed}
    ranked = sorted(((t, a) for a, t in recent.items() if a in ok), reverse=True)
    if not ranked:
        return lab, "label"
    if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < CLICK_TIE * 1000:
        return lab, "tie"
    return ranked[0][1], "click"


# ── one SMS, one answer ───────────────────────────────────────────────────
#
# The app has posted one SMS twice, 27 ms apart; 3.2.0 posts it once per label
# when the phone does not say which slot it came in on. Every copy used to be
# its own code: a second copy parked under the other label outlived the first
# and was typed into that number's next challenge.
#
# An SMS is identified by the phone, Divar's send stamp and the code. The first
# copy to answer something marks it used and takes its held copies with it; a
# later copy is discarded.


def sms_id(device: Optional[str], sent_stamp: Optional[int], code: str) -> str:
    """The SMS behind a POST, however many times the phone posts it.

    Keyed on SECRET_KEY: it ends up in Redis key names, and a plain hash of a
    six-digit code and a known stamp is a million guesses from the code.
    """
    from app.config import get_settings
    secret = (getattr(get_settings(), "secret_key", "") or "").encode("utf-8")
    msg = f"{device or '-'}|{sent_stamp or '-'}|{code}".encode("utf-8")
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()[:32]


def _meta(raw: Optional[str]) -> dict:
    try:
        v = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    return v if isinstance(v, dict) else {}


async def _drop_copy(r, watch: str, sms: str, *also: str) -> None:
    """Delete `watch` (and `also`) only if it still holds a copy of `sms`.

    WATCHed, so a newer code parked in the same slot between the read and the
    delete survives."""
    async with r.pipeline(transaction=True) as p:
        await p.watch(watch)
        if _meta(await p.get(watch)).get("sms") != sms:
            return
        p.multi()
        p.delete(watch, *also)
        try:
            await p.execute()
        except WatchError:
            pass


async def _purge(r, sms: str) -> None:
    """Drop every held copy of this SMS: it has answered something."""
    for acct in await r.smembers(_copies_key(sms)):
        await _drop_copy(r, _early_key(acct), sms)
        await _drop_copy(r, _login_meta_key(acct), sms, _login_key(acct))
    await r.delete(_copies_key(sms))


async def _use(r, sms: str, *, purge: bool = True) -> bool:
    """Mark one SMS used. False if a copy of it already answered something.
    Its other held copies go with it, unless the caller has yet to find out
    whether its own answer lands (see use_sms)."""
    if not await r.set(_used_key(sms), "1", nx=True, ex=_USED_TTL):
        return False
    if purge:
        await _purge(r, sms)
    return True


@_redis_safe(True)
async def use_sms(sms: Optional[str], *, purge: bool = True) -> bool:
    """Claim this SMS for the prompt it is about to answer. False: a copy of it
    already answered one. True on a Redis blip — the code is not held back for
    a check that could not be made.

    purge=False for an answer that can still fail (submit() into a prompt that
    may have closed a moment ago): the held copies are dropped by drop_copies()
    once it has landed, and survive a release_sms() if it did not."""
    if not sms:
        return True
    return await _use(await _redis(), sms, purge=purge)


@_redis_safe(None)
async def drop_copies(sms: Optional[str]) -> None:
    """The answer use_sms(purge=False) was reserved for has landed."""
    if sms:
        await _purge(await _redis(), sms)


@_redis_safe(None)
async def release_sms(sms: Optional[str]) -> None:
    """use_sms() taken back: the prompt it was meant for had already moved on."""
    if sms:
        r = await _redis()
        await r.delete(_used_key(sms))


@_redis_safe(None)
async def copy_of(sms: Optional[str], account: Optional[str], kind: str = "contact") -> Optional[str]:
    """«used» if this SMS already answered something, «held» if a copy of it
    already waits for this account, else None."""
    if not sms:
        return None
    r = await _redis()
    if await r.exists(_used_key(sms)):
        return "used"
    acct = _digits(account)
    slot = _early_key(acct) if kind == "contact" else _login_meta_key(acct)
    if acct and _meta(await r.get(slot)).get("sms") == sms:
        return "held"
    return None


async def _asked_after(r, kind: str, acct: str, at_ms) -> bool:
    """Whether this account's current challenge was opened AFTER a held code
    arrived — so the code cannot be its answer. No click on record (a worker
    that does not note them yet) is not evidence either way."""
    if at_ms is None:
        return False
    asked = await r.get(_asked_key(kind, acct))
    return bool(asked) and int(asked) > int(at_ms)


async def _note_held_code_refused(acct: str, kind: str, meta: dict) -> None:
    """On the record. Never raises.

    Two different stories end here, and only one is the phone's fault. A code
    held for a number that had not been clicked when it arrived is another
    number's code under this one's label — the visible end of a phone
    labelling its SIMs wrongly, so it goes on the phone's card. A code held
    for a number that HAD just been clicked is that number's own, only late:
    its prompt closed before it came, and a new challenge wants a new code.
    Calling that a wrong label would flag a phone that is set up correctly.
    """
    own = bool(meta.get("seen"))
    reason = "stale_held" if own else "arrived_before_click"
    logger.warning(f"[otp_store] a {kind} code held for {acct} arrived before this "
                   f"account's current challenge — not used ({reason})")
    try:
        from app.services import sms_log
        await sms_log.record(
            sms_log.INBOUND,
            (f"کد {kind} که برای {acct} نگه داشته شده بود به کار نرفت — کد چالش قبلی همین "
             "شماره بود و دیر رسید؛ چالش تازه کد تازه می‌خواهد" if own else
             f"کد {kind} که برای {acct} نگه داشته شده بود به کار نرفت — پیش از کلیک خود "
             "این شماره رسیده بود، پس کد شمارهٔ دیگری بود"),
            level="info" if own else "warning", route="forwarder", account=acct, kind=kind,
            device=meta.get("device"), reason=reason)
    except Exception as e:
        logger.warning(f"[otp_store] could not record a refused code: {e}")


# A code that arrived before anything was waiting for it.
#
# Divar sends the SMS the moment the contact button is clicked. The scraper
# only registers its request after solving a captcha and finding the modal —
# measured live: SMS forwarded and delivered to the server at 20:06:32, the
# request registered at 20:06:38. Six seconds late, and the code was thrown
# away with «no_pending_for_account» while the run sat waiting for a human.
#
# With a forwarder this is the NORMAL order, not a rare race: the phone beats
# the browser almost every time. So an unmatched contact code is parked by
# account, and request() claims it the instant it opens. EARLY_TTL is now a
# Redis expiry rather than a timestamp this module checks by hand — Redis
# forgetting the key IS "too old to use any more".


@_redis_safe(False)
async def park_early_code(account: Optional[str], code: str,
                          sent_stamp_ms: Optional[int] = None, *,
                          sms: Optional[str] = None, at_ms: Optional[int] = None,
                          device: Optional[str] = None, seen: Optional[bool] = None) -> bool:
    """Hold a code nothing is waiting for yet. True if it was parked.

    `at_ms` is when it reached the server (now, by default) — the claim guard
    in request() compares it with the account's own click. `seen`: whether
    this account had just been clicked when it arrived, which tells a late own
    code from another number's if the guard ever refuses it."""
    acct = _digits(account)
    if not acct or not code:
        return False
    r = await _redis()
    await r.set(_early_key(acct), json.dumps({
        "code": code, "sent": sent_stamp_ms, "sms": sms,
        "at": int(at_ms) if at_ms is not None else _now_ms(), "device": device,
        "seen": seen,
    }), ex=EARLY_TTL)
    if sms:
        await r.sadd(_copies_key(sms), acct)
        await r.expire(_copies_key(sms), _USED_TTL)
    return True


async def _claim_early(phone_hint: Optional[str], r) -> Optional[Tuple[str, Optional[int]]]:
    """Take a parked code for this account if one is still fresh and is this
    account's to take. GETDEL, so two requests opening for the same account
    cannot both claim it.

    Refused when it arrived before this account's own challenge was opened —
    a code cannot answer a click that had not happened yet, so it is another
    number's code under this one's label — and when a copy of the same SMS has
    already answered something."""
    acct = _digits(phone_hint)
    if not acct:
        return None
    raw = await r.getdel(_early_key(acct))
    if not raw:
        return None
    data = _meta(raw)
    if await _asked_after(r, "contact", acct, data.get("at")):
        await _note_held_code_refused(acct, "contact", data)
        return None
    if data.get("sms") and not await _use(r, data["sms"]):
        return None
    return data.get("code"), data.get("sent")


@_redis_safe(False)
async def request(key: str, phone_hint: str = "") -> bool:
    """Register a wait for `key`. Returns True if a code parked earlier for
    this account was claimed on the spot — the caller's wait is already over
    before it begins, same as the old code finding its asyncio.Event
    pre-set.

    Starts from a clean slate. The same key — the same listing, retried in
    the same job — can be requested again, and a leftover `code` field or a
    stale entry still sitting in signal:{key} from that earlier round would
    let wait_code() below wake instantly on a code this new prompt was never
    given.
    """
    r = await _redis()
    pkey = _prompt_key(key)
    await r.delete(pkey, _signal_key(key))
    await r.hset(pkey, mapping={"phone_hint": phone_hint, "ts": time.time(),
                                "resend": "0", "resends": "0"})
    await r.expire(pkey, wait_window() + 60)
    await r.sadd(_INDEX_KEY, key)

    early = await _claim_early(phone_hint, r)
    if not early:
        return False
    code, sent = early
    await r.hset(pkey, mapping={"code": code, "source": "forwarder-early"})
    if sent:
        await r.set(_sent_key(key), float(sent) / 1000.0, ex=_SENT_STAMP_TTL)
    await r.rpush(_signal_key(key), "1")
    await r.expire(_signal_key(key), wait_window() + 60)
    return True


@_redis_safe(False)
async def wait_code(key: str, timeout: float) -> bool:
    """Block up to `timeout` seconds for submit() (or an early claim in
    request()) to signal this key. True the instant a code is available —
    BLPOP wakes as soon as the list is pushed to, not on the next poll — and
    honest False on a plain timeout or a Redis hiccup, so the wait loop's own
    cancel/switch/resend checks always get to run every `timeout` seconds
    regardless of which one happened.
    """
    r = await _redis()
    # redis-py 5.0.1's BLPOP wants a whole-second timeout — a float silently
    # corrupts the wire command (fakeredis and real Redis both answer "timeout
    # is negative"). contact_extractor only ever calls this with an integer
    # slice_s; ceil() covers any other caller without ever passing 0, which
    # BLPOP treats as "block forever".
    r_timeout = max(1, math.ceil(timeout))
    got = await r.blpop([_signal_key(key)], timeout=r_timeout)
    return got is not None


# Asking again is worth a hard cap — see MAX_RESENDS above.


@_redis_safe(lambda: {"ok": False, "reason": "no_request",
                      "message": "این درخواست دیگر باز نیست — اسکرپر رد شده و برای آگهی بعدی دوباره می‌پرسد"})
async def ask_resend(key: str) -> dict:
    """Ask the parked browser to press Divar's «ارسال مجدد».

    Only the browser can do it — the control lives on the page it is sitting
    on — so this raises a flag the wait loop picks up within its next slice.
    Returns what to tell the operator.
    """
    r = await _redis()
    entry = await r.hgetall(_prompt_key(key))
    if not entry or entry.get("code"):
        return {"ok": False, "reason": "no_request",
                "message": "این درخواست دیگر باز نیست — اسکرپر رد شده و برای آگهی بعدی دوباره می‌پرسد"}
    if int(entry.get("resends", 0) or 0) >= MAX_RESENDS:
        return {"ok": False, "reason": "limit",
                "message": f"بیشتر از {MAX_RESENDS} بار نمی‌شود کد خواست"}
    await r.hset(_prompt_key(key), "resend", "1")
    return {"ok": True, "message": "درخواست ارسال دوباره ثبت شد"}


@_redis_safe(False)
async def take_resend(key: str) -> bool:
    """Consume a pending resend request. Called only by the wait loop."""
    r = await _redis()
    pkey = _prompt_key(key)
    if await r.hget(pkey, "resend") != "1":
        return False
    await r.hset(pkey, "resend", "0")
    await r.hincrby(pkey, "resends", 1)
    return True


@_redis_safe(None)
async def restart_clock(key: str) -> None:
    """A fresh code deserves a fresh window — otherwise the countdown the
    panel shows belongs to the code that never arrived."""
    r = await _redis()
    pkey = _prompt_key(key)
    if await r.exists(pkey):
        await r.hset(pkey, "ts", str(time.time()))
        await r.expire(pkey, wait_window() + 60)


@_redis_safe(False)
async def submit(key: str, code: str, sent_stamp_ms: Optional[int] = None,
                 source: str = "panel") -> bool:
    """Answer one open prompt.

    WATCH/MULTI, not the exists-then-HSETNX pair this used to be: between
    that exists check and the write, the waiter (a timeout, a cancel, a
    switch) could clear the prompt — and the HSET after it would then
    silently recreate the hash from nothing, with no TTL at all, an immortal
    key nothing ever prunes. WATCH catches exactly that: if the prompt
    changes or disappears before this commits, the whole write is refused
    instead of reviving it.
    """
    r = await _redis()
    pkey = _prompt_key(key)
    async with r.pipeline(transaction=True) as p:
        await p.watch(pkey)
        entry = await p.hgetall(pkey)
        # A prompt already answered has a `code` field, and a second
        # submit() (a stale resend racing a fresh one, a replayed POST) must
        # not overwrite it or re-signal a waiter that already moved on.
        if not entry or entry.get("code"):
            return False
        p.multi()
        p.hset(pkey, mapping={"code": code, "source": source})
        try:
            await p.execute()
        except WatchError:
            return False
    # When the SMS was sent, if the forwarder told us. The extractor reads it
    # back after it types the code, and that difference is the one number a
    # forwarder is judged by.
    if sent_stamp_ms:
        await r.set(_sent_key(key), float(sent_stamp_ms) / 1000.0, ex=_SENT_STAMP_TTL)
    await r.rpush(_signal_key(key), "1")
    await r.expire(_signal_key(key), wait_window() + 60)
    return True


@_redis_safe(None)
async def pop_sent_stamp(key: str) -> Optional[float]:
    """Epoch seconds Divar sent the SMS, once, for the code just typed."""
    r = await _redis()
    v = await r.getdel(_sent_key(key))
    return float(v) if v else None


@_redis_safe(None)
async def find_pending_for_account(account: Optional[str]) -> Optional[Tuple[str, dict]]:
    """The open request whose account matches — by ACCOUNT, never «latest».

    Two accounts can be waiting at once (two jobs, or a rotation mid-pause),
    and a code belongs to the SIM it arrived on. Handing it to whichever
    prompt is newest would type account A's code into account B's modal.
    """
    want = _digits(account)
    if not want:
        return None
    r = await _redis()
    now = time.time()
    window = wait_window()
    members = await r.smembers(_INDEX_KEY)
    best = None
    stale = []
    for k in members:
        entry = await r.hgetall(_prompt_key(k))
        if not entry:
            stale.append(k)
            continue
        ts = float(entry.get("ts", 0) or 0)
        if entry.get("code") or now - ts >= window:
            continue
        if _digits(entry.get("phone_hint")) == want:
            if best is None or ts > best[1]["ts"]:
                best = (k, {"ts": ts, "phone_hint": entry.get("phone_hint", "")})
    if stale:
        await r.srem(_INDEX_KEY, *stale)
    return best


# ── login codes ──
#
# Divar's login flow (auth.py) is driven by a person in the panel, so a
# forwarded login code has nowhere to go immediately. It is parked here for a
# short while so the panel can pick it up instead of the person re-typing what
# the phone already sent. Nothing waits on it; unclaimed codes simply expire
# on Redis's own clock now — GETDEL is the "consumed once" the dict version
# needed a manual pop for.


# The code stays a bare string under login:{acct}, as it always was, and what
# the routing needs to know about it sits beside it in loginmeta:{acct}: a
# pod still running the previous release reads login:{acct} as the code
# itself, and during a rolling deploy it must still get a code, not JSON.


@_redis_safe(None)
async def put_login_code(account: Optional[str], code: str, *,
                         sms: Optional[str] = None, at_ms: Optional[int] = None,
                         device: Optional[str] = None, seen: Optional[bool] = None) -> None:
    acct = _digits(account)
    if not acct or not code:
        return
    r = await _redis()
    # The code rides in the metadata too, so a bare code written beside an
    # older metadata entry — by a pod on the previous release — is not judged
    # by somebody else's arrival time.
    meta = json.dumps({"code": code, "sms": sms, "seen": seen, "device": device,
                       "at": int(at_ms) if at_ms is not None else _now_ms()})
    async with r.pipeline(transaction=True) as p:
        p.set(_login_key(acct), code, ex=LOGIN_CODE_TTL)
        p.set(_login_meta_key(acct), meta, ex=LOGIN_CODE_TTL)
        await p.execute()
    if sms:
        await r.sadd(_copies_key(sms), acct)
        await r.expire(_copies_key(sms), _USED_TTL)


@_redis_safe(None)
async def take_login_code(account: Optional[str]) -> Optional[str]:
    """The parked login code for this number, once — unless it arrived before
    this number's login was started (another number's code under this one's
    label) or a copy of the same SMS was already taken."""
    acct = _digits(account)
    if not acct:
        return None
    r = await _redis()
    async with r.pipeline(transaction=True) as p:
        p.getdel(_login_key(acct))
        p.getdel(_login_meta_key(acct))
        code, raw = await p.execute()
    if not code:
        return None
    meta = _meta(raw)
    if meta.get("code") != code:
        meta = {}
    if await _asked_after(r, "login", acct, meta.get("at")):
        await _note_held_code_refused(acct, "login", meta)
        return None
    if meta.get("sms") and not await _use(r, meta["sms"]):
        return None
    return code


@_redis_safe(list)
async def get_pending() -> list:
    r = await _redis()
    now = time.time()
    window = wait_window()
    members = await r.smembers(_INDEX_KEY)
    out = []
    stale = []
    for k in members:
        entry = await r.hgetall(_prompt_key(k))
        if not entry:
            stale.append(k)
            continue
        ts = float(entry.get("ts", 0) or 0)
        if entry.get("code") or now - ts >= window:
            continue
        resends = int(entry.get("resends", 0) or 0)
        out.append({
            "key": k,
            "phone_hint": entry.get("phone_hint", ""),
            # the countdown is the server's to state: the browser cannot know
            # when the request was registered, only when it noticed
            "remaining": max(int(window - (now - ts)), 0),
            "resends": resends,
            "resends_left": max(MAX_RESENDS - resends, 0),
        })
    if stale:
        await r.srem(_INDEX_KEY, *stale)
    return out


@_redis_safe(None)
async def pop_code(key: str) -> Optional[str]:
    r = await _redis()
    pkey = _prompt_key(key)
    code = await r.hget(pkey, "code")
    await r.delete(pkey)
    await r.srem(_INDEX_KEY, key)
    return code


@_redis_safe(None)
async def clear(key: str) -> None:
    r = await _redis()
    await r.delete(_prompt_key(key), _signal_key(key))
    await r.srem(_INDEX_KEY, key)


@_redis_safe(0)
async def clear_job(job_id: str) -> int:
    """Drop every request belonging to one job; returns how many.

    Keys are «{job_id}:{divar_id}», so cancelling a job takes its prompts with
    it instead of leaving one open against a scrape that has stopped.
    """
    r = await _redis()
    prefix = f"{job_id}:"
    members = await r.smembers(_INDEX_KEY)
    keys = [k for k in members if k.startswith(prefix)]
    for k in keys:
        await r.delete(_prompt_key(k), _signal_key(k))
    if keys:
        await r.srem(_INDEX_KEY, *keys)
    await r.delete(_switch_key(job_id))
    return len(keys)


# ── «use a different number» ─────────────────────────────────────────────
#
# A person watching a run can see what the scraper cannot: the phone behind
# the current Divar number is off, or in somebody else's pocket, and every
# code Divar sends it goes nowhere. They ask for another number from the
# panel; the run picks the request up at its next safe point — between
# listings, or at once if it is parked on a code prompt — and moves.
#
# One pending request per job; a newer one replaces an older one, because
# the latest thing somebody asked for is what they want. No TTL: unlike a
# prompt or a code, there is no natural deadline for "the run gets around to
# checking" — it lives until take_switch() consumes it or clear_job() drops
# it with the rest of that job's state.


@_redis_safe(None)
async def request_switch(job_id, phone: Optional[str] = None, *, by: Optional[int] = None,
                         reason: str = "manual", from_phone: Optional[str] = None) -> None:
    """Ask a running job to move to `phone` (or to its next own number).

    `from_phone` makes it «move off this one»: dropped if, by the time the run
    gets to it, it is already on a different number."""
    if not job_id:
        return
    r = await _redis()
    await r.set(_switch_key(job_id), json.dumps({
        "phone": phone or None, "by": by, "reason": reason,
        "from_phone": from_phone or None, "at": time.time(),
    }))


@_redis_safe(False)
async def has_switch(job_id) -> bool:
    """Whether a switch is waiting — for the OTP wait loop, which must stop
    waiting on the old number without consuming the request."""
    if not job_id:
        return False
    r = await _redis()
    return bool(await r.exists(_switch_key(job_id)))


@_redis_safe(None)
async def take_switch(job_id) -> Optional[dict]:
    """Consume the pending switch for this job, if any."""
    if not job_id:
        return None
    r = await _redis()
    raw = await r.getdel(_switch_key(job_id))
    return json.loads(raw) if raw else None


@_redis_safe(0)
async def cancel_all(job_id: Optional[str] = None) -> int:
    """User declined OTP: drop that job's pending prompts and stop asking it for
    codes for a while. Returns how many pending requests were dropped.

    With no job_id this still suppresses everything, because the dashboard's
    «close» button is not always able to say which job it meant — but the
    caller should pass one whenever it can.
    """
    r = await _redis()
    if job_id is None:
        members = await r.smembers(_INDEX_KEY)
        jobs = {job_of(k) for k in members if job_of(k)}
        for k in members:
            await r.delete(_prompt_key(k), _signal_key(k))
        if members:
            await r.srem(_INDEX_KEY, *members)
        for j in jobs:
            await r.setex(_cancel_key(j), _CANCEL_WINDOW, "1")
        return len(members)

    prefix = f"{job_id}:"
    members = await r.smembers(_INDEX_KEY)
    keys = [k for k in members if k.startswith(prefix)]
    for k in keys:
        await r.delete(_prompt_key(k), _signal_key(k))
    if keys:
        await r.srem(_INDEX_KEY, *keys)
    await r.setex(_cancel_key(job_id), _CANCEL_WINDOW, "1")
    return len(keys)


@_redis_safe(False)
async def is_cancelled(key_or_job: Optional[str] = None) -> bool:
    """True while OTP prompts are suppressed for this job.

    Accepts either a full request key or a bare job id, so callers holding an
    otp_key do not have to split it themselves. Redis's own TTL on
    cancel:{job_id} is the expiry the in-memory version used to check by
    hand — a missing key already means «the window passed».
    """
    job = job_of(key_or_job) if key_or_job else ""
    if not job:
        return False
    r = await _redis()
    return bool(await r.exists(_cancel_key(job)))


@_redis_safe(None)
async def reset_cancel(job_id: Optional[str] = None) -> None:
    """Clear the suppression — called when a fresh scrape job starts."""
    r = await _redis()
    if job_id is None:
        keys = [k async for k in r.scan_iter(match=f"{_cancel_key('*')}")]
        if keys:
            await r.delete(*keys)
    else:
        await r.delete(_cancel_key(job_id))


# ── identity walls ────────────────────────────────────────────────────────
#
# Accounts Divar has asked to prove who they are — national ID, birth date.
# Nothing automated can answer that, so the only useful thing to do with it
# is to say it loudly, where the person who can log in and do it is looking.
# No TTL, same as the switch registry above and for the same reason: an
# unresolved identity check can sit for days, and it must stay on the panel
# for every one of them — the durable copy is cookies.identity_required_at,
# this is the in-memory-turned-Redis copy the panel's poll reads.


@_redis_safe(None)
async def note_identity_required(phone: str, *, job_id=None, text: str = "") -> None:
    acct = _digits(phone)
    if not acct:
        return
    r = await _redis()
    await r.set(_identity_key(acct), json.dumps({
        "phone": phone, "job_id": str(job_id) if job_id else None,
        "at": time.time(), "text": (text or "")[:400],
    }))


@_redis_safe(False)
async def clear_identity_required(phone: str) -> bool:
    r = await _redis()
    return bool(await r.delete(_identity_key(_digits(phone))))


@_redis_safe(list)
async def identity_required() -> list:
    r = await _redis()
    now = time.time()
    out = []
    async for k in r.scan_iter(match=f"{_identity_key('*')}"):
        raw = await r.get(k)
        if not raw:
            continue
        v = json.loads(raw)
        v["age"] = int(now - v.get("at", now))
        out.append(v)
    return out
