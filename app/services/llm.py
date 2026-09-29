"""
هوش مصنوعی — the one door to the model gateway.

Every agent (app/ai/, and the explainer in match_service) talks to Liara
through this module and nothing else. What lives here, once:

  * the connection — an OpenAI-compatible gateway; the key and the base URL
    come from the environment (GitHub-managed, SECRETS.md §2d), never from
    the panel;
  * which model does which job — write (Persian a person reads), read
    (listings and needs), vision (photos), embed — defaults from the
    environment, overridable from the panel's AI card;
  * the privacy rule — phone numbers and e-mail addresses are masked before
    anything leaves for a third party, in one place, for every agent;
  * the ledger and the cap — one ai_usage row per call with Liara's own cost
    figure, and a daily cap in dollars: past it, background agents stop until
    tomorrow (BudgetExceeded) and the panel says so;
  * the gateway's own «no credit» — a 402, or a 403/429 whose body says the
    credit, quota or balance is used up, pauses every agent until the next
    Tehran midnight or until the card's test is answered (QuotaExhausted, a
    BudgetExceeded), and a refusal's body is kept, scrubbed, so the log and the
    card can say what it was;
  * structured answers — JSON mode with a pydantic schema, one retry on a
    malformed answer, then LLMError — never a half-parsed dict.

Nothing here is on a request path that matters: a caller that cannot afford
to fail catches LLMError and carries on without the model, which is the
rule for every agent (the ranking ships, the reason does not).
"""
import asyncio
import json
import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, NoReturn, Optional, Tuple, Type

import httpx
from loguru import logger
from sqlalchemy import func, select

from app.config import get_settings
from app.services import secret_box

settings = get_settings()

JOBS = ("write", "read", "vision", "embed")
# panel overrides of the environment's per-job models, and the card's knobs
KEY_MODELS = {job: f"ai_model_{job}" for job in JOBS}
KEY_CAP = "ai_daily_cap_usd"
KEY_NOTES = "ai_office_notes"       # appended to every «write» prompt — the office's own rules
KEY_ENABLED = "ai_enabled"
# The gateway said the credit is gone: a JSON object in app_settings — the one
# place every pod reads — naming when it began, until when it lasts and the
# gateway's own words. Absent, unreadable or past its `until` = no pause.
KEY_PAUSE = "ai_quota_pause"
# One switch per agent, so a noisy one can be stopped without stopping the
# rest. Absent = on: an agent added later runs without a settings row.
AGENTS = ("explainer", "reader", "need", "embed", "vision", "assistant")


def agent_key(agent: str) -> str:
    return f"ai_agent_{agent}"


def agent_cap_key(agent: str) -> str:
    return f"ai_agent_cap_{agent}"


DEFAULT_CAP_USD = 2.0
# a runaway agent gets half the office's daily budget by default — enough to
# keep working, never enough to spend the whole day alone
AGENT_CAP_SHARE = 0.5
TIMEOUT = 40.0
# reasoning models (Liara's GLM family): the floor and the ceiling of the
# budget they get, because their thinking comes out of the same max_tokens
REASONING_MIN_TOKENS = 1500
REASONING_MAX_TOKENS = 6000
REASONING_PREFIXES = ("z-ai/", "deepseek/", "moonshotai/")


def _reasons(model: str) -> bool:
    return (model or "").lower().startswith(REASONING_PREFIXES)
# Fixed +03:30 — the production image has no tz database (see call_queue.TEHRAN).
TEHRAN = timezone(timedelta(hours=3, minutes=30), "Asia/Tehran")


class LLMError(Exception):
    """The model did not answer usably. Callers that must not fail catch this."""


class NotConfigured(LLMError):
    pass


class Disabled(LLMError):
    pass


class BudgetExceeded(LLMError):
    pass


class QuotaExhausted(BudgetExceeded):
    """The GATEWAY says the account's credit, quota or balance is used up — not
    our own daily cap, which is BudgetExceeded. It is one, on purpose: every
    caller that already ends a pass on a budget error, does not count it
    against the listing and carries on without the model, does exactly that
    here. `str(e)` is one stable Persian sentence for the whole pause (so a log
    that dedupes on it stays quiet) and does not carry the gateway's words —
    those are `gateway_message`, for the admin's card and the log."""

    def __init__(self, message: str, *, gateway_message: str = "", status: Optional[int] = None,
                 until: Optional[datetime] = None):
        super().__init__(message)
        self.gateway_message = gateway_message
        self.status = status
        self.until = until


class CircuitOpen(LLMError):
    """The gateway failed repeatedly; calls are refused without going out
    until the cooldown ends."""


class RateLimited(LLMError):
    """The gateway itself said to back off. `retry_after` is seconds, the
    same shape as VerificationError elsewhere in the codebase."""

    def __init__(self, message: str, retry_after: float):
        super().__init__(message)
        self.retry_after = retry_after


# ── configuration ────────────────────────────────────────────────────────────

def env_models() -> Dict[str, str]:
    return {
        "write": (settings.llm_model or "").strip(),
        "read": (getattr(settings, "llm_model_read", "") or "").strip(),
        "vision": (getattr(settings, "llm_model_vision", "") or "").strip(),
        "embed": (getattr(settings, "llm_model_embed", "") or "").strip(),
    }


def configured() -> bool:
    return bool((settings.llm_api_key or "").strip() and (settings.llm_base_url or "").strip())


def workspace_id() -> str:
    """The workspace (project) in the base URL — for the card, not the key."""
    base = (settings.llm_base_url or "").strip()
    m = re.search(r"/api/([0-9a-f]{24})/", base + "/")
    return m.group(1) if m else ""


