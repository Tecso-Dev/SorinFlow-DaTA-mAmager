"""
The nightly backup, and its copy off the server.

The snapshot has always been taken; what was missing was anywhere for it to
go. Shipping it to Telegram was written and waited on two values that only
existed as environment variables — so it never ran. This is where the two
values are entered from the panel (the token encrypted, like the SMTP
password), where the bot's chat is found instead of read off a curl, and
where a shipment can be fired right now to see it arrive.

super_admin and root only: it is the whole database.
"""
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import _role_dep
from app.auth.permissions import ROLE_ROOT, ROLE_SUPER_ADMIN
from app.config import get_settings
from app.database import get_db
from app.models.user import User
from app.services import audit
from app.services import backup_service as bk
from app.services import secret_box

router = APIRouter()
settings = get_settings()
_super_admin = Depends(_role_dep(ROLE_ROOT, ROLE_SUPER_ADMIN))

_TOKEN_RE = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$")


class BackupSettingsIn(BaseModel):
    bot_token: Optional[str] = Field(None, max_length=120)
    chat_id: Optional[str] = Field(None, max_length=200)     # one id, or several separated by commas
    # api.telegram.org is blocked from Iran; the way out, chosen on the panel
    proxy_mode: Optional[str] = Field(None, pattern="^(manual|pool|relay)$")
    proxy: Optional[str] = Field(None, max_length=300)       # manual: one URL
    proxy_pool: Optional[str] = Field(None, max_length=400)  # pool: "*" or ids
    relay: Optional[str] = Field(None, max_length=300)       # relay: the worker's base URL
    relay_key: Optional[str] = Field(None, max_length=200)   # relay: its shared key, if any


class ProbeIn(BaseModel):
    bot_token: Optional[str] = Field(None, max_length=120)
    proxy_mode: Optional[str] = Field(None, pattern="^(manual|pool|relay)$")
    proxy: Optional[str] = Field(None, max_length=300)
    proxy_pool: Optional[str] = Field(None, max_length=400)
    relay: Optional[str] = Field(None, max_length=300)
    relay_key: Optional[str] = Field(None, max_length=200)


async def _route_for(payload, db) -> dict:
    """The route being typed wins over the saved one, so it can be tried
    before it is saved."""
    mode = payload.proxy_mode
    if mode == "manual" or (mode is None and (payload.proxy or "").strip()):
        typed = (payload.proxy or "").strip()
        if typed:
            if not bk.valid_proxy(typed):
                raise HTTPException(400, "آدرس پراکسی شکل درستی ندارد (مثل socks5://user:pass@host:1080)")
            return bk._route("manual", [typed])
        if mode == "manual":
            return bk._route("manual", [])
    if mode == "relay":
        base = (payload.relay or "").strip()
        if not bk.valid_relay(base):
            raise HTTPException(400, "آدرس رله باید https و بدون مسیر باشد (مثل https://tg.example.com)")
        key = (payload.relay_key or "").strip()
        if not key:
            # The field reads «کلید ذخیره شده — خالی یعنی بدون تغییر». Testing
            # with it empty sent no key at all: the Worker refused, and the
            # panel said the bot token was wrong while backups went through.
            saved = await secret_box.get_many(db, (bk.KEY_RELAY_KEY,))
            if saved.get(bk.KEY_RELAY_KEY):
                key = secret_box.decrypt(saved[bk.KEY_RELAY_KEY]).strip()
        return bk._route("relay", [], base, key)
    if mode == "pool":
        urls = await bk._pool_urls(db, (payload.proxy_pool or "*").strip())
        if not urls:
            raise HTTPException(400, "هیچ پراکسی فعالی در فهرست داشبورد انتخاب نشده است")
        return bk._route("pool", urls)
    return await bk.resolve_route(db)


from app.crm import digest as _digest


