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
from typing import Any, Dict, List, Optional, Type

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
DEFAULT_CAP_USD = 2.0
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
    rows = {}
    try:
        rows = await secret_box.get_many(db, (*KEY_MODELS.values(), KEY_CAP, KEY_NOTES, KEY_ENABLED))
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
    enabled = (rows.get(KEY_ENABLED) or "true").lower() != "false"
    return {
        "configured": configured(),
        "enabled": enabled,
        "workspace": workspace_id(),
        "models": models,
        "model_sources": sources,
        "cap_usd": cap,
        "notes": rows.get(KEY_NOTES) or "",
    }


# ── privacy ──────────────────────────────────────────────────────────────────

_PHONE = re.compile(r"(?<!\d)(?:\+?98|0)?9\d{9}(?!\d)")
_PHONE_FA = re.compile(r"(?<![۰-۹])(?:\+?۹۸|۰)?۹[۰-۹]{9}(?![۰-۹])")
_LANDLINE = re.compile(r"(?<!\d)0\d{2,3}[-\s]?\d{7,8}(?!\d)")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def mask_pii(text: Optional[str]) -> str:
    """Phone numbers and e-mail addresses out, before text leaves the office.
    A listing's text is the owner's; a need's text is the customer's. Neither
    number is the model's business, and the model does not need them to do
    its job."""
    if not text:
        return ""
    t = _PHONE.sub("۰۹×××××××××", str(text))
    t = _PHONE_FA.sub("۰۹×××××××××", t)
    t = _LANDLINE.sub("۰××××××××", t)
    return _EMAIL.sub("ایمیل", t)


# ── the ledger and the cap ───────────────────────────────────────────────────

def _day_start_utc(now: Optional[datetime] = None) -> datetime:
    local = (now or datetime.now(timezone.utc)).astimezone(TEHRAN)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


async def spent_today(db) -> float:
    from app.models.ai_usage import AiUsage
    return float((await db.execute(
        select(func.coalesce(func.sum(AiUsage.cost_usd), 0.0))
        .where(AiUsage.created_at >= _day_start_utc()))).scalar_one() or 0.0)


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


async def _gate(db, cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Configured, switched on, and under the cap — or the reason it is not."""
    if not configured():
        raise NotConfigured("هوش مصنوعی تنظیم نشده است (LLM_API_KEY / LLM_BASE_URL)")
    cfg = cfg or await config(db)
    if not cfg["enabled"]:
        raise Disabled("هوش مصنوعی از پنل خاموش است")
    spent = await spent_today(db)
    if spent >= cfg["cap_usd"]:
        raise BudgetExceeded(f"سقف روزانه پر شد ({spent:.2f} از {cfg['cap_usd']:.2f} دلار)")
    return cfg


def _extract_json(content: str) -> Any:
    """The model's JSON, even when it wrapped it in a code fence."""
    text = (content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S).strip()
    return json.loads(text)


async def chat(job: str, messages: List[Dict[str, Any]], *, agent: str, db=None,
               schema: Optional[Type] = None, json_mode: bool = False,
               max_tokens: int = 400, temperature: float = 0.2,
               timeout: float = TIMEOUT, cap: bool = True,
               model_override: Optional[str] = None) -> Dict[str, Any]:
    """One completion for `job`, as `agent`. Returns
    {"content", "data", "model", "usage", "cost_usd", "cost_toman", "ms"} —
    `data` is the parsed JSON (validated against `schema`, a pydantic model,
    when given). Raises LLMError (or a subclass) for anything the caller
    cannot use. `cap=False` skips the daily cap — for the panel's own test.
    `model_override` is for a bake-off only: the same door, the same ledger,
    another model than the one configured for the job."""
    if job not in JOBS:
        raise ValueError(f"unknown job {job!r}")
    from app.database import async_session_maker
    own = db is None
    session = async_session_maker() if own else db
    try:
        cfg = await config(session)
        if cap:
            await _gate(session, cfg)
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
        t0 = time.monotonic()
        usage: Dict[str, Any] = {}
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(_url("chat/completions"), headers=_headers(), json=body)
            ms = int((time.monotonic() - t0) * 1000)
            if resp.status_code != 200:
                last_error = f"HTTP {resp.status_code}: {resp.text[:160]}"
                await _record(agent, job, model, {}, ms, False, last_error)
                raise LLMError(last_error)
            data = resp.json()
            usage = data.get("usage") or {}
            choice = (data.get("choices") or [{}])[0]
            content = (choice.get("message") or {}).get("content") or ""
            if not content.strip() and choice.get("finish_reason") == "length" and attempt == 1:
                # the budget went to thinking and nothing was left for the
                # answer — once more with room for both
                last_error = "empty answer: the token budget went to reasoning"
                await _record(agent, job, model, usage, ms, False, last_error)
                body["max_tokens"] = min(int(body["max_tokens"]) * 3, REASONING_MAX_TOKENS)
                continue
            out: Dict[str, Any] = {"content": content, "data": None, "model": data.get("model") or model,
                                   "usage": usage, "cost_usd": float(usage.get("cost") or 0.0),
                                   "cost_toman": float(usage.get("total_cost_toman") or 0.0), "ms": ms}
            if json_mode or schema is not None:
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
            if attempt == 1 and isinstance(e, (httpx.TransportError, OSError)):
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
        cfg = await _gate(session)
    finally:
        if own:
            await session.close()
    model = cfg["models"].get("embed")
    if not model:
        raise NotConfigured("مدلی برای Embedding تعیین نشده است")
    clean = [mask_pii(t)[:8000] for t in texts]
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(_url("embeddings"), headers=_headers(), json={"model": model, "input": clean})
    except (httpx.HTTPError, OSError) as e:
        await _record(agent, "embed", model, {}, int((time.monotonic() - t0) * 1000), False, f"{type(e).__name__}: {e}")
        raise LLMError(f"{type(e).__name__}: {str(e)[:160]}")
    ms = int((time.monotonic() - t0) * 1000)
    if resp.status_code != 200:
        err = f"HTTP {resp.status_code}: {resp.text[:160]}"
        await _record(agent, "embed", model, {}, ms, False, err)
        raise LLMError(err)
    data = resp.json()
    await _record(agent, "embed", model, data.get("usage") or {}, ms, True)
    rows = sorted(data.get("data") or [], key=lambda d: d.get("index", 0))
    return [r["embedding"] for r in rows]


async def test_connection(db) -> Dict[str, Any]:
    """The panel's «تست»: one tiny request on the write model, cap ignored."""
    out = await chat("write", [{"role": "user", "content": "فقط بنویس: سلام سورین"}],
                     agent="test", db=db, max_tokens=12, cap=False)
    return {"ok": True, "model": out["model"], "reply": out["content"].strip()[:40], "ms": out["ms"],
            "cost_usd": out["cost_usd"], "cost_toman": out["cost_toman"]}


# ── Liara's own view, when the account token is there ────────────────────────

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