async def config(db) -> Dict[str, Any]:
    """What is in effect: env first, the panel's overrides on top."""
    agent_cap_keys = tuple(agent_cap_key(a) for a in AGENTS)
    rows = {}
    try:
        rows = await secret_box.get_many(db, (*KEY_MODELS.values(), KEY_CAP, KEY_NOTES, KEY_ENABLED,
                                               KEY_PAUSE, *agent_cap_keys))
    except Exception as e:
        logger.warning(f"[ai] settings unreadable: {e}")
    models, sources = {}, {}
    for job, env_val in env_models().items():
        panel = (rows.get(KEY_MODELS[job]) or "").strip()
        models[job] = panel or env_val
        sources[job] = "panel" if panel else "env"
    try:
        cap = float(rows.get(KEY_CAP)) if rows.get(KEY_CAP) else DEFAULT_CAP_USD
    except ValueError:
        cap = DEFAULT_CAP_USD
    # each agent's own ceiling — a panel override, or half the global cap so
    # it moves with it when nobody has set one by hand
    agent_caps = {}
    for a in AGENTS:
        raw = rows.get(agent_cap_key(a))
        try:
            agent_caps[a] = float(raw) if raw not in (None, "") else round(cap * AGENT_CAP_SHARE, 4)
        except ValueError:
            agent_caps[a] = round(cap * AGENT_CAP_SHARE, 4)
    enabled = (rows.get(KEY_ENABLED) or "true").lower() != "false"
    return {
        "configured": configured(),
        "enabled": enabled,
        "workspace": workspace_id(),
        "models": models,
        "model_sources": sources,
        "cap_usd": cap,
        "agent_caps": agent_caps,
        "notes": rows.get(KEY_NOTES) or "",
        # the gateway said the credit is gone and the agents are waiting — or None
        "pause": _parse_pause(rows.get(KEY_PAUSE)),
    }


# ── privacy ──────────────────────────────────────────────────────────────────

# Mobile numbers: an optional country/trunk prefix, then a real carrier
# prefix — Iranian mobiles only ever start 90x/91x/92x/93x/99x, which is what
# keeps a bare 10-digit price like «9500000000» or a postal code from reading
# as a phone number — then the rest of the number with at most one separator
# between any two digits (no run of two spaces, no doubled dash).
_MOBILE = re.compile(r"(?<!\d)(?:0098|\+?98|0)?9[0139](?:[-.\s]?\d){8}(?!\d)")
# Landlines: trunk 0, a 2–3 digit area code (optionally in parens), then a
# 7–8 digit local number — same loose, single-separator rule.
_LANDLINE = re.compile(r"(?<!\d)\(?0\d{2,3}\)?[-.\s]?\d(?:[-.\s]?\d){6,7}(?!\d)")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

# Persian and Arabic-Indic digits fold to ASCII one-for-one, so the
# normalised copy is exactly as long as the original and its match offsets
# still point at the right characters in it.
_DIGIT_FOLD = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def mask_pii(text: Optional[str]) -> str:
    """Phone numbers and e-mail addresses out, before text leaves the office.
    A listing's text is the owner's; a need's text is the customer's. Neither
    number is the model's business, and the model does not need them to do
    its job.

    Numbers are found on a digit-folded copy — Persian, Arabic-Indic and
    plain digits (and mixes of the three within one number) all become
    ASCII — then the matched spans are cut from the original, which is what
    lets one pair of patterns catch every digit system instead of one
    pattern per script. A price, a date, a listing code or a postal code
    never has a real carrier prefix or the exact landline shape, so they
    read through untouched; see tests/test_ai_core.py for the cases this
    was tuned against."""
    if not text:
        return ""
    t = str(text)
    normalized = t.translate(_DIGIT_FOLD)
    spans: Dict[Tuple[int, int], str] = {}
    for rx, mask in ((_LANDLINE, "۰××××××××"), (_MOBILE, "۰۹×××××××××")):
        for m in rx.finditer(normalized):
            spans[m.span()] = mask   # a mobile match on the same span as a
                                      # landline one (it can look like both)
                                      # wins — it is the narrower, surer read
    out, cursor = [], 0
    for (start, end), mask in sorted(spans.items()):
        if start < cursor:
            continue   # already covered by an earlier, wider span
        out.append(t[cursor:start])
        out.append(mask)
        cursor = end
    out.append(t[cursor:])
    return _EMAIL.sub("ایمیل", "".join(out))


# ── the ledger and the cap ───────────────────────────────────────────────────

def _day_start_utc(now: Optional[datetime] = None) -> datetime:
    local = (now or datetime.now(timezone.utc)).astimezone(TEHRAN)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


async def agent_enabled(db, agent: str) -> bool:
    """Is this agent switched on? Its own switch, then the global one."""
    try:
        rows = await secret_box.get_many(db, (agent_key(agent), KEY_ENABLED))
    except Exception:
        return True
    if (rows.get(KEY_ENABLED) or "true").lower() == "false":
        return False
    return (rows.get(agent_key(agent)) or "true").lower() != "false"


async def agents_enabled(db) -> Dict[str, bool]:
    try:
        rows = await secret_box.get_many(db, tuple(agent_key(a) for a in AGENTS))
    except Exception:
        rows = {}
    return {a: (rows.get(agent_key(a)) or "true").lower() != "false" for a in AGENTS}


async def spent_today(db, agent: Optional[str] = None) -> float:
    from app.models.ai_usage import AiUsage
    q = select(func.coalesce(func.sum(AiUsage.cost_usd), 0.0)).where(AiUsage.created_at >= _day_start_utc())
    if agent:
        q = q.where(AiUsage.agent == agent)
    return float((await db.execute(q)).scalar_one() or 0.0)


# ── the pause: the gateway itself has no credit left ─────────────────────────
#
# Unlike the breaker (one process's memory, see the note above _Breaker), this
# lives in app_settings: the API pods, the worker and the scheduler each run
# agents of their own, and the one that first meets «no credit» must stop them
# all. It ends at the next Tehran midnight, or when the AI card's test is
# answered, whichever comes first — nobody has to remember to lift it, and
# nobody has to wait for midnight after topping the account up.

# What this process has already said about a pause, so that many calls meeting
# the same one make one log line each — not one per listing. A pause is told
# apart from the next one by when it began (`since`): one that ends and a later
# one the same day are two news items.
_pause_told: Dict[str, str] = {}      # "since": the pause this process last logged the start of
_pause_noted: Dict[str, str] = {}     # loop tag -> `since` of the pause it last noted


