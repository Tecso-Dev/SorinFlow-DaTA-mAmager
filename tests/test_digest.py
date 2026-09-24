"""
خلاصهٔ صبحگاهی — one Telegram message a day, at eight, to the chat the backup
and the matches already go to: what came in overnight, what fits whom, what
got cheaper, how many calls wait, whether the backup arrived.
"""
import os
import sys
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_digest.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

ROOT = Path(__file__).resolve().parent.parent
JS = (ROOT / "frontend/js/app.js").read_text(encoding="utf-8")
HTML = (ROOT / "frontend/index.html").read_text(encoding="utf-8")


class TestTheShape:

    def test_it_runs_off_the_request_path_and_can_be_switched_off(self):
        main = (ROOT / "app/main.py").read_text(encoding="utf-8")
        assert "digest_task = asyncio.create_task(_digest_loop())" in main and "digest_task.cancel()" in main
        src = (ROOT / "app/crm/digest.py").read_text(encoding="utf-8")
        assert 'getattr(settings, "match_engine", True)' in src and "HOUR < 0" in src
        cfg = (ROOT / "app/config.py").read_text(encoding="utf-8")
        # env="DIGEST_HOUR" was pydantic v1 syntax that pydantic-settings 2.x
        # silently ignores; DIGEST_HOUR now reaches the field via AliasChoices.
        assert 'digest_hour: int = Field(default=8, validation_alias=AliasChoices("DIGEST_HOUR", "digest_hour"))' in cfg

    def test_no_system_tzdata_is_needed(self):
        src = (ROOT / "app/crm/digest.py").read_text(encoding="utf-8")
        assert "ZoneInfo" not in src and "timedelta(hours=3, minutes=30)" in src

    def test_it_goes_the_way_the_backup_goes(self):
        src = (ROOT / "app/crm/digest.py").read_text(encoding="utf-8")
        fn = src[src.index("async def send"):src.index("async def tick")]
        assert "resolve_telegram(db)" in fn and "resolve_route(db)" in fn
        assert 'tg_request(cfg["token"], "sendMessage", route' in fn
        assert "except Exception" in fn, "one refused chat must not stop the others"

    def test_the_panel_shows_it_on_the_backup_card(self):
        assert 'id="bk-digest"' in HTML and 'onclick="bkDigest()"' in HTML
        fn = JS[JS.index("async function bkDigest"):JS.index("async function loadBackup") if JS.index("async function loadBackup") > JS.index("async function bkDigest") else JS.index("// ── the way out to Telegram")]
        assert "apiCall('/backup/digest')" in fn and "apiCall('/backup/digest/send', { method: 'POST' })" in fn
        assert "askConfirm(" in fn
        for bad in ("prompt(", "confirm(", "alert("):
            assert bad not in fn.replace("askConfirm(", "")


class TestTheClock:

    def test_early_means_wait_and_after_the_hour_means_once(self, monkeypatch):
        from app.crm import digest
        monkeypatch.setattr(digest, "HOUR", 8)
        # 07:30 Tehran = 04:00 UTC
        early = datetime(2026, 9, 22, 4, 0, tzinfo=timezone.utc)
        assert asyncio.run(digest.tick(now=early)) == {"skipped": "early"}
        # 08:30 Tehran, already sent today
        later = datetime(2026, 9, 22, 5, 0, tzinfo=timezone.utc)

        async def sent_today(_db):
            return "2026-09-22"
        monkeypatch.setattr(digest, "last_sent", sent_today)
        assert asyncio.run(digest.tick(now=later)) == {"skipped": "sent"}

    def test_the_backup_line(self):
        from app.crm import digest
        since = datetime.now(timezone.utc) - timedelta(hours=24)
        fresh = (datetime.now() - timedelta(hours=5)).isoformat(timespec="seconds")
        stale = (datetime.now() - timedelta(days=3)).isoformat(timespec="seconds")
        assert digest._backup_line({}, since) == "هنوز فرستاده نشده"
        assert digest._backup_line({"at": fresh, "ok": True, "size_kb": 2048, "delivered": ["1", "2"]}, since) == "رسید (2 MB · 2 چت)"
        assert digest._backup_line({"at": fresh, "ok": True, "size_kb": 300, "delivered": ["1"], "error": "chat not found"}, since) \
            == "رسید (300 KB · 1 چت) — بخشی نرسید: chat not found"
        assert digest._backup_line({"at": fresh, "ok": False, "error": "proxy down"}, since) == "نرسید: proxy down"
        assert digest._backup_line({"at": stale, "ok": True}, since).startswith("در ۲۴ ساعت گذشته فرستاده نشد")
        # what backup_service writes now: with its UTC offset — a naive/aware
        # comparison here would have raised and taken the whole digest down
        fresh_utc = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat(timespec="seconds")
        stale_utc = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat(timespec="seconds")
        assert digest._backup_line({"at": fresh_utc, "ok": True, "size_kb": 10, "delivered": ["1"]}, since) == "رسید (10 KB · 1 چت)"
        assert digest._backup_line({"at": stale_utc, "ok": True}, since).startswith("در ۲۴ ساعت گذشته فرستاده نشد")