@router.get("/status")
async def backup_status(db: AsyncSession = Depends(get_db), _: User = _super_admin):
    cfg = await bk.resolve_telegram(db)
    snaps = bk.local_snapshots()
    route = await bk.resolve_route(db)
    saved = {}
    try:
        saved = await secret_box.get_many(db, (bk.KEY_PROXY_MODE, bk.KEY_PROXY_POOL, bk.KEY_RELAY, bk.KEY_RELAY_KEY, bk.KEY_PROXY))
    except Exception:
        pass
    env_route = bool((settings.telegram_proxy or "").strip() or (getattr(settings, "telegram_api_base", "") or "").strip())
    return {
        "configured": bool(cfg["token"] and bk.chat_ids(cfg["chat_id"])),
        "chat_ids": bk.chat_ids(cfg["chat_id"]),
        "source": cfg["source"],
        "token_masked": secret_box.mask(cfg["token"]),
        "chat_id": cfg["chat_id"],
        # the way out: what is in effect, and what is saved for each mode
        "proxy_mode": route["mode"],
        "route_label": bk.describe_route(route),
        "route_configured": bool(route["proxies"] or route["mode"] == "relay"),
        "proxy_masked": bk.mask_url(secret_box.decrypt(saved[bk.KEY_PROXY])) if saved.get(bk.KEY_PROXY) else "",
        "proxy_pool": (saved.get(bk.KEY_PROXY_POOL) or "*"),
        "relay": saved.get(bk.KEY_RELAY) or "",
        "relay_key_set": bool(saved.get(bk.KEY_RELAY_KEY)),
        "proxy_source": "env" if env_route else ("panel" if (route["proxies"] or route["mode"] == "relay") else None),
        "schedule_fa": "هر شب ۰۳:۳۰ به وقت تهران",
        "keep_local": bk.KEEP_LOCAL,
        "snapshots": snaps[:5],
        "snapshot_count": len(snaps),
        "last_offsite": await bk.last_offsite(db),
        "digest_hour": _digest.HOUR,
        "digest_last_sent": await _digest.last_sent(db),
    }


@router.put("/settings")
async def put_backup_settings(payload: BackupSettingsIn,
                              db: AsyncSession = Depends(get_db),
                              user: User = _super_admin):
    actor = user.username
    if payload.bot_token is not None:
        tok = payload.bot_token.strip()
        if tok:
            if not _TOKEN_RE.match(tok):
                raise HTTPException(400, "توکن ربات شکل درستی ندارد (مثل 123456789:AA…)")
            await secret_box.put(db, bk.KEY_TOKEN, secret_box.encrypt(tok), actor)
            logger.info(f"[backup] telegram token updated by {actor}")
        else:
            # An empty string is «forget it», the same as the SMTP password.
            await secret_box.put(db, bk.KEY_TOKEN, None, actor)
            logger.info(f"[backup] telegram token cleared by {actor}")
    if payload.chat_id is not None:
        raw = payload.chat_id.strip()
        ids = bk.chat_ids(raw)
        if raw and (not ids or len(ids) != len([p for p in re.split(r"[\s,،;]+", raw) if p])):
            raise HTTPException(400, "شناسهٔ چت باید عدد باشد (چت‌های گروهی با منفی شروع می‌شوند)؛ چند شناسه را با ویرگول جدا کنید")
        await secret_box.put(db, bk.KEY_CHAT, ", ".join(ids) or None, actor)
    if payload.proxy is not None:
        proxy = payload.proxy.strip()
        if proxy and not bk.valid_proxy(proxy):
            raise HTTPException(400, "آدرس پراکسی شکل درستی ندارد (مثل socks5://user:pass@host:1080)")
        # encrypted like the token: the URL usually carries the proxy's password
        await secret_box.put(db, bk.KEY_PROXY, secret_box.encrypt(proxy) if proxy else None, actor)
        logger.info(f"[backup] telegram proxy {'updated' if proxy else 'cleared'} by {actor}")
    if payload.proxy_pool is not None:
        spec = payload.proxy_pool.strip()
        if spec and spec != "*" and not re.fullmatch(r"[\d\s,،;]+", spec):
            raise HTTPException(400, "فهرست پراکسی‌ها باید شناسه‌های عددی باشد یا *")
        await secret_box.put(db, bk.KEY_PROXY_POOL, spec or None, actor)
    if payload.relay is not None:
        base = payload.relay.strip().rstrip("/")
        if base and not bk.valid_relay(base):
            raise HTTPException(400, "آدرس رله باید https و بدون مسیر باشد (مثل https://tg.example.com)")
        await secret_box.put(db, bk.KEY_RELAY, base or None, actor)
    if payload.relay_key is not None:
        key = payload.relay_key.strip()
        await secret_box.put(db, bk.KEY_RELAY_KEY, secret_box.encrypt(key) if key else None, actor)
    if payload.proxy_mode is not None:
        await secret_box.put(db, bk.KEY_PROXY_MODE, payload.proxy_mode, actor)
        logger.info(f"[backup] telegram route set to {payload.proxy_mode} by {actor}")
    return await backup_status(db, user)