def _next_tehran_midnight(now: Optional[datetime] = None) -> datetime:
    """When the Tehran day `now` falls in ends, in UTC — the day the daily cap
    counts, and so the day a credit pause lasts. Fixed +03:30, no tz database."""
    return _day_start_utc(now) + timedelta(days=1)


def _parse_pause(raw: Optional[str], now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """The pause stored in `raw`, or None: nothing there, not JSON, no end
    date, or the end has passed — which is how it lifts itself at midnight."""
    if not raw:
        return None
    try:
        state = json.loads(raw)
        until = datetime.fromisoformat(state["until"])
    except (ValueError, KeyError, TypeError):
        return None
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    if (now or datetime.now(timezone.utc)) >= until:
        return None
    return {"since": str(state.get("since") or ""), "until": until.isoformat(),
            "status": state.get("status"), "message": str(state.get("message") or ""),
            "agent": str(state.get("agent") or ""), "job": str(state.get("job") or "")}


async def pause_state(db=None) -> Optional[Dict[str, Any]]:
    """The credit pause in force, or None. What a background loop asks before
    it looks at its queue — a pass that would only be refused at the door
    should not fetch a photo or prepare a prompt first. Never raises: a
    settings table nobody can read is not a reason to stop the agents."""
    from app.database import async_session_maker
    own = db is None
    session = async_session_maker() if own else db
    try:
        rows = await secret_box.get_many(session, (KEY_PAUSE,))
    except Exception as e:
        logger.warning(f"[ai] pause state unreadable: {type(e).__name__}: {e}")
        return None
    finally:
        if own:
            await session.close()
    return _parse_pause(rows.get(KEY_PAUSE))


async def clear_pause(db, actor: str = "ai_test") -> bool:
    """Lift the pause — the card's test just got an answer, so the account has
    credit. True when there was one to lift."""
    try:
        if not _parse_pause((await secret_box.get_many(db, (KEY_PAUSE,))).get(KEY_PAUSE)):
            return False
        await secret_box.put(db, KEY_PAUSE, None, actor)
    except Exception as e:
        logger.warning(f"[ai] credit pause could not be lifted: {type(e).__name__}: {e}")
        return False
    logger.info("[ai] credit pause lifted: the panel's test got an answer from the gateway")
    return True


def note_pause(tag: str, pause: Optional[Dict[str, Any]]) -> None:
    """A background loop's one line about a pause, when it begins for that
    loop — not on every pass. With no pause it forgets, so the next one is
    news again."""
    if not pause:
        _pause_noted.pop(tag, None)
        return
    since = pause.get("since") or ""
    if _pause_noted.get(tag) == since:
        return
    _pause_noted[tag] = since
    logger.info(f"[{tag}] paused until the next Tehran midnight or a successful test — "
                f"the gateway said: {pause.get('message') or '—'}")


def _pause_error(state: Dict[str, Any]) -> QuotaExhausted:
    """What a call refused at the door raises. One sentence for the whole
    pause: it names the hour and the way out, not the gateway's words."""
    until: Optional[datetime]
    try:
        until = datetime.fromisoformat(state["until"])
    except (ValueError, KeyError, TypeError):
        until = None
    hour = until.astimezone(TEHRAN).strftime("%H:%M") if until else "00:00"
    return QuotaExhausted(
        f"اعتبار یا سهمیهٔ سرویس هوش مصنوعی تمام شده است (HTTP {state.get('status')}) — "
        f"ایجنت‌ها تا فردا ساعت {hour} (به وقت تهران) یا تا موفق شدن «تست اتصال» در کارت هوش مصنوعی متوقف‌اند",
        gateway_message=str(state.get("message") or ""), status=state.get("status"), until=until)


async def _record(agent: str, job: str, model: str, usage: Dict[str, Any], ms: int,
                  ok: bool, error: str = "") -> None:
    """One row, on its own session — a ledger that fails must not look like a
    model that failed."""
    from app.database import async_session_maker
    from app.models.ai_usage import AiUsage
    try:
        async with async_session_maker() as s:
            s.add(AiUsage(agent=agent[:40], job=job, model=(model or "")[:120],
                          prompt_tokens=int(usage.get("prompt_tokens") or 0),
                          completion_tokens=int(usage.get("completion_tokens") or 0),
                          cost_usd=float(usage.get("cost") or 0.0),
                          cost_toman=float(usage.get("total_cost_toman") or 0.0),
                          ms=ms, ok=ok, error=(error or "")[:300] or None))
            await s.commit()
    except Exception as e:
        logger.warning(f"[ai] ledger write failed: {e}")


async def usage_summary(db) -> Dict[str, Any]:
    """Today and this month, in total and per agent — the card's numbers."""
    from app.models.ai_usage import AiUsage
    now = datetime.now(timezone.utc)
    day = _day_start_utc(now)
    month = now.astimezone(TEHRAN).replace(day=1, hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)

    async def _sum(since):
        row = (await db.execute(
            select(func.count(AiUsage.id), func.coalesce(func.sum(AiUsage.cost_usd), 0.0),
                   func.coalesce(func.sum(AiUsage.cost_toman), 0.0),
                   func.coalesce(func.sum(AiUsage.prompt_tokens + AiUsage.completion_tokens), 0))
            .where(AiUsage.created_at >= since))).one()
        # six decimals: a handful of tiny calls is $0.000013, not $0.0
        return {"calls": int(row[0]), "cost_usd": round(float(row[1]), 6),
                "cost_toman": round(float(row[2])), "tokens": int(row[3])}

    from sqlalchemy import Integer, case
    per = (await db.execute(
        select(AiUsage.agent, func.count(AiUsage.id), func.coalesce(func.sum(AiUsage.cost_usd), 0.0),
               func.coalesce(func.sum(AiUsage.cost_toman), 0.0),
               func.coalesce(func.sum(case((AiUsage.ok.is_(False), 1), else_=0)), 0))
        .where(AiUsage.created_at >= month).group_by(AiUsage.agent)
        .order_by(func.sum(AiUsage.cost_usd).desc()))).all()
    last = (await db.execute(select(AiUsage).order_by(AiUsage.created_at.desc()).limit(1))).scalars().first()
    return {
        "today": await _sum(day),
        "month": await _sum(month),
        "by_agent": [{"agent": a, "calls": int(c), "cost_usd": round(float(u), 6),
                      "cost_toman": round(float(t)), "failed": int(f or 0)} for a, c, u, t, f in per],
        "last": last.to_dict() if last else None,
    }


# ── the calls ────────────────────────────────────────────────────────────────

def _headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {(settings.llm_api_key or '').strip()}",
            "Content-Type": "application/json"}