# ── through the real app (Postgres) ──────────────────────────────────────────

@pytest.fixture(scope="module")
def client():
    import fakeredis.aioredis
    import app.database as db
    from app.config import get_settings
    if not str(db.engine.url).startswith("postgresql"):
        pytest.skip("needs Postgres — see test_auth_roles.py", allow_module_level=True)
    cfg = get_settings()
    saved = (cfg.environment, cfg.api_key, cfg.cookies_path, cfg.scrape_scheduler, cfg.match_engine,
             cfg.telegram_bot_token, cfg.telegram_chat_id)
    cfg.environment, cfg.api_key = "test", ""
    cfg.cookies_path = "/tmp/sorinflow-test-cookies"
    cfg.scrape_scheduler = False
    cfg.match_engine = False
    cfg.telegram_bot_token, cfg.telegram_chat_id = "", ""
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)

    async def _get_redis():
        return fake
    db.get_redis = _get_redis
    import app.services.verification as v
    v.get_redis = _get_redis
    from fastapi.testclient import TestClient
    import app.main as m
    with TestClient(m.app) as c:
        yield c
    (cfg.environment, cfg.api_key, cfg.cookies_path, cfg.scrape_scheduler, cfg.match_engine,
     cfg.telegram_bot_token, cfg.telegram_chat_id) = saved