@router.post("/proxy-test")
async def proxy_test(payload: ProbeIn, db: AsyncSession = Depends(get_db),
                     _: User = _super_admin):
    """getMe through the proxy — the bot's name and the round trip, or why not."""
    tok = (payload.bot_token or "").strip() or (await bk.resolve_telegram(db))["token"]
    if not tok:
        raise HTTPException(400, "ابتدا توکن ربات را وارد کنید")
    route = await _route_for(payload, db)
    # the pool is tested one proxy at a time: the point is to see which ones work
    if route["mode"] == "pool":
        results = []
        for url in route["proxies"]:
            one = bk._route("manual", [url])
            try:
                r = await bk.telegram_ping(tok, route=one)
                results.append({"proxy": bk.mask_url(url), "ok": True, "ms": r["ms"], "bot": r["bot"]})
            except Exception as e:
                results.append({"proxy": bk.mask_url(url), "ok": False, "error": type(e).__name__ if not isinstance(e, ValueError) else str(e)})
        ok = [r for r in results if r["ok"]]
        if not ok:
            raise HTTPException(503, "هیچ‌کدام از پراکسی‌های انتخاب‌شده به تلگرام نرسید")
        return {"bot": ok[0]["bot"], "ms": ok[0]["ms"], "proxy": bk.describe_route(route), "results": results}
    try:
        info = await bk.telegram_ping(tok, route=route)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        blocked = not (route["proxies"] or route["mode"] == "relay")
        raise HTTPException(503, ("بدون پراکسی به تلگرام نرسید — از این سرور تلگرام مسدود است" if blocked else
                                  f"از این راه به تلگرام نرسید ({bk.describe_route(route)})") + f" ({e if isinstance(e, bk.RelayError) else type(e).__name__})")
    info["proxy"] = bk.describe_route(route)
    return info


@router.post("/probe")
async def probe_bot(payload: ProbeIn, db: AsyncSession = Depends(get_db),
                    _: User = _super_admin):
    """Who the bot is and which chats have written to it. Takes the token
    being typed, or the saved one, so the chat can be picked before saving."""
    tok = (payload.bot_token or "").strip() or (await bk.resolve_telegram(db))["token"]
    if not tok:
        raise HTTPException(400, "ابتدا توکن ربات را وارد کنید")
    route = await _route_for(payload, db)
    try:
        info = await bk.telegram_probe(tok, route=route)
        # the assistant's long poll consumes updates before this can read
        # them; the chats it saw are remembered for exactly this list
        from app.ai import assistant as _assistant
        have = {c["id"] for c in info["chats"]}
        info["chats"] += [c for c in await _assistant.seen_chats(db) if c["id"] not in have]
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        blocked = not (route["proxies"] or route["mode"] == "relay")
        raise HTTPException(503, ("بدون پراکسی به تلگرام نرسید — از این سرور تلگرام مسدود است؛ پراکسی را تنظیم کنید" if blocked else
                                  f"از این راه به تلگرام نرسید ({bk.describe_route(route)})") + f" ({e if isinstance(e, bk.RelayError) else type(e).__name__})")
    if not info["chats"]:
        info["hint_fa"] = ("هنوز هیچ چتی به ربات پیام نداده. در تلگرام ربات را باز کنید، "
                           "Start را بزنید و یک پیام بفرستید، بعد دوباره «پیدا کن» را بزنید.")
    return info