def _url(path: str) -> str:
    return f"{(settings.llm_base_url or '').strip().rstrip('/')}/{path.lstrip('/')}"


async def _gate(db, cfg: Optional[Dict[str, Any]] = None, agent: str = "") -> Dict[str, Any]:
    """Configured, switched on, and under both caps — or the reason it is not."""
    if not configured():
        raise NotConfigured("هوش مصنوعی تنظیم نشده است (LLM_API_KEY / LLM_BASE_URL)")
    cfg = cfg or await config(db)
    if not cfg["enabled"]:
        raise Disabled("هوش مصنوعی از پنل خاموش است")
    if agent and not await agent_enabled(db, agent):
        raise Disabled(f"این ایجنت از پنل خاموش است ({agent})")
    if cfg.get("pause"):
        # the gateway said the credit is gone: nothing goes out until midnight
        # or until the card's test is answered — one refusal, not one per listing
        raise _pause_error(cfg["pause"])
    spent = await spent_today(db)
    if spent >= cfg["cap_usd"]:
        who = f" — ایجنت «{agent}»" if agent else ""
        raise BudgetExceeded(f"سقف روزانهٔ کل پر شد{who} ({spent:.2f} از {cfg['cap_usd']:.2f} دلار)")
    if agent:
        # the agent's own ceiling, on top of the shared one — a noisy agent
        # stops alone and the rest of the office keeps working
        agent_cap = cfg["agent_caps"].get(agent, cfg["cap_usd"] * AGENT_CAP_SHARE)
        agent_spent = await spent_today(db, agent=agent)
        if agent_spent >= agent_cap:
            raise BudgetExceeded(f"سقف روزانهٔ ایجنت «{agent}» پر شد ({agent_spent:.2f} از {agent_cap:.2f} دلار)")
    return cfg


# The keys a gateway will accept back in `messages`. Liara answers a tool call
# with `refusal: null` and `reasoning: null` beside the call, then refuses the
# next request with «messages[2].refusal must be a string» when they are echoed
# — so a turn is trimmed to what it means before it goes back.
_TURN_KEYS = ("role", "content", "tool_calls", "name", "tool_call_id")


def _clean_turn(message: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: v for k, v in message.items() if k in _TURN_KEYS and v is not None}
    out.setdefault("role", "assistant")
    if out.get("tool_calls"):
        # `index` is a streaming artefact; some gateways reject it on the way back
        out["tool_calls"] = [{k: v for k, v in c.items() if k in ("id", "type", "function")}
                             for c in out["tool_calls"]]
    return out


def _extract_json(content: str) -> Any:
    """The model's JSON, even when it wrapped it in a code fence."""
    text = (content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S).strip()
    return json.loads(text)


# ── the circuit breaker ──────────────────────────────────────────────────────
#
# ponytail: state lives in this process's memory. A second replica has its
# own breaker and its own failure count, so one pod tripping does not pause
# another — move this to Redis (app/services/verification.py already leans
# on it for the same reason) if the backend ever runs more than one.

_BREAKER_BASE_COOLDOWN = 30.0
_BREAKER_MAX_COOLDOWN = 600.0   # 10 minutes — the escalating ceiling
_BREAKER_FAIL_THRESHOLD = 5
_RETRY_AFTER_CAP = 900.0        # 15 minutes — Liara's own word wins, capped
# A half-open trial that never reports back — cancelled, or failed somewhere
# no outcome is recorded — must not hold the one slot for the life of the
# process: after this long another trial goes through. Longer than the
# slowest call (a reasoning model's 90 s timeout, and its one retry).
_BREAKER_TRIAL_LEASE = 200.0


class _Breaker:
    def __init__(self) -> None:
        self.state = "closed"   # closed | open | half_open
        self.fails = 0
        self._next_cooldown = _BREAKER_BASE_COOLDOWN
        self.until_monotonic = 0.0
        self.until_at: Optional[datetime] = None
        self.trial_at = 0.0

    def allow(self) -> bool:
        now = time.monotonic()
        if self.state == "closed":
            return True
        if self.state == "open":
            if now < self.until_monotonic:
                return False
            self.state, self.trial_at = "half_open", now   # cooldown passed — one trial call through
            return True
        # half_open: the one trial is out — unless it never came back
        if now - self.trial_at > _BREAKER_TRIAL_LEASE:
            self.trial_at = now
            return True
        return False

    def on_success(self) -> None:
        self.state, self.fails = "closed", 0
        self._next_cooldown = _BREAKER_BASE_COOLDOWN

    def on_failure(self, retry_after: Optional[float] = None) -> None:
        if retry_after is not None:
            # the gateway said how long, so its word wins over our own count
            self._open(min(retry_after, _RETRY_AFTER_CAP))
            self.fails = 0
            return
        if self.state == "half_open":
            self._escalate()   # the trial failed — wait longer next time
            return
        self.fails += 1
        if self.fails >= _BREAKER_FAIL_THRESHOLD:
            self._escalate()

    def _escalate(self) -> None:
        self._open(self._next_cooldown)
        self._next_cooldown = min(self._next_cooldown * 2, _BREAKER_MAX_COOLDOWN)
        self.fails = 0

    def _open(self, seconds: float) -> None:
        self.state = "open"
        self.until_monotonic = time.monotonic() + seconds
        self.until_at = datetime.now(timezone.utc) + timedelta(seconds=seconds)


_breaker = _Breaker()


def breaker_status() -> Dict[str, Any]:
    """State and until when, for the AI card's «مکث تا …». Read-only — it is
    the next call, not this read, that flips open → half-open once the
    cooldown has passed."""
    return {"state": _breaker.state,
            "until": _breaker.until_at.isoformat() if _breaker.state == "open" else None}


