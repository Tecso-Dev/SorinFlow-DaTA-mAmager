"""
Deleting a user who has a forwarder phone or a scheduled scrape.

Reported by رسا on 1405/07/05: «حذف» asks for confirmation and then the user
is still there. forwarder_devices.user_id and scrape_schedules.owner_user_id
point at users with no ON DELETE rule, so Postgres refused the delete of
anyone who had either, and the panel only showed an error toast. Everything
else that points at a user already says what happens to it (leads and
properties keep their row with the owner cleared; portal and Telegram rows
go with the person).
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_user_delete.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")


@pytest.fixture(scope="module")
def client():
    import app.database as db
    from app.config import get_settings
    if not str(db.engine.url).startswith("postgresql"):
        pytest.skip("needs Postgres — foreign keys are what this is about", allow_module_level=True)
    cfg = get_settings()
    saved = (cfg.environment, cfg.api_key, cfg.scrape_scheduler, cfg.match_engine,
             cfg.scrape_worker_enabled)
    cfg.environment, cfg.api_key = "test", ""
    cfg.scrape_scheduler = cfg.match_engine = cfg.scrape_worker_enabled = False
    from _fake_redis import redis_factory
    get_redis = redis_factory()
    import app.services.verification as v
    from app.api.routes import auth as auth_routes
    db.get_redis = v.get_redis = auth_routes.get_redis = get_redis
    from fastapi.testclient import TestClient
    import app.main as m
    with TestClient(m.app) as c:
        yield c
    (cfg.environment, cfg.api_key, cfg.scrape_scheduler, cfg.match_engine,
     cfg.scrape_worker_enabled) = saved


def _run(coro_fn):
    async def _go():
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        eng = create_async_engine(os.environ["DATABASE_URL"])
        try:
            async with async_sessionmaker(eng, expire_on_commit=False)() as s:
                return await coro_fn(s)
        finally:
            await eng.dispose()
    return asyncio.run(_go())


@pytest.fixture(scope="module")
def people(client):
    from app.models.user import User
    from app.auth.jwt import get_password_hash

    async def _go(s):
        admin = User(username="ud_admin", full_name="مدیر", role="super_admin", permissions=[],
                     phone="09120000301", phone_verified=True,
                     hashed_password=get_password_hash("pw123456"), is_active=True)
        s.add(admin)
        await s.commit()
        return admin.id
    return {"admin": _run(_go)}


def _auth(client):
    r = client.post("/api/users/token", data={"username": "ud_admin", "password": "pw123456"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _colleague(name, *, phone=False, schedule=False, lead=False):
    """A colleague with whatever history the case needs; returns ids."""
    from app.models.user import User
    from app.models.forwarder import ForwarderDevice
    from app.models.scrape_schedule import ScrapeSchedule
    from app.models.lead import Lead

    async def _go(s):
        u = User(username=name, full_name=name, role="admin", permissions=["scraper"],
                 hashed_password="x", is_active=True)
        s.add(u)
        await s.flush()
        out = {"user": u.id}
        if phone:
            d = ForwarderDevice(user_id=u.id, label="گوشی", sim_phone="09140000001")
            s.add(d)
            await s.flush()
            out["device"] = d.id
        if schedule:
            sc = ScrapeSchedule(owner_user_id=u.id, name="هر روز", hour=8, minute=0,
                                enabled=True, config={"city": "urmia", "category": "buy-apartment"})
            s.add(sc)
            await s.flush()
            out["schedule"] = sc.id
        if lead:
            from app.models.property import Property
            prop = Property(tag_number=f"t-{name}", divar_id=f"d-{name}",
                            url=f"https://divar.ir/v/{name}", title="آپارتمان")
            s.add(prop)
            await s.flush()
            ld = Lead(property_id=prop.id, phone_number="09140000002", status="new",
                      assigned_to_user_id=u.id)
            s.add(ld)
            await s.flush()
            out["lead"] = ld.id
        await s.commit()
        return out
    return _run(_go)


def _exists(model_path, row_id):
    import importlib
    mod, cls = model_path.rsplit(".", 1)
    model = getattr(importlib.import_module(mod), cls)

    async def _go(s):
        return await s.get(model, row_id)
    return _run(_go)


def test_a_user_with_a_forwarder_phone_is_deleted(client, people):
    ids = _colleague("ud_phone", phone=True)
    r = client.delete(f"/api/users/{ids['user']}", headers=_auth(client))
    assert r.status_code == 200, r.text
    assert _exists("app.models.user.User", ids["user"]) is None
    # the phone answered for this person's numbers alone; it goes with them
    assert _exists("app.models.forwarder.ForwarderDevice", ids["device"]) is None


def test_a_user_with_a_scheduled_scrape_is_deleted(client, people):
    ids = _colleague("ud_sched", schedule=True)
    r = client.delete(f"/api/users/{ids['user']}", headers=_auth(client))
    assert r.status_code == 200, r.text
    assert _exists("app.models.user.User", ids["user"]) is None
    # a schedule runs on its owner's account; nobody is left to run it for
    assert _exists("app.models.scrape_schedule.ScrapeSchedule", ids["schedule"]) is None


def test_their_leads_stay_with_the_owner_cleared(client, people):
    ids = _colleague("ud_leads", phone=True, schedule=True, lead=True)
    r = client.delete(f"/api/users/{ids['user']}", headers=_auth(client))
    assert r.status_code == 200, r.text
    lead = _exists("app.models.lead.Lead", ids["lead"])
    assert lead is not None and lead.assigned_to_user_id is None


def test_the_delete_is_in_the_audit_log(client, people):
    ids = _colleague("ud_audit", phone=True)
    h = _auth(client)
    assert client.delete(f"/api/users/{ids['user']}", headers=h).status_code == 200
    r = client.get("/api/audit/events", params={"action": "user_delete"}, headers=h)
    assert r.status_code == 200, r.text
    mine = [e.get("summary") or "" for e in r.json().get("items", []) if "ud_audit" in (e.get("summary") or "")]
    assert mine and "1 گوشی فورواردر" in mine[0], mine


def test_the_confirmation_says_what_goes_with_them():
    from pathlib import Path
    app_js = (Path(__file__).resolve().parent.parent / "frontend" / "js" / "app.js").read_text(encoding="utf-8")
    i = app_js.index("async function deleteUser(id)")
    assert "گوشی‌های فورواردر و اسکرپ‌های زمان‌بندی‌شدهٔ او هم حذف می‌شوند" in app_js[i:i + 600]
