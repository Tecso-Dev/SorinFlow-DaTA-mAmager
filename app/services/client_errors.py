"""
Errors that happen in the user's browser, reported home.

«It errors on some phones» is not something anyone can fix from a chair:
the phone is in somebody else's pocket, the console is not open, and the
error may be that app.js does not even parse on that browser. A tiny ES5
hook in index.html catches window.onerror and sends it here; this keeps the
last few dozen in Redis and the monitoring page shows them with the browser
that had them.

Public endpoint, so it is rate-limited per address and stores nothing it
was not given a shape for. Never raises into the request: a reporter that
500s is a second error.
"""
import json
from datetime import datetime, timezone

from loguru import logger

from app.database import get_redis

KEY = "sorinflow:client_errors"
KEEP = 60
PER_MINUTE = 20
FIELDS = ("kind", "message", "source", "line", "col", "stack", "url", "ua", "screen", "at")
LIMITS = {"message": 500, "source": 200, "stack": 1500, "url": 300, "ua": 300, "screen": 20, "kind": 20, "at": 40}


def shape(raw: dict) -> dict:
    """Only the fields the hook sends, each cut to size."""
    out = {}
    for k in FIELDS:
        v = raw.get(k)
        if v is None:
            continue
        if k in ("line", "col"):
            try:
                out[k] = int(v)
            except (TypeError, ValueError):
                out[k] = 0
        else:
            out[k] = str(v)[:LIMITS.get(k, 200)]
    out["received_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return out


def browser_of(ua: str) -> str:
    """A short name a person recognises, from the user-agent string."""
    ua = ua or ""
    os_ = "iPhone" if "iPhone" in ua else "iPad" if "iPad" in ua else \
        "Android" if "Android" in ua else "Windows" if "Windows" in ua else \
        "Mac" if "Macintosh" in ua else "Linux" if "Linux" in ua else "?"
    if "SamsungBrowser" in ua:
        br = "Samsung"
    elif "Firefox" in ua:
        br = "Firefox"
    elif "Edg/" in ua:
        br = "Edge"
    elif "OPR/" in ua or "Opera" in ua:
        br = "Opera"
    elif "CriOS" in ua or "Chrome" in ua:
        br = "Chrome"
    elif "Safari" in ua:
        br = "Safari"
    elif "; wv)" in ua:
        br = "WebView"
    else:
        br = "?"
    import re
    m = re.search(r"(?:Chrome|CriOS|Firefox|Version|SamsungBrowser|Edg|OPR)/(\d+)", ua)
    ver = m.group(1) if m else ""
    return f"{br} {ver} · {os_}".strip()


async def record(raw: dict, ip: str) -> bool:
    """Store one report. False when this address is over its budget or Redis
    is unavailable — either way the request still answers 204."""
    try:
        r = await get_redis()
        bucket = f"{KEY}:budget:{ip}"
        n = await r.incr(bucket)
        if n == 1:
            await r.expire(bucket, 60)
        if n > PER_MINUTE:
            return False
        item = shape(raw)
        item["ip"] = ip
        pipe = r.pipeline()
        pipe.lpush(KEY, json.dumps(item, ensure_ascii=False))
        pipe.ltrim(KEY, 0, KEEP - 1)
        await pipe.execute()
        logger.warning(f"[client-error] {browser_of(item.get('ua', ''))}: "
                       f"{item.get('message', '')[:160]} @ {item.get('source', '')}:{item.get('line', 0)}")
        return True
    except Exception as e:
        logger.warning(f"[client-error] could not record: {e}")
        return False


async def recent(limit: int = 30) -> list:
    try:
        r = await get_redis()
        rows = await r.lrange(KEY, 0, max(0, limit - 1))
    except Exception:
        return []
    out = []
    for raw in rows:
        try:
            d = json.loads(raw)
        except Exception:
            continue
        d["browser"] = browser_of(d.get("ua", ""))
        out.append(d)
    return out


async def clear() -> None:
    try:
        r = await get_redis()
        await r.delete(KEY)
    except Exception:
        pass