def _circuit_open() -> CircuitOpen:
    until = _breaker.until_at.astimezone(TEHRAN).strftime("%H:%M") if _breaker.until_at else "چند لحظهٔ دیگر"
    return CircuitOpen(f"مسیر هوش مصنوعی به‌خاطر خطاهای پیاپی موقتاً متوقف است — تا ساعت {until} دوباره تلاش کنید")


def _parse_retry_after(value: Optional[str]) -> Optional[float]:
    """Seconds to wait — Liara sends either a plain integer or an HTTP-date."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError):
        return None


# ── cost from tokens, when the provider does not report it ──────────────────
#
# Liara returns usage.cost on most calls; a model that does not gets recorded
# at $0 today, so the daily caps never see what it actually spent. USD per 1M
# tokens (input, output) — public list prices, and they drift over time; a
# rough charge beats a silent $0 that never trips a cap. Longer, narrower
# names are listed first so "gpt-4.1-nano" is not priced as "gpt-4.1-mini".
_PRICES: List[Tuple[str, float, float]] = [
    ("gpt-4.1-nano", 0.10, 0.40),
    ("gpt-4.1-mini", 0.40, 1.60),
    ("gpt-4.1", 2.00, 8.00),
    ("gpt-4o-mini", 0.15, 0.60),
    ("gpt-4o", 2.50, 10.00),
    ("text-embedding-3-large", 0.13, 0.0),
    ("text-embedding-3-small", 0.02, 0.0),
    ("gemini-2.0-flash", 0.10, 0.40),
    ("gemini-2.5-flash", 0.30, 2.50),
    ("gemini", 0.30, 2.50),              # an unnamed 2.x flash generation
    ("deepseek", 0.28, 0.42),
    ("glm", 0.60, 2.20),                 # z-ai/glm — Liara's reasoning family
]
_DEFAULT_PRICE = (1.00, 3.00)   # unknown model: priced like a mid model, not
                                 # a mini one, so an unpriced model errs safe
_warned_models: set = set()


def _price_for(model: str) -> Tuple[float, float]:
    """(input, output) USD per 1M tokens — a family match, or a conservative
    default logged once per model name we have not seen."""
    name = (model or "").lower()
    for key, p_in, p_out in _PRICES:
        if key in name:
            return p_in, p_out
    if name and name not in _warned_models:
        _warned_models.add(name)
        logger.warning(f"[ai] no price on file for model {model!r} — using the conservative default")
    return _DEFAULT_PRICE


def _estimate_tokens(text: str) -> int:
    """Persian runs about 3 characters per token — used only when the
    gateway reports no token counts at all."""
    n = len(text or "")
    return max(1, round(n / 3)) if n else 0


def _fill_cost(model: str, usage: Dict[str, Any], sent: str, received: str = "") -> Dict[str, Any]:
    """`usage` with `cost` filled in when Liara did not report one (or
    reported 0 with real tokens) — priced from `_PRICES`, on token counts
    estimated from characters when even those are missing. Returns a copy;
    the caller's own dict is left alone."""
    usage = dict(usage or {})
    if float(usage.get("cost") or 0) > 0:
        return usage
    p_tok = int(usage.get("prompt_tokens") or 0)
    c_tok = int(usage.get("completion_tokens") or 0)
    if p_tok <= 0 and c_tok <= 0:
        p_tok, c_tok = _estimate_tokens(sent), _estimate_tokens(received)
        if p_tok or c_tok:
            usage["prompt_tokens"], usage["completion_tokens"] = p_tok, c_tok
            logger.info(f"[ai] {model}: no token counts from the gateway — estimated {p_tok}+{c_tok} from characters")
    if p_tok <= 0 and c_tok <= 0:
        return usage
    p_in, p_out = _price_for(model)
    usage["cost"] = p_tok * p_in / 1_000_000 + c_tok * p_out / 1_000_000
    return usage


# ── what the gateway said when it refused ────────────────────────────────────
#
# The door used to keep resp.text[:160]: «HTTP 400: {"error": {"message": "…»
# and the sentence that says why was on the far side of the cut. A refusal is
# now kept whole — compacted, scrubbed, at most ERROR_BODY_MAX characters — in
# the error a caller logs, and as far as the ledger's column reaches.

ERROR_BODY_MAX = 1000       # characters of a refusal's body an error, a log line and the pause keep
_BODY_READ_MAX = 20000      # what is read of a body at all, before it is scrubbed: a proxy's HTML error page is not data
_SPACES = re.compile(r"\s+")


def _clip(text: str, limit: int) -> str:
    text = _SPACES.sub(" ", text or "").strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _scrub(text: str) -> str:
    """What a refusal may say before it is stored or logged: the API key itself
    out (some gateways echo the key they refused), then the same masking as
    everything else that leaves or is kept — phone numbers and e-mail addresses."""
    key = (settings.llm_api_key or "").strip()
    if len(key) >= 8:
        text = text.replace(key, "***")
    return mask_pii(text)


def _reason_in(data: Any, depth: int = 0) -> str:
    """The sentence in an error body: OpenAI's {"error": {"message": …}} and
    the few other shapes gateways use ({"message"}, {"detail"}, {"error": "…"})."""
    if depth > 3:
        return ""
    if isinstance(data, str):
        return data
    if isinstance(data, list):
        return "; ".join(filter(None, (_reason_in(x, depth + 1) for x in data[:3])))
    if not isinstance(data, dict):
        return ""
    err = data.get("error")
    for holder in (err if isinstance(err, dict) else None, data):
        if isinstance(holder, dict):
            for k in ("message", "detail", "msg", "error_description", "description"):
                found = _reason_in(holder.get(k), depth + 1)
                if found.strip():
                    return found
    return err if isinstance(err, str) else ""


def _refusal(resp) -> Tuple[str, str]:
    """(body, reason) of a refused call: the whole body, and the reason inside
    it in the gateway's own words — both scrubbed and bounded. A body that is
    not JSON (a proxy's page, plain text) is its own reason."""
    try:
        raw = (resp.text or "")[:_BODY_READ_MAX]
    except Exception:
        raw = ""
    body = reason = raw
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    if isinstance(data, (dict, list)):
        # re-dumped without \uXXXX escapes, so Persian in a body is Persian to
        # the check below and to whoever reads the log
        body = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        reason = _reason_in(data) or body
    return _clip(_scrub(body), ERROR_BODY_MAX), _clip(_scrub(reason), ERROR_BODY_MAX)