def _seed():
    """What a night leaves behind: two listings (one rental), a finished scrape and
    a failed one, a match, a price cut on a listing that got cheaper, a lead due
    for a call and one booked for later today."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.models.user import User
    from app.models.property import Property
    from app.models.lead import Lead
    from app.models.scraping_job import ScrapingJob
    from app.models.crm_models import Customer, CustomerMatch, PriceAlert
    from app.auth.jwt import get_password_hash
    from app.crm.digest import TEHRAN

    async def _go():
        eng = create_async_engine(os.environ["DATABASE_URL"])
        maker = async_sessionmaker(eng, expire_on_commit=False)
        now = datetime.now(timezone.utc)
        try:
            async with maker() as s:
                s.add(User(username="dg_boss", full_name="مدیر خلاصه", role="super_admin", permissions=["crm"],
                           hashed_password=get_password_hash("pw123456"), is_active=True))
                p1 = Property(title="آپارتمان ۹۵ متری خیابان کاشانی", tag_number="dg-1", divar_id="dg-1", url="https://divar.ir/v/dg-1",
                              city_name="ارومیه", district="خیابان کاشانی", area=95, rooms=2, property_type="آپارتمان",
                              listing_type="buy", total_price=4_000_000_000, is_active=True, phone_number="09141112233")
                p2 = Property(title="سوئیت اجاره‌ای دانشکده", tag_number="dg-2", divar_id="dg-2", url="https://divar.ir/v/dg-2",
                              city_name="ارومیه", district="دانشکده", area=60, rooms=1, property_type="آپارتمان",
                              listing_type="rent", deposit=200_000_000, rent_price=8_000_000, is_active=True, phone_number="09141112244")
                c = Customer(full_name="مشتری خلاصه", mobile1="09121119999", consultant_name="مدیر خلاصه",
                             desired_city="ارومیه", desired_district="خیابان کاشانی", desired_type="apartment",
                             deal_type="buy", budget_max=5_000_000_000)
                s.add_all([p1, p2, c])
                s.add(ScrapingJob(status="completed", started_at=now - timedelta(hours=6), completed_at=now - timedelta(hours=5), new_items=2))
                s.add(ScrapingJob(status="failed", started_at=now - timedelta(hours=3), completed_at=now - timedelta(hours=3), error_message="cookie"))
                await s.flush()
                s.add(CustomerMatch(property_id=p1.id, customer_id=c.id, score=77, reasons=["داخل بودجه"], consultant="مدیر خلاصه", status="new"))
                s.add(PriceAlert(property_id=p1.id, listing_type="buy", from_amount=4_500_000_000, to_amount=4_000_000_000,
                                 delta_pct=-11, moved_at=now - timedelta(hours=2), status="new"))
                s.add(Lead(property_id=p1.id, phone_number="09141112233", status="new", next_call_at=None))
                later = now.astimezone(TEHRAN).replace(hour=23, minute=0, second=0, microsecond=0)
                if later <= now.astimezone(TEHRAN):
                    later = later + timedelta(minutes=30)  # a test run after 23:00 still books it 'today'
                s.add(Lead(property_id=p2.id, phone_number="09141112244", status="contacted", next_call_at=later))
                await s.commit()
        finally:
            await eng.dispose()
    asyncio.run(_go())


def _tok(client, username):
    r = client.post("/api/users/token", data={"username": username, "password": "pw123456"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


_REAL_CLIENT = httpx.AsyncClient      # captured before any patching: Fakes must not stack


def _telegram(monkeypatch, handler):
    from app.services import backup_service as bk

    class Fake(_REAL_CLIENT):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **kw)
    monkeypatch.setattr(bk.httpx, "AsyncClient", Fake)


class TestThroughTheApp:

    def test_the_message_the_send_and_the_daily_record(self, client, monkeypatch):
        from app.crm import digest
        _seed()
        boss = _tok(client, "dg_boss")

        # the preview: every line present, the numbers at least what was seeded
        # (other suites share this database in CI)
        r = client.get("/api/backup/digest", headers=boss)
        assert r.status_code == 200, r.text
        pv = r.json()
        text, n = pv["text"], pv["counts"]
        assert text.startswith("☀️ خلاصهٔ صبح — ") and "دیروز تا حالا:" in text
        assert n["properties"] >= 2 and n["rent"] >= 1 and n["jobs_done"] >= 1 and n["jobs_failed"] >= 1
        assert n["matches"] >= 1 and n["waiting"] >= 1 and n["drops"] >= 1
        assert n["calls_due"] >= 1 and n["callbacks"] >= 1
        assert f"🏠 آگهی تازه: {n['properties']} (فروش {n['properties'] - n['rent']} · اجاره {n['rent']})" in text
        assert f"🕷 اسکرپ: {n['jobs_done']} کامل · {n['jobs_failed']} ناموفق" in text
        assert f"🎯 تطبیق تازه: {n['matches']} · در انتظار تماس: {n['waiting']}" in text
        assert f"📉 کاهش قیمت: {n['drops']}" in text and "• آپارتمان ۹۵ متری خیابان کاشانی — 11٪ ارزان‌تر" in text
        assert f"📞 صف تماس امروز: {n['calls_due']} · تماس مجدد امروز: {n['callbacks']}" in text
        assert "💾 بکاپ دیشب: " in text and text.rstrip().endswith("/dashboard/#crm")
        assert "09141112233" not in text, "owners' numbers stay in the office"
        assert pv["hour"] == digest.HOUR and pv["configured"] is False and pv["last_sent"] is None

        # nothing to send to: the button says so, nothing is recorded
        assert client.post("/api/backup/digest/send", headers=boss).status_code == 502

        # configured: the extra send reaches every chat and leaves the daily record alone
        assert client.put("/api/backup/settings", headers=boss,
                          json={"bot_token": "123456789:AAHdg-token_for_the_digest_test_0123456", "chat_id": "111, 222", "proxy_mode": "manual", "proxy": ""}).status_code == 200
        calls = []

        def handler(req):
            calls.append((req.url.path, req.read()))
            return httpx.Response(200, json={"ok": True, "result": {"message_id": len(calls)}})
        _telegram(monkeypatch, handler)
        r = client.post("/api/backup/digest/send", headers=boss)
        assert r.status_code == 200, r.text
        assert r.json()["delivered"] == ["111", "222"] and r.json()["error"] == ""
        assert len(calls) == 2 and all(p.endswith("/sendMessage") for p, _ in calls)
        assert b"\\u2600" in calls[0][1] or "☀️".encode() in calls[0][1]
        assert client.get("/api/backup/digest", headers=boss).json()["last_sent"] is None

        # the loop's own send, after the hour: once, then recorded; a second tick waits for tomorrow
        monkeypatch.setattr(digest, "HOUR", 8)
        at_eight_thirty = datetime.now(timezone.utc).astimezone(digest.TEHRAN).replace(hour=8, minute=30)
        res = asyncio.run(digest.tick(now=at_eight_thirty))
        assert res["ok"] and res["delivered"] == ["111", "222"], res
        today = at_eight_thirty.strftime("%Y-%m-%d")
        assert client.get("/api/backup/status", headers=boss).json()["digest_last_sent"] == today
        assert asyncio.run(digest.tick(now=at_eight_thirty)) == {"skipped": "sent"}
        assert len(calls) == 4

        # one chat refusing does not lose the other, and is named
        def flaky(req):
            body = req.read()
            if b'"chat_id": "222"' in body or b'"chat_id":"222"' in body:
                return httpx.Response(400, json={"ok": False, "description": "Bad Request: chat not found"})
            return httpx.Response(200, json={"ok": True, "result": {}})
        _telegram(monkeypatch, flaky)
        r = client.post("/api/backup/digest/send", headers=boss)
        assert r.status_code == 200 and r.json()["delivered"] == ["111"] and "222: 400" in r.json()["error"]

        client.put("/api/backup/settings", headers=boss, json={"bot_token": "", "chat_id": ""})