@router.get("/digest")
async def digest_preview(db: AsyncSession = Depends(get_db), _: User = _super_admin):
    """Today's message as it would go out now, and when the last one went."""
    from app.crm import digest
    built = await digest.build(db)
    cfg = await bk.resolve_telegram(db)
    return {"text": built["text"], "counts": built["counts"], "hour": digest.HOUR,
            "last_sent": await digest.last_sent(db),
            "configured": bool(cfg["token"] and bk.chat_ids(cfg["chat_id"]))}


@router.post("/digest/send")
async def digest_send_now(db: AsyncSession = Depends(get_db), user: User = _super_admin):
    """An extra one, right now — the daily one still goes at its hour."""
    from app.crm import digest
    res = await digest.send(db, record=False)
    logger.info(f"[digest] manual send by {user.username}: delivered={res.get('delivered')} error={res.get('error')!r}")
    if not res["ok"]:
        raise HTTPException(status_code=502, detail=res.get("error") or "ارسال نشد")
    return res


@router.post("/run")
async def run_backup_now(db: AsyncSession = Depends(get_db),
                         user: User = _super_admin, request: Request = None):
    """A snapshot right now, shipped if Telegram is configured."""
    res = await bk.run_backup(db)
    logger.info(f"[backup] manual run by {user.username}: {res}")
    await audit.record("backup_run", actor=user, summary=f"بکاپ دستی توسط «{user.username}»",
                       detail={"file": res.get("file"), "telegram_sent": res.get("telegram_sent")},
                       request=request)
    res["last_offsite"] = await bk.last_offsite(db)
    return res


@router.post("/diagnose")
async def diagnose_routes(db: AsyncSession = Depends(get_db), _: User = _super_admin):
    """«تست همهٔ راه‌ها»: getMe straight to Telegram, through the relay, and
    through each proxy, one at a time — which of them works today, and how
    fast. Uses the saved token."""
    tok = (await bk.resolve_telegram(db))["token"]
    if not tok:
        raise HTTPException(400, "ابتدا توکن ربات را ذخیره کنید")
    return {"rows": await bk.diagnose(tok, await bk.every_way_out(db))}


@router.get("/dr")
async def dr_status(_: User = _super_admin):
    """The full disaster-recovery bundle, built on the host every night at
    04:00 Tehran (scripts/dr_backup.sh): the last run, the last failure, and
    whether a «همین حالا» request is waiting for the host to pick it up."""
    from app.services import dr_backup as dr
    st = dr.read_status()
    return {
        "last_run": st.get("last_run"),
        "history": (st.get("history") or [])[:5],
        "last_alert": st.get("last_alert"),
        "requested": dr.REQUEST.exists(),
        "undelivered": sorted(d.name for d in dr.OUTBOX.glob("*") if d.is_dir()),
        "schedule_fa": "هر شب ۰۴:۰۰ به وقت تهران",
    }


@router.post("/dr/run")
async def dr_run_now(user: User = _super_admin, request: Request = None):
    """«همین حالا»: drop the request file the host's dr-backup.path unit
    watches. The host builds and ships the bundle; this only asks."""
    from app.services import dr_backup as dr
    if dr.REQUEST.exists():
        raise HTTPException(409, "درخواست قبلی هنوز منتظر سرور است — چند دقیقه بعد وضعیت را ببینید")
    if not dr.request_run():
        raise HTTPException(503, "درخواست ثبت نشد — فضای داده در دسترس نیست")
    logger.info(f"[dr] full backup requested by {user.username}")
    await audit.record("dr_run", actor=user,
                       summary=f"اجرای بکاپ کامل (DR) توسط «{user.username}» درخواست شد",
                       request=request)
    return {"requested": True}


_RELAY_WORKER = Path(__file__).resolve().parents[3] / "deploy" / "telegram-relay" / "worker.js"


@router.get("/relay-worker", response_class=PlainTextResponse)
async def relay_worker_code(_: User = _super_admin):
    """The Worker the panel tells people to paste into Cloudflare — read from
    deploy/telegram-relay/worker.js, so the panel can never show a copy that
    has drifted from the one that is tested."""
    try:
        return _RELAY_WORKER.read_text(encoding="utf-8")
    except OSError:
        raise HTTPException(404, "فایل Worker روی سرور پیدا نشد")