# A refusal that says the money is gone. Two tiers, so that a rate limit with
# the word «limit» in it is never mistaken for one: STRONG says credit, quota or
# balance outright, and holds even with a Retry-After; WEAK names a period or
# the workspace beside a limit («Workspace daily token limit exceeded») and is
# read only when nothing says «rate» and no Retry-After came. Lower-cased text.
_QUOTA_STRONG = re.compile("|".join((
    r"insufficient[\s_-]*(?:quota|credits?|balance|funds?)",
    r"exceeded (?:your|the|its|their) (?:current |free |daily |monthly )?(?:quota|credits?)\b",
    r"\b(?:quota|credits?|balance|funds?)\b[^.\n]{0,40}\b(?:exhausted|depleted|used[\s-]up|insufficient|not enough|"
    r"too low|(?:has|have) run out|ran out|is (?:zero|negative|empty|over))\b",
    r"\btokens\b[^.\n]{0,40}\b(?:exhausted|depleted|used[\s-]up|(?:has|have) run out|ran out)\b",
    r"(?:not|n't)[\s-]+(?:have\s+)?enough[^.\n]{0,20}\b(?:credits?|balance|funds?|quota|tokens)\b",
    r"\b(?:out of|ran out of|run out of|exhausted|depleted)\b[^.\n]{0,20}\b(?:quota|credits?|balance|funds?|(?:free )?tokens)\b",
    r"\bno[\s-]+(?:quota|credits?|balance|funds?)\b",
    r"\bno[\s-]+(?:remaining|more|free|available)[\s-]+(?:quota|credits?|balance|funds?|tokens?)\b",
    r"payment required|top[\s-]?up|add (?:more )?(?:credits?|funds)|recharge (?:your|the)",
    r"اعتبار[^.\n]{0,30}(?:تمام|پایان|کافی نیست|ناکافی|صفر)",
    r"موجودی[^.\n]{0,30}(?:کافی نیست|ناکافی|صفر|تمام)",
    r"(?:شارژ|سهمیه)[^.\n]{0,30}(?:تمام|پایان|کافی نیست|ناکافی)",
    r"سقف[^.\n]{0,30}(?:مصرف|توکن|روزانه|ماهانه|اعتبار)",
)))
_QUOTA_WEAK = re.compile("|".join((
    r"\bworkspace\b[^.\n]{0,80}\b(?:limit|quota|credits?|balance|budget)\b",
    r"\b(?:usage|spending|token|free[\s-]*token)s?[\s_-]*(?:limit|cap|quota|allowance|budget)\b",
    r"\b(?:daily|monthly|weekly)\b[^.\n]{0,20}\b(?:limit|cap|quota|allowance|budget)\b",
    r"\bquota\b[^.\n]{0,30}\b(?:reached|exceeded|exhausted)\b",
    r"\b(?:reached|exceeded|hit)\b[^.\n]{0,20}\b(?:daily|monthly|weekly|usage|spending|quota)\b",
    r"\bfree[\s-]*tokens?\b[^.\n]{0,30}\b(?:finished|ended|over|gone|used|left)\b",
)))
_RATE_LIMIT = re.compile("|".join((
    r"rate[\s_-]*limit", r"too many (?:concurrent )?requests", r"slow down", r"concurren\w+",
    r"requests?[\s-]*(?:per|/)[\s-]*(?:min|sec|hour)", r"per[\s-]*(?:minute|min|second|sec|hour)\b",
    r"\b(?:rpm|tpm|rps)\b",
)))


def quota_exhausted(status: int, body: str, *, retry_after: bool = False) -> bool:
    """Does this refusal say the account's credit, quota or balance is used up?

    A 402 always does. A 403 or 429 does when its body says so; a plain 429 —
    «rate limit», «too many requests», a per-minute quota, or any answer that
    came with a Retry-After — stays what it always was: a rate limit, which the
    breaker and Retry-After already handle. Anything else (a 401 is the key, a
    400 the request, a 5xx the road) never does, whatever it says."""
    if status == 402:
        return True
    if status not in (403, 429):
        return False
    text = (body or "").lower()
    if _QUOTA_STRONG.search(text):
        return True
    if retry_after or _RATE_LIMIT.search(text):
        return False
    return bool(_QUOTA_WEAK.search(text))


async def _pause_for_quota(*, status: int, reason: str, agent: str, job: str) -> QuotaExhausted:
    """Pause every agent until the next Tehran midnight, and return the error
    to raise. Written on a session of its own, like the ledger — the caller's
    may be in the middle of a listing — and read back first: a second refusal
    the same day keeps when the pause began, and takes the newest words."""
    now = datetime.now(timezone.utc)
    state: Dict[str, Any] = {"since": now.isoformat(), "until": _next_tehran_midnight(now).isoformat(),
                             "status": status, "message": reason, "agent": agent, "job": job}
    try:
        from app.database import async_session_maker
        async with async_session_maker() as s:
            earlier = _parse_pause((await secret_box.get_many(s, (KEY_PAUSE,))).get(KEY_PAUSE), now)
            if earlier and earlier.get("since"):
                state["since"] = earlier["since"]
            await secret_box.put(s, KEY_PAUSE, json.dumps(state, ensure_ascii=False), "ai_gateway")
    except Exception as e:
        logger.warning(f"[ai] the credit pause could not be stored ({type(e).__name__}: {e}) — "
                       f"the next call asks the gateway again")
    err = _pause_error(state)
    if _pause_told.get("since") != state["since"]:
        _pause_told["since"] = state["since"]
        hour = err.until.astimezone(TEHRAN).strftime("%H:%M") if err.until else "?"
        logger.warning(f"[ai] the gateway refused {agent or '?'}/{job} (HTTP {status}): {reason} — every agent waits until "
                       f"{hour} Tehran tomorrow, or until the AI card's test is answered")
    return err


