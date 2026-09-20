"""
کمپین ایمیلی — the marketing panel's missing half.

`/api/email/audiences`, `/broadcast` and `/export` existed and nothing in the
panel called them (roadmap #10). The card mirrors the SMS twin: a named group
with its count shown and sent back for the server to verify, a preview of the
very template the broadcast uses, the addresses as a CSV.
"""
import os
import sys
import asyncio
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_email_campaign.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

ROOT = Path(__file__).resolve().parent.parent
JS = (ROOT / "frontend/js/app.js").read_text(encoding="utf-8")
HTML = (ROOT / "frontend/index.html").read_text(encoding="utf-8")


class TestThePanel:

    def test_the_three_endpoints_are_called_now(self):
        for ep in ("/email/audiences", "/email/broadcast'", "/email/broadcast/preview", "/email/export?audience="):
            assert ep in JS, ep

    def test_the_card_is_the_managers_and_counts_before_it_sends(self):
        card = HTML[HTML.index('id="em-campaign-card"'):HTML.index('id="em-filter-template"')]
        assert "crm-superadmin-only" in HTML[HTML.index('id="em-campaign-card"') - 80:HTML.index('id="em-campaign-card"')]
        for i in ("em-audiences", "em-bc-subject", "em-bc-body", "em-bc-cta", "em-bc-url", "em-bc-btn", "em-bc-preview"):
            assert f'id="{i}"' in card, i
        fn = JS[JS.index("async function sendEmailBroadcast"):JS.index("async function exportEmailAudience")]
        assert "confirm_count: _emAudienceCount" in fn, "the number shown is the number the server verifies"
        assert "askConfirm(" in fn and "prompt(" not in fn and "confirm(" not in fn.replace("askConfirm(", "")
        assert "/تغییر کرده/.test(e.message" in fn, "a 409 reloads the counts"

    def test_the_preview_is_the_broadcast_template(self):
        src = (ROOT / "app/api/routes/email.py").read_text(encoding="utf-8")
        fn = src[src.index("async def broadcast_preview"):src.index('@router.get("/export")')]
        assert "tpl.notification(" in fn and "_: User = _super_admin" in fn
        assert "raw: true" in JS[JS.index("async function emPreview()"):JS.index("async function sendEmailBroadcast")]

    def test_the_history_can_be_narrowed_to_campaigns(self):
        assert '<option value="broadcast">کمپین‌ها</option>' in HTML
        assert "qs.set('template', template)" in JS


# ── through the real app (Postgres) ──────────────────────────────────────────

@pytest.fixture(scope="module")
def client():
    import fakeredis.aioredis
    import app.database as db
    from app.config import get_settings
    if not str(db.engine.url).startswith("postgresql"):
        pytest.skip("needs Postgres — see test_auth_roles.py", allow_module_level=True)
    cfg = get_settings()
    saved = (cfg.environment, cfg.api_key, cfg.cookies_path, cfg.scrape_scheduler, cfg.match_engine)
    cfg.environment, cfg.api_key = "test", ""
    cfg.cookies_path = "/tmp/sorinflow-test-cookies"
    cfg.scrape_scheduler = False
    cfg.match_engine = False
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
    (cfg.environment, cfg.api_key, cfg.cookies_path, cfg.scrape_scheduler, cfg.match_engine) = saved


def _seed():
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.models.user import User
    from app.auth.jwt import get_password_hash

    async def _go():
        eng = create_async_engine(os.environ["DATABASE_URL"])
        maker = async_sessionmaker(eng, expire_on_commit=False)
        try:
            async with maker() as s:
                s.add(User(username="ec_boss", full_name="مدیر", role="super_admin", permissions=["email"], email="boss@sorinflow.example",
                           email_verified=True, hashed_password=get_password_hash("pw123456"), is_active=True))
                s.add(User(username="ec_staff", full_name="کارمند", role="admin", permissions=["email"],
                           hashed_password=get_password_hash("pw123456"), is_active=True))
                # two visitors: one consented and verified, one consented but never verified
                s.add(User(username="ec_v1", full_name="بازدیدکننده", role="visitor", email="v1@sorinflow.example",
                           email_verified=True, marketing_opt_in=True, hashed_password=get_password_hash("pw123456"), is_active=True))
                s.add(User(username="ec_v2", full_name="بازدیدکننده ۲", role="visitor", email="v2@sorinflow.example",
                           email_verified=False, marketing_opt_in=True, hashed_password=get_password_hash("pw123456"), is_active=True))
                await s.commit()
        finally:
            await eng.dispose()
    asyncio.run(_go())


def _tok(client, username):
    r = client.post("/api/users/token", data={"username": username, "password": "pw123456"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


class TestThroughTheApp:

    def test_count_preview_export_and_a_broadcast(self, client, monkeypatch):
        _seed()
        boss, staff = _tok(client, "ec_boss"), _tok(client, "ec_staff")

        auds = {a["key"]: a for a in client.get("/api/email/audiences", headers=boss).json()["audiences"]}
        assert auds["marketing"]["count"] == 1, "consent AND a verified address"
        assert auds["staff"]["count"] >= 1

        # the preview is the template with the form's words in it, and the manager's
        r = client.post("/api/email/broadcast/preview", headers=boss,
                        json={"subject": "فایل‌های تازه", "message": "سلام", "cta_label": "دیدن", "cta_url": "https://sorinflow.example"})
        assert r.status_code == 200 and "فایل‌های تازه" in r.text and "دیدن" in r.text and "<html" in r.text
        assert client.post("/api/email/broadcast/preview", headers=staff, json={"subject": "x", "message": "y"}).status_code == 403

        ex = client.get("/api/email/export?audience=marketing", headers=boss).json()
        assert ex["emails"] == ["v1@sorinflow.example"] and ex["count"] == 1
        assert client.get("/api/email/export?audience=marketing", headers=staff).status_code == 403

        # the broadcast goes through the mail service once per address; the log says so
        sent = []
        from app.api.routes import email as E

        async def fake_send(addr, subject, html, text, db=None, cfg=None):
            sent.append(addr)
            return {"success": True}

        async def fake_cfg(db):
            return {"host": "x"}
        monkeypatch.setattr(E.mail, "send", fake_send)
        monkeypatch.setattr(E.mail, "resolve_config", fake_cfg)
        r = client.post("/api/email/broadcast", headers=boss, json={
            "audience": "marketing", "subject": "فایل‌های تازه", "message": "سلام", "confirm_count": 1})
        assert r.status_code == 200 and r.json() == {"ok": True, "sent": 1, "failed": 0, "total": 1}
        assert sent == ["v1@sorinflow.example"]
        # a stale count is refused, not quietly mailed
        assert client.post("/api/email/broadcast", headers=boss, json={
            "audience": "marketing", "subject": "x", "message": "y", "confirm_count": 5}).status_code == 409
        hist = client.get("/api/email/messages?template=broadcast", headers=boss).json()
        assert hist["total"] >= 1 and hist["items"][0]["template"] == "broadcast"
