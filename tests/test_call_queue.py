"""
The call queue: one tap per outcome, and the lead comes back when it should.

1,728 of 1,739 leads were «new» on 18 September. The grid with a status
dropdown was not how anyone works a phone list.
"""
import os
import sys
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_calls.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.crm import call_queue as cq   # noqa: E402

NOW = datetime(2026, 9, 18, 8, 0, tzinfo=timezone.utc)     # 11:30 Tehran


class TestOutcomes:

    def test_answered_moves_new_to_contacted_and_does_not_come_back(self):
        r = cq.apply("answered", status="new", attempts=0, now=NOW)
        assert r["status"] == "contacted" and r["next_call_at"] is None and r["call_attempts"] == 1

    def test_answered_leaves_a_later_status_alone(self):
        assert cq.apply("answered", status="visit", attempts=3, now=NOW)["status"] == "visit"

    def test_no_answer_comes_back_two_hours_then_tomorrow_morning_then_two_days(self):
        first = cq.apply("no_answer", status="new", attempts=0, now=NOW)
        assert first["status"] == "new" and first["next_call_at"] == NOW + timedelta(hours=2)
        second = cq.apply("no_answer", status="new", attempts=1, now=NOW)
        tehran = second["next_call_at"].astimezone(cq.TEHRAN)
        assert (tehran.day, tehran.hour, tehran.minute) == (19, 10, 0)
        third = cq.apply("no_answer", status="new", attempts=2, now=NOW)
        assert third["next_call_at"] == NOW + timedelta(days=2)

    def test_after_four_unanswered_it_stops_coming_back_on_its_own(self):
        r = cq.apply("no_answer", status="new", attempts=3, now=NOW)
        assert r["call_attempts"] == 4 and r["next_call_at"] is None
        assert r["status"] == "new", "still open — a person can decide, the queue just stops nagging"

    def test_busy_is_half_an_hour(self):
        assert cq.apply("busy", status="new", attempts=0, now=NOW)["next_call_at"] == NOW + timedelta(minutes=30)

    def test_callback_needs_a_time_and_uses_it(self):
        with pytest.raises(ValueError):
            cq.apply("callback", status="new", attempts=0, now=NOW)
        at = NOW + timedelta(hours=5)
        r = cq.apply("callback", status="new", attempts=0, now=NOW, callback_at=at)
        assert r["next_call_at"] == at and r["status"] == "contacted"

    def test_visit_and_rejections(self):
        assert cq.apply("visit", status="contacted", attempts=1, now=NOW)["status"] == "visit"
        assert cq.apply("not_interested", status="new", attempts=0, now=NOW)["status"] == "rejected"
        assert cq.apply("wrong_number", status="new", attempts=0, now=NOW)["status"] == "rejected"

    def test_an_outcome_the_panel_does_not_offer_is_refused(self):
        with pytest.raises(ValueError):
            cq.apply("hung_up", status="new", attempts=0, now=NOW)


class TestItIsWiredIn:

    def test_the_columns_exist_on_the_model_and_in_the_migration(self):
        from app.models.lead import Lead
        for col in ("next_call_at", "call_attempts", "last_call_at", "last_call_outcome"):
            assert hasattr(Lead, col)
        db = Path("app/database.py").read_text(encoding="utf-8")
        assert "ADD COLUMN IF NOT EXISTS next_call_at TIMESTAMPTZ" in db
        assert "_migrate_call_queue," in db

    def test_the_status_change_is_scored_like_a_hand_edit(self):
        src = Path("app/api/routes/crm.py").read_text(encoding="utf-8")
        fn = src[src.index("async def log_call"):src.index("async def calls_summary")]
        assert "await record_lead_status(db, agent, lead.status)" in fn
        assert '_log_activity(db, "lead", lead.id, "call"' in fn

    def test_the_queue_is_mine_or_nobodys_and_due(self):
        src = Path("app/api/routes/crm.py").read_text(encoding="utf-8")
        fn = src[src.index("def _queue_query"):src.index("async def calls_today")]
        assert "Lead.assigned_to == agent" in fn and "Lead.assigned_to.is_(None)" in fn
        assert "Lead.next_call_at <= now" in fn
        assert 'Lead.status.in_(("new", "contacted"))' in fn

    def test_the_panel_has_the_tab_first_and_no_native_dialogs(self):
        html = Path("frontend/index.html").read_text(encoding="utf-8")
        js = Path("frontend/js/app.js").read_text(encoding="utf-8")
        tabs = html[html.index('id="crm-main-tabs"'):html.index('id="crm-tabs-content"')]
        assert tabs.index("#crm-tab-calls") < tabs.index("#crm-tab-tasks"), "the day starts with the calls"
        assert 'id="crm-tab-calls"' in html
        blk = js[js.index("// ═══ Call queue"):js.index("// ═══ End call queue")]
        for bad in ("prompt(", "confirm(", "alert("):
            assert bad not in blk.replace("askConfirm(", "").replace("askText(", "")
        assert "href=\"tel:" in blk, "on a phone, the number must be a tap"
        assert "loadCalls();" in js.split("case 'crm':")[1][:120]


# ── through the real app (Postgres) ──────────────────────────────────────────