async def _refused(resp, *, agent: str, job: str, model: str, ms: int) -> NoReturn:
    """The gateway answered with something other than a completion: put it on
    the ledger, and raise what the caller needs to know — always raises."""
    status = resp.status_code
    body, reason = _refusal(resp)
    error = f"HTTP {status}: {body}"
    named_wait = _parse_retry_after(resp.headers.get("retry-after")) if status in (429, 503) else None
    await _record(agent, job, model, {}, ms, False, error)      # the ledger keeps as much as its column holds
    if quota_exhausted(status, f"{reason} {body}", retry_after=bool((resp.headers.get("retry-after") or "").strip())):
        # the road is fine and the request was fine: the account is empty. The
        # breaker is for the road, so it is left as the answer found it.
        _breaker.on_success()
        raise await _pause_for_quota(status=status, reason=reason, agent=agent, job=job)
    if named_wait is not None:
        # the gateway itself named a wait — obey it, do not retry
        _breaker.on_failure(retry_after=named_wait)
        raise RateLimited(error, named_wait)
    if status == 429 or status >= 500:
        _breaker.on_failure()
    else:
        # the gateway answered: the request was wrong (a key, a body), not
        # the road — the breaker is for the road
        _breaker.on_success()
    raise LLMError(error)


async def chat(job: str, messages: List[Dict[str, Any]], *, agent: str, db=None,
               schema: Optional[Type] = None, json_mode: bool = False,
               max_tokens: int = 400, temperature: float = 0.2,
               timeout: float = TIMEOUT, cap: bool = True,
               model_override: Optional[str] = None,
               tools: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """One completion for `job`, as `agent`. Returns
    {"content", "data", "model", "usage", "cost_usd", "cost_toman", "ms",
     "message", "tool_calls"} — `data` is the parsed JSON (validated against
    `schema`, a pydantic model, when given); with `tools` (OpenAI function
    specs) the model may answer with `tool_calls` instead of content, and
    `message` is its raw turn to append to the conversation. Raises LLMError
    (or a subclass) for anything the caller cannot use — including CircuitOpen
    when the gateway has failed repeatedly and this call never goes out,
    RateLimited (with `.retry_after`) on a 429/503 that named one, and
    QuotaExhausted (a BudgetExceeded) when the gateway says the account has no
    credit left — or already said so, and every agent is paused until midnight
    or the card's test. `cap=False` skips the daily cap and that pause — for
    the panel's own test, which is how the pause ends. `model_override` is for a
    bake-off only: the same door, the same ledger, another model than the one
    configured for the job."""
    if job not in JOBS:
        raise ValueError(f"unknown job {job!r}")
    from app.database import async_session_maker
    own = db is None
    session = async_session_maker() if own else db
    try:
        cfg = await config(session)
        if cap:
            await _gate(session, cfg, agent=agent)
        elif not configured():
            raise NotConfigured("هوش مصنوعی تنظیم نشده است (LLM_API_KEY / LLM_BASE_URL)")
    finally:
        if own:
            await session.close()
    model = model_override or cfg["models"].get(job) or cfg["models"]["write"]
    if not model:
        raise NotConfigured(f"مدلی برای کار «{job}» تعیین نشده است")

    if job == "write" and cfg["notes"].strip():
        # the office's standing instructions, on every message a person reads
        messages = [{"role": "system", "content": "تذکرات دفتر: " + cfg["notes"].strip()}, *messages]

    body: Dict[str, Any] = {"model": model, "messages": messages,
                            "temperature": temperature, "max_tokens": max_tokens}
    if json_mode or schema is not None:
        body["response_format"] = {"type": "json_object"}
    if tools:
        body["tools"] = tools
    if _reasons(model):
        # A reasoning model thinks inside the same budget it answers from.
        # On Liara, GLM's reasoning cannot be switched off («Reasoning is
        # mandatory for this endpoint»); `thinking: disabled` only shortens
        # it. With a 300-token budget every answer came back empty — the
        # whole budget went to thought — so the floor is high enough for
        # both, and a first empty answer is retried with three times more.
        body["thinking"] = {"type": "disabled"}
        body["max_tokens"] = max(max_tokens, REASONING_MIN_TOKENS)

    if _reasons(model):
        timeout = max(timeout, 90.0)
    last_error = ""
    for attempt in (1, 2):
        if not _breaker.allow():
            raise _circuit_open()
        t0 = time.monotonic()
        usage: Dict[str, Any] = {}
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(_url("chat/completions"), headers=_headers(), json=body)
            ms = int((time.monotonic() - t0) * 1000)
            if resp.status_code != 200:
                await _refused(resp, agent=agent, job=job, model=model, ms=ms)
            _breaker.on_success()
            data = resp.json()
            usage = data.get("usage") or {}
            choice = (data.get("choices") or [{}])[0]
            message = _clean_turn(choice.get("message") or {})
            content = message.get("content") or ""
            tool_calls = message.get("tool_calls") or []
            sent = "".join(str(m.get("content") or "") for m in body["messages"])
            usage = _fill_cost(model, usage, sent, content)
            if not content.strip() and not tool_calls and choice.get("finish_reason") == "length" and attempt == 1:
                # the budget went to thinking and nothing was left for the
                # answer — once more with room for both
                last_error = "empty answer: the token budget went to reasoning"
                await _record(agent, job, model, usage, ms, False, last_error)
                body["max_tokens"] = min(int(body["max_tokens"]) * 3, REASONING_MAX_TOKENS)
                continue
            out: Dict[str, Any] = {"content": content, "data": None, "model": data.get("model") or model,
                                   "usage": usage, "cost_usd": float(usage.get("cost") or 0.0),
                                   "cost_toman": float(usage.get("total_cost_toman") or 0.0), "ms": ms,
                                   "message": message, "tool_calls": tool_calls}
            if (json_mode or schema is not None) and not tool_calls:
                try:
                    parsed = _extract_json(content)
                    if schema is not None:
                        parsed = schema.model_validate(parsed).model_dump()
                    out["data"] = parsed
                except Exception as e:
                    last_error = f"malformed answer: {type(e).__name__}: {str(e)[:120]}"
                    await _record(agent, job, model, usage, ms, False, last_error)
                    if attempt == 1:
                        # once more, with the complaint attached — models fix
                        # their own JSON reliably when told what was wrong
                        body["messages"] = [*messages, {"role": "assistant", "content": content},
                                            {"role": "user", "content": "پاسخ قبلی JSON معتبر نبود. فقط JSON معتبر برگردان، بدون توضیح."}]
                        continue
                    raise LLMError(last_error)
            await _record(agent, job, model, usage, ms, True)
            return out
        except LLMError:
            raise
        except (httpx.HTTPError, OSError, ValueError) as e:
            ms = int((time.monotonic() - t0) * 1000)
            last_error = f"{type(e).__name__}: {str(e)[:160]}"
            await _record(agent, job, model, usage, ms, False, last_error)
            if isinstance(e, (httpx.TransportError, OSError)):
                _breaker.on_failure()
                if attempt == 1:
                    # today's one retry — never for a 429, which is handled
                    # above as a normal (non-transport) response, not here
                    await asyncio.sleep(1.0)
                    continue
            raise LLMError(last_error)
    raise LLMError(last_error or "no answer")


async def embed(texts: List[str], *, agent: str, db=None, timeout: float = TIMEOUT) -> List[List[float]]:
    """Vectors for `texts`, in order. Same gate, same ledger."""
    from app.database import async_session_maker
    own = db is None
    session = async_session_maker() if own else db
    try:
        cfg = await _gate(session, agent=agent)
    finally:
        if own:
            await session.close()
    model = cfg["models"].get("embed")
    if not model:
        raise NotConfigured("مدلی برای Embedding تعیین نشده است")
    if not _breaker.allow():
        raise _circuit_open()
    clean = [mask_pii(t)[:8000] for t in texts]
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(_url("embeddings"), headers=_headers(), json={"model": model, "input": clean})
    except (httpx.HTTPError, OSError) as e:
        _breaker.on_failure()
        await _record(agent, "embed", model, {}, int((time.monotonic() - t0) * 1000), False, f"{type(e).__name__}: {e}")
        raise LLMError(f"{type(e).__name__}: {str(e)[:160]}")
    ms = int((time.monotonic() - t0) * 1000)
    if resp.status_code != 200:
        await _refused(resp, agent=agent, job="embed", model=model, ms=ms)
    _breaker.on_success()
    data = resp.json()
    usage = _fill_cost(model, data.get("usage") or {}, "".join(clean))
    await _record(agent, "embed", model, usage, ms, True)
    rows = sorted(data.get("data") or [], key=lambda d: d.get("index", 0))
    return [r["embedding"] for r in rows]


async def test_connection(db) -> Dict[str, Any]:
    """The panel's «تست»: one tiny request on the write model, cap ignored."""
    out = await chat("write", [{"role": "user", "content": "فقط بنویس: سلام سورین"}],
                     agent="test", db=db, max_tokens=12, cap=False)
    # an answer means the account has credit: whatever paused the agents is over
    cleared = await clear_pause(db)
    return {"ok": True, "model": out["model"], "reply": out["content"].strip()[:40], "ms": out["ms"],
            "cost_usd": out["cost_usd"], "cost_toman": out["cost_toman"], "pause_cleared": cleared}


# ── Liara's own view, when the account token is there ────────────────────────

async def liara_quota() -> Optional[Dict[str, Any]]:
    """Liara's own free-token allowance for this workspace, daily and monthly —
    the quota the office is spending before the plan's paid tokens start."""
    tok = (getattr(settings, "liara_api_token", "") or "").strip()
    ws = workspace_id()
    if not (tok and ws):
        return None
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(f"https://ai.liara.ir/v1/workspaces/{ws}/free-tokens",
                                 headers={"Authorization": f"Bearer {tok}"})
            w = await client.get(f"https://ai.liara.ir/v1/workspaces/{ws}",
                                 headers={"Authorization": f"Bearer {tok}"})
        if r.status_code != 200:
            return None
        body = r.json() or {}
        plan = ((w.json() or {}).get("workspace") or {}).get("plan") if w.status_code == 200 else None
    except Exception as e:
        logger.warning(f"[ai] liara quota unavailable: {type(e).__name__}: {e}")
        return None
    return {"plan": plan, "daily": body.get("daily") or {}, "monthly": body.get("monthly") or {}}


async def liara_activity(days: int = 30) -> Optional[Dict[str, Any]]:
    """Tokens, cost and calls per model as Liara counted them, for the last
    `days` — read with the account token. None when there is no token or
    Liara does not answer; the card then shows our ledger alone."""
    tok = (getattr(settings, "liara_api_token", "") or "").strip()
    ws = workspace_id()
    if not (tok and ws):
        return None
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(f"https://ai.liara.ir/v1/workspaces/{ws}/activity",
                                 headers={"Authorization": f"Bearer {tok}"})
        if r.status_code != 200:
            return None
        days_seen = r.json() if isinstance(r.json(), list) else []
    except Exception as e:
        logger.warning(f"[ai] liara activity unavailable: {type(e).__name__}: {e}")
        return None
    since = (datetime.now(timezone.utc).astimezone(TEHRAN) - timedelta(days=days)).strftime("%Y-%m-%d")
    per_model: Dict[str, Dict[str, float]] = {}
    for day in days_seen:
        if str(day.get("date", "")) < since:
            continue
        for row in day.get("data") or []:
            m = per_model.setdefault(row.get("model") or "?", {"tokens": 0, "cost_toman": 0.0, "calls": 0})
            m["tokens"] += int(row.get("total_tokens") or 0)
            m["cost_toman"] += float(row.get("total_cost_tomans") or 0.0)
            m["calls"] += int(row.get("request_count") or 0)
    return {"days": days,
            "models": [{"model": k, **{kk: (round(v) if kk != "tokens" else int(v)) for kk, v in vals.items()}}
                       for k, vals in sorted(per_model.items(), key=lambda kv: -kv[1]["cost_toman"])],
            "cost_toman": round(sum(v["cost_toman"] for v in per_model.values())),
            "calls": sum(v["calls"] for v in per_model.values())}
