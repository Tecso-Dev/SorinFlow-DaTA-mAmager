"""
Shared fakes for the tests of the AI gateway's «no credit» pause (#35):
test_ai_quota_pause.py (the door), test_ai_quota_agents.py (the agents that
must stop asking) and test_ai_quota_card.py (what the panel is told).

Not in conftest.py on purpose, like _fake_redis.py: a bare `from _ai_quota_kit
import …` works from any test file, because pytest's default import mode puts
each test file's own directory on sys.path.

No network anywhere: the gateway is an httpx.MockTransport, and the settings,
the ledger and the listings are a sqlite file of each test's own — the door
opens sessions of its own for the ledger and for the pause, so the session
maker it uses is pointed at that file. Nothing about the settings table or the
ledger is faked.
"""
import contextlib
import json
import os
import sys
from datetime import datetime, timezone

import httpx
import pytest
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_ai_quota_kit.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from sqlalchemy import select                                                # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool                                         # noqa: E402

import app.models                                                            # noqa: E402,F401  every table on Base.metadata
from app.database import Base                                                # noqa: E402
from app.models.ai_usage import AiUsage                                      # noqa: E402
from app.models.app_setting import AppSetting                                # noqa: E402
from app.models.property import Category, City, Property                     # noqa: E402
from app.services import llm, secret_box                                     # noqa: E402

_REAL_CLIENT = httpx.AsyncClient
UTC = timezone.utc

GATEWAY_WORDS = "Workspace has insufficient credit"
OUT_OF_CREDIT = {"error": {"message": GATEWAY_WORDS, "type": "insufficient_credit", "code": "payment_required"}}
GOOD_FACTS = {"kind": "shop", "floor": 1, "summary": "مغازه", "confidence": {"kind": 1}}


# ── the fakes ────────────────────────────────────────────────────────────────

def _gateway(monkeypatch, respond):
    """The gateway is `respond(request) -> httpx.Response`. Returns the list
    of requests it was asked, which is what «nothing goes out» is measured on."""
    requests = []

    def handler(req):
        requests.append(req)
        return respond(req)

    class Fake(_REAL_CLIENT):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **kw)
    monkeypatch.setattr(llm.httpx, "AsyncClient", Fake)
    return requests


def _refuse(status, body="", headers=None):
    if isinstance(body, (dict, list)):
        return httpx.Response(status, json=body, headers=headers)
    return httpx.Response(status, content=body.encode("utf-8") if isinstance(body, str) else body, headers=headers)


def _answer(content, cost=0.001):
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False)
    return httpx.Response(200, json={
        "model": "test/model", "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": cost, "total_cost_toman": 300}})


def _configure(monkeypatch, key="k-test"):
    monkeypatch.setattr(llm.settings, "llm_api_key", key, raising=False)
    monkeypatch.setattr(llm.settings, "llm_base_url", "https://ai.liara.ir/api/6aa50e58b5e9e82406b93188/v1", raising=False)
    monkeypatch.setattr(llm.settings, "llm_model", "openai/gpt-4.1-mini", raising=False)
    monkeypatch.setattr(llm.settings, "llm_model_read", "z-ai/glm-5.3-flash", raising=False)
    monkeypatch.setattr(llm.settings, "llm_model_vision", "z-ai/glm-5.3-flash", raising=False)
    monkeypatch.setattr(llm.settings, "llm_model_embed", "openai/text-embedding-3-small", raising=False)


def _freeze(monkeypatch, iso):
    """The clock llm.py reads, stopped at `iso` (UTC)."""
    fixed = datetime.fromisoformat(iso).replace(tzinfo=UTC)

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)
    monkeypatch.setattr(llm, "datetime", Frozen)


def _forget_what_was_told():
    """A second pod's memory is empty: nothing of what this one already
    logged, and nothing of what it saw."""
    for name in ("_pause_told", "_pause_noted"):
        getattr(llm, name, {}).clear()


# ── what each test module's three fixtures are made of ───────────────────────
#
# (Fixtures imported from here would be «redefined» by every test that takes
# them as a parameter, so each module declares its own three, one line of body
# each, and this is what they call.)

def reset_process():
    """What a process holds in memory — the breaker, and what it already told
    the log — must not carry one test's story into the next."""
    llm._breaker = llm._Breaker()
    _forget_what_was_told()


@contextlib.contextmanager
def capture_logs():
    lines = []
    hid = logger.add(lambda m: lines.append(f"{m.record['level'].name} {m.record['message']}"), level="DEBUG")
    try:
        yield lines
    finally:
        logger.remove(hid)


@contextlib.asynccontextmanager
async def open_world(monkeypatch, tmp_path):
    """Real settings, ledger and listings in a sqlite file of this test's own,
    and the session maker the door opens its own sessions with pointed at it."""
    tables = [City.__table__, Category.__table__, Property.__table__, AppSetting.__table__, AiUsage.__table__]
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'quota.db'}", poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("app.database.async_session_maker", maker)
    _configure(monkeypatch)
    try:
        yield maker
    finally:
        await engine.dispose()


# ── helpers ──────────────────────────────────────────────────────────────────

async def _setting(maker, key):
    async with maker() as db:
        return (await secret_box.get_many(db, (key,))).get(key)


async def _ledger(maker):
    async with maker() as db:
        return (await db.execute(select(AiUsage).order_by(AiUsage.id))).scalars().all()


async def _chat(agent="vision", job="vision", **kw):
    return await llm.chat(job, [{"role": "user", "content": "x"}], agent=agent, **kw)


async def _pause_now(monkeypatch, message=GATEWAY_WORDS):
    """The gateway refuses once, for credit, and the whole office is paused."""
    _gateway(monkeypatch, lambda r: _refuse(402, {"error": {"message": message}}))
    with pytest.raises(llm.QuotaExhausted):
        await _chat("test", "write", cap=False)


async def _seed_photo_listings(maker, root, n):
    """n active listings whose gallery is on disk, the way the scraper leaves it."""
    from PIL import Image
    async with maker() as db:
        props = []
        for i in range(n):
            did = f"d{i:04d}"
            (root / did).mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (80, 60), (200, 120, 80)).save(root / did / "0.jpg", "JPEG")
            props.append(Property(tag_number=f"T{i}", divar_id=did, title=f"آپارتمان {i}", url=f"https://divar.ir/v/{did}",
                                  images=[f"/images/{did}/0.jpg"], has_images=True, images_downloaded=True,
                                  is_active=True, serial_no=1000 + i))
        db.add_all(props)
        await db.commit()
        return [p.id for p in props]


async def _seed_text_listings(maker, n):
    async with maker() as db:
        props = [Property(tag_number=f"T{i}", divar_id=f"D{i}", url=f"https://divar.ir/v/{i}", title=f"آپارتمان {i}",
                          description="", serial_no=1000 + i, is_active=True) for i in range(n)]
        db.add_all(props)
        await db.commit()
        return [p.id for p in props]