@pytest.fixture(scope="module")
def client():
    import fakeredis.aioredis
    import app.database as db
    from app.config import get_settings
    if not str(db.engine.url).startswith("postgresql"):
        pytest.skip("needs Postgres — see test_auth_roles.py", allow_module_level=True)
    cfg = get_settings()
    saved = (cfg.environment, cfg.api_key, cfg.cookies_path, cfg.scrape_scheduler)
    cfg.environment, cfg.api_key = "test", ""
    cfg.cookies_path = "/tmp/sorinflow-test-cookies"
    cfg.scrape_scheduler = False
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
    (cfg.environment, cfg.api_key, cfg.cookies_path, cfg.scrape_scheduler) = saved


def _seed(username, full_name, leads):
    """A consultant and a few leads (each on its own property)."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.models.user import User
    from app.models.property import Property
    from app.models.lead import Lead
    from app.auth.jwt import get_password_hash

    async def _go():
        eng = create_async_engine(os.environ["DATABASE_URL"])
        maker = async_sessionmaker(eng, expire_on_commit=False)
        ids = []
        try:
            async with maker() as s:
                s.add(User(username=username, full_name=full_name, role="admin", permissions=["crm"],
                           hashed_password=get_password_hash("pw123456"), is_active=True))
                await s.commit()
                for kw in leads:
                    uid = f"{username}-{len(ids)}"
                    p = Property(title=kw.get("title", "آپارتمان"), tag_number=f"cq-{uid}", divar_id=f"cq-{uid}",
                                 url=f"https://divar.ir/v/{uid}")
                    s.add(p)
                    await s.flush()
                    l = Lead(property_id=p.id, phone_number=kw.get("phone", "09140000001"),
                             property_title=p.title, city_name="ارومیه", status=kw.get("status", "new"),
                             assigned_to=kw.get("assigned_to"), next_call_at=kw.get("next_call_at"))
                    s.add(l)
                    await s.flush()
                    ids.append(l.id)
                await s.commit()
        finally:
            await eng.dispose()
        return ids
    return asyncio.run(_go())


def _tok(client, username):
    r = client.post("/api/users/token", data={"username": username, "password": "pw123456"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


class TestThroughTheApp:

    def test_a_days_work(self, client):
        soon = datetime.now(timezone.utc) - timedelta(minutes=5)
        later = datetime.now(timezone.utc) + timedelta(hours=3)
        mine, theirs = _seed("cq_sara", "سارا احمدی", [
            {"phone": "09140000001"},                                     # fresh
            {"phone": "09140000002", "next_call_at": soon},               # a callback that is due
            {"phone": "09140000003", "next_call_at": later},              # not yet
            {"phone": "09140000004", "assigned_to": "کس دیگر"},           # somebody else's
            {"phone": "09140000005", "status": "rejected"},               # closed
        ])[:2], None
        sara = _tok(client, "cq_sara")
        d = client.get("/api/crm/calls/today", headers=sara).json()
        ids = [x["id"] for x in d["items"]]
        assert mine[1] == ids[0], "the due callback comes first"
        assert mine[0] in ids and len(ids) == 2, "not-yet, somebody else's and closed leads stay out"
        assert d["due_callbacks"] == 1 and d["done_today"] == 0 and d["agent"] == "سارا احمدی"

        # no answer: the lead leaves the list and comes back in two hours
        r = client.post(f"/api/crm/leads/{mine[0]}/call", headers=sara, json={"outcome": "no_answer"})
        assert r.status_code == 200, r.text
        assert r.json()["next_call_at"] and r.json()["lead"]["assigned_to"] == "سارا احمدی", "the first dial claims it"
        assert mine[0] not in [x["id"] for x in client.get("/api/crm/calls/today", headers=sara).json()["items"]]

        # answered, with a note: status moves, the note lands on the lead, the timeline has both
        r = client.post(f"/api/crm/leads/{mine[1]}/call", headers=sara,
                        json={"outcome": "answered", "note": "قیمت قطعی است"})
        assert r.status_code == 200, r.text
        lead = r.json()["lead"]
        assert lead["status"] == "contacted" and "قیمت قطعی است" in lead["notes"]
        tl = client.get(f"/api/crm/activity/lead/{mine[1]}", headers=sara).json()
        actions = [a["action"] for a in (tl if isinstance(tl, list) else tl.get("items", []))]
        assert "call" in actions and "status_change" in actions

        # the manager's summary counts today's dials by outcome
        s = client.get("/api/crm/calls/summary?days=1", headers=sara).json()
        me = next(a for a in s["agents"] if a["agent"] == "سارا احمدی")
        assert me["calls"] == 2 and me["answered"] == 1 and me["no_answer"] == 1

        # a visit with a time lands in the calendar
        at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        r = client.post(f"/api/crm/leads/{mine[1]}/call", headers=sara, json={"outcome": "visit", "visit_at": at})
        assert r.status_code == 200 and r.json()["event_id"] and r.json()["lead"]["status"] == "visit"

    def test_somebody_elses_lead_cannot_be_dialled_by_an_admin(self, client):
        ids = _seed("cq_other", "دیگری", [{"phone": "09140000009", "assigned_to": "سارا احمدی"}])
        r = client.post(f"/api/crm/leads/{ids[0]}/call", headers=_tok(client, "cq_other"), json={"outcome": "answered"})
        assert r.status_code == 403

    def test_an_unknown_outcome_is_a_400(self, client):
        ids = _seed("cq_bad", "بد", [{"phone": "09140000010"}])
        r = client.post(f"/api/crm/leads/{ids[0]}/call", headers=_tok(client, "cq_bad"), json={"outcome": "hung_up"})
        assert r.status_code == 400
