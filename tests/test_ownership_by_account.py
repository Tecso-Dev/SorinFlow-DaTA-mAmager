"""
Who owns a row is an account, not a display name.

Private files, personal cabinets, matches, tasks, the call queue and
«سورین»'s customers used to be attributed by the owner's display name —
full_name, else username — and a display name is something anyone edits in
their own profile. Each owned row now also names the account
(app/auth/visibility.py OWNERSHIP): written with the name by this release,
resolved from the name for what an older release wrote.

A sqlite database of this file's own — foreign keys on, so deleting an
account nulls its rows the way Postgres does — and a fake Redis: runs the
same in the sqlite suite and the Postgres one.
"""
import asyncio
import os
import sys

import fakeredis
import fakeredis.aioredis
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_ownership_by_account.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

import sqlalchemy as sa  # noqa: E402
from sqlalchemy import event, select, text  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

import app.models  # noqa: E402,F401 — every model on Base.metadata
from app.auth.visibility import OWNERSHIP, backfill_owner_ids, stamp_owner  # noqa: E402
from app.database import Base  # noqa: E402
from app.models.crm_models import Cabinet, Customer, CustomerMatch, Task  # noqa: E402
from app.models.lead import Lead  # noqa: E402
from app.models.property import Property  # noqa: E402
from app.models.user import User  # noqa: E402

# UUID columns sqlite cannot render; nothing here touches them
PG_ONLY = {"scraping_jobs", "scraping_logs", "skipped_listings"}
PERMS = ["crm", "filing", "properties"]


def _fk_on(engine):
    @event.listens_for(engine, "connect")
    def _on(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


def _prop(serial, **kw):
    return Property(tag_number=f"own-{serial}", divar_id=f"own-{serial}", url=f"https://divar.ir/v/own-{serial}",
                    title=f"آپارتمان {serial}", city_name="ارومیه", district="خیابان گلها", area=100, rooms=2,
                    property_type="آپارتمان", listing_type="buy", total_price=4_000_000_000,
                    serial_no=serial, is_active=True, **kw)


# ── the backfill, on rows written the way the previous release writes them ────

@pytest.fixture
def sync_db(tmp_path):
    """A plain sync sqlite engine: backfill_owner_ids takes a sync Connection,
    as Alembic's op.get_bind() and init_db's run_sync hand it one."""
    eng = sa.create_engine(f"sqlite:///{tmp_path}/backfill.db")
    _fk_on(eng)
    tables = [t for name, t in Base.metadata.tables.items() if name not in PG_ONLY]
    Base.metadata.create_all(eng, tables=tables)
    with eng.begin() as c:
        c.execute(text(
            "INSERT INTO users (id, username, full_name, hashed_password, role, is_active, totp_enabled,"
            " email_2fa_enabled, phone_verified, email_verified, marketing_opt_in, presence, token_version) VALUES "
            "(1, 'mina', 'مینا رضایی', 'x', 'admin', 1, 0, 0, 0, 0, 0, 'available', 0),"
            "(2, 'reza', NULL, 'x', 'admin', 1, 0, 0, 0, 0, 0, 'available', 0),"      # known by username
            "(3, 'blank', '', 'x', 'super_admin', 1, 0, 0, 0, 0, 0, 'available', 0),"  # '' is no name: username
            "(4, 'twin1', 'دوقلو', 'x', 'admin', 1, 0, 0, 0, 0, 0, 'available', 0),"
            "(5, 'twin2', 'دوقلو', 'x', 'root', 1, 0, 0, 0, 0, 0, 'available', 0),"
            "(6, 'visitor', 'مینا رضایی', 'x', 'visitor', 1, 0, 0, 0, 0, 0, 'available', 0),"
            "(7, 'gone', 'رفته', 'x', 'admin', 0, 0, 0, 0, 0, 0, 'available', 0)"))  # inactive is still an account
    yield eng
    eng.dispose()


NAMES = {
    "مینا رضایی": 1,     # a visitor with the same name does not make it ambiguous
    "reza": 2,
    "blank": 3,
    "دوقلو": None,       # two staff accounts
    "Reza": None,        # compared exactly, as it always was
    " مینا رضایی": None,
    "رفته": 7,
    "کسی که نیست": None,
}


def _seed_by_name(eng):
    """Every owned table, one row per name, written with the name alone —
    what the previous release leaves behind."""
    with eng.begin() as c:
        c.execute(text("INSERT INTO properties (id, tag_number, divar_id, title, url) "
                       "VALUES (1, 'base', 'base', 'پایه', 'https://divar.ir/v/base')"))
        c.execute(text("INSERT INTO crm_customers (id, full_name) VALUES (1, 'پایه')"))
        for i, name in enumerate(NAMES, start=10):
            c.execute(text("INSERT INTO properties (id, tag_number, divar_id, title, url, created_by) "
                           "VALUES (:i, :t, :t, 'x', 'https://divar.ir/v/x', :n)"), {"i": i, "t": f"p{i}", "n": name})
            c.execute(text("INSERT INTO crm_cabinets (id, name, owner) VALUES (:i, 'کمد', :n)"), {"i": i, "n": name})
            c.execute(text("INSERT INTO crm_customer_matches (id, property_id, customer_id, score, consultant) "
                           "VALUES (:i, :i, 1, 60, :n)"), {"i": i, "n": name})
            c.execute(text("INSERT INTO crm_tasks (id, title, assigned_to) VALUES (:i, 'کار', :n)"), {"i": i, "n": name})
            c.execute(text("INSERT INTO leads (id, property_id, status, call_attempts, assigned_to) "
                           "VALUES (:i, 1, 'new', 0, :n)"), {"i": i, "n": name})
            c.execute(text("INSERT INTO crm_customers (id, full_name, consultant_name) VALUES (:i, 'م', :n)"),
                      {"i": i, "n": name})
        # never assigned: NULL and '' stay exactly as they are
        c.execute(text("INSERT INTO crm_tasks (id, title, assigned_to) VALUES (98, 'کار', NULL), (99, 'کار', '')"))


def _owners(eng):
    """{table: {name: account id}} as the database holds it now."""
    out = {}
    with eng.connect() as c:
        for table, (name_col, id_col) in OWNERSHIP.items():
            rows = c.execute(text(f"SELECT {name_col}, {id_col} FROM {table} WHERE id >= 10 AND id < 90")).all()
            out[table] = dict(rows)
    return out


def _backfill(eng):
    with eng.begin() as c:
        return backfill_owner_ids(c)


class TestBackfill:

    def test_each_name_gets_the_one_staff_account_that_goes_by_it(self, sync_db):
        _seed_by_name(sync_db)
        assert _backfill(sync_db) == len(OWNERSHIP) * len(NAMES)
        for table, owners in _owners(sync_db).items():
            assert owners == NAMES, table
        with sync_db.connect() as c:
            assert c.execute(text("SELECT assigned_to_user_id, owner_resolved_from FROM crm_tasks "
                                  "WHERE id IN (98, 99) ORDER BY id")).all() == [(None, None), (None, None)]

    def test_a_second_run_changes_nothing(self, sync_db):
        _seed_by_name(sync_db)
        _backfill(sync_db)
        assert _backfill(sync_db) == 0, "pods restart; the step runs at every boot"

    def test_renaming_after_the_backfill_moves_nothing(self, sync_db):
        """mina renames; nima takes her old name; the twins' ambiguity goes
        away; the next boots resolve nothing."""
        _seed_by_name(sync_db)
        _backfill(sync_db)
        with sync_db.begin() as c:
            c.execute(text("UPDATE users SET full_name = 'مینا ر.' WHERE id = 1"))
            c.execute(text("INSERT INTO users (id, username, full_name, hashed_password, role, is_active, "
                           "totp_enabled, email_2fa_enabled, phone_verified, email_verified, marketing_opt_in, "
                           "presence, token_version) VALUES (8, 'nima', 'مینا رضایی', 'x', 'admin', 1, 0, 0, 0, 0, 0,"
                           " 'available', 0)"))
            c.execute(text("DELETE FROM users WHERE id = 5"))
            c.execute(text("UPDATE users SET full_name = 'کسی که نیست' WHERE id = 2"))
        assert _backfill(sync_db) == 0
        for table, owners in _owners(sync_db).items():
            assert owners == NAMES, table

    def test_a_deleted_account_leaves_its_rows_to_nobody_for_good(self, sync_db):
        _seed_by_name(sync_db)
        _backfill(sync_db)
        with sync_db.begin() as c:
            c.execute(text("DELETE FROM users WHERE id = 7"))         # ON DELETE SET NULL
            c.execute(text("UPDATE users SET full_name = 'رفته' WHERE id = 2"))
        _backfill(sync_db)
        for table, owners in _owners(sync_db).items():
            assert owners["رفته"] is None, table

    def test_what_the_previous_release_writes_later_is_resolved_at_the_next_boot(self, sync_db):
        """A reassignment by name leaves the old account beside the new name
        until the boot step sees the two disagree; a new row by name has no
        account until then."""
        _seed_by_name(sync_db)
        _backfill(sync_db)
        with sync_db.begin() as c:
            c.execute(text("UPDATE crm_tasks SET assigned_to = 'reza' WHERE assigned_to = 'مینا رضایی'"))
            c.execute(text("UPDATE crm_customers SET consultant_name = NULL WHERE consultant_name = 'reza'"))
            c.execute(text("INSERT INTO leads (id, property_id, status, call_attempts, assigned_to) "
                           "VALUES (50, 1, 'new', 0, 'مینا رضایی')"))
        assert _backfill(sync_db) == 3
        with sync_db.connect() as c:
            tasks = c.execute(text("SELECT assigned_to, assigned_to_user_id FROM crm_tasks WHERE id >= 10 AND id < 90")).all()
            assert sorted(uid for name, uid in tasks if name == "reza") == [2, 2]
            assert c.execute(text("SELECT consultant_user_id FROM crm_customers WHERE id = 11")).scalar() is None
            assert c.execute(text("SELECT assigned_to_user_id FROM leads WHERE id = 50")).scalar() == 1

    def test_a_table_without_the_columns_yet_is_left_alone(self, sync_db):
        """The boot step runs before Alembic on the first boot after this
        change: the columns are not there yet, and 0016 backfills itself."""
        with sync_db.begin() as c:
            c.execute(text("ALTER TABLE crm_tasks DROP COLUMN owner_resolved_from"))
            c.execute(text("INSERT INTO crm_tasks (id, title, assigned_to) VALUES (10, 'کار', 'reza')"))
        assert _backfill(sync_db) == 0


# ── the panel writes both halves ─────────────────────────────────────────────

@pytest.fixture
def office(tmp_path, monkeypatch):
    """Two consultants, a manager and root in a database of their own, the
    API as the panel calls it, and a boot step to run on demand."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import app.database as database
    import app.services.audit as audit
    from app.api.routes import router as api_router
    from app.auth.jwt import access_claims, create_access_token

    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/office.db", poolclass=NullPool)
    _fk_on(eng.sync_engine)
    maker = async_sessionmaker(eng, expire_on_commit=False)
    people = {
        "root": User(username="own_root", full_name="روت", role="root"),
        "boss": User(username="own_boss", full_name="مدیر دفتر", role="super_admin"),
        "mina": User(username="own_mina", full_name="مینا رضایی", role="admin", permissions=PERMS),
        "reza": User(username="own_reza", full_name="رضا کریمی", role="admin", permissions=PERMS),
        "nima": User(username="own_nima", full_name="نیما", role="admin", permissions=PERMS),
    }

    async def build():
        tables = [t for name, t in Base.metadata.tables.items() if name not in PG_ONLY]
        async with eng.begin() as c:
            await c.run_sync(lambda sc: Base.metadata.create_all(sc, tables=tables))
        async with maker() as s:
            for u in people.values():
                u.hashed_password, u.is_active = "x", True
            s.add_all(people.values())
            await s.commit()
    asyncio.run(build())

    server = fakeredis.FakeServer()

    async def get_redis():
        return fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    monkeypatch.setattr(database, "get_redis", get_redis)
    monkeypatch.setattr(audit, "async_session_maker", maker)

    api = FastAPI()
    api.include_router(api_router, prefix="/api")

    async def session():
        async with maker() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise
    api.dependency_overrides[database.get_db] = session
    client = TestClient(api)

    def call(method, path, who, **kw):
        headers = {"Authorization": f"Bearer {create_access_token(access_claims(people[who]))}"}
        r = client.request(method, f"/api{path}", headers=headers, **kw)
        return r

    def run(fn):
        async def go():
            async with maker() as s:
                out = await fn(s)
                await s.commit()
                return out
        return asyncio.run(go())

    def boot():
        async def go():
            async with eng.begin() as c:
                return await c.run_sync(backfill_owner_ids)
        return asyncio.run(go())

    yield {"api": call, "run": run, "boot": boot, "people": people, "maker": maker}
    asyncio.run(eng.dispose())


def _get(office, model, row_id):
    return office["run"](lambda s: s.get(model, row_id))


class TestWrites:

    def test_a_task_is_its_creators_account_unless_somebody_is_named(self, office):
        api, p = office["api"], office["people"]
        own = api("POST", "/crm/tasks", "mina", json={"title": "تماس با مالک"}).json()
        assert (own["assigned_to"], own["assigned_to_user_id"]) == ("مینا رضایی", p["mina"].id)
        handed = api("POST", "/crm/tasks", "mina", json={"title": "بازدید", "assigned_to": "رضا کریمی"}).json()
        assert handed["assigned_to_user_id"] == p["reza"].id
        nobody = api("POST", "/crm/tasks", "mina", json={"title": "؟", "assigned_to": "کسی که نیست"}).json()
        assert nobody["assigned_to"] == "کسی که نیست" and nobody["assigned_to_user_id"] is None

    def test_a_form_that_sends_back_the_name_it_showed_keeps_the_owner(self, office):
        """The task form sends assigned_to on every save, filled with the
        name it was shown — after its owner renamed, a name nobody holds."""
        api, p = office["api"], office["people"]
        task = api("POST", "/crm/tasks", "boss", json={"title": "قرارداد", "assigned_to": "رضا کریمی"}).json()
        assert api("PATCH", "/users/me", "reza", json={"full_name": "رضا کریمی (فروش)"}).status_code == 200
        r = api("PUT", f"/crm/tasks/{task['id']}", "boss", json={"title": "قرارداد نهایی", "assigned_to": "رضا کریمی"})
        assert r.status_code == 200 and r.json()["assigned_to_user_id"] == p["reza"].id
        r = api("PUT", f"/crm/tasks/{task['id']}", "boss", json={"assigned_to": "مینا رضایی"})
        assert r.json()["assigned_to_user_id"] == p["mina"].id, "a different name is a reassignment"

    def test_a_personal_cabinet_and_a_private_file_carry_their_makers_account(self, office):
        api, p = office["api"], office["people"]
        cab = api("POST", "/filing/cabinets", "mina", json={"name": "کمد من", "personal": True}).json()
        assert (cab["owner"], cab["owner_user_id"]) == ("مینا رضایی", p["mina"].id)
        shared = api("PATCH", f"/filing/cabinets/{cab['id']}", "mina", json={"personal": False}).json()
        assert shared["owner"] is None and shared["owner_user_id"] is None

        pid = office["run"](lambda s: _add(s, _prop(9101)))
        assert api("PATCH", f"/filing/files/{pid}", "mina", json={"is_private": True}).status_code == 200
        prop = _get(office, Property, pid)
        assert (prop.created_by, prop.created_by_user_id) == ("مینا رضایی", p["mina"].id)

    def test_the_first_dial_claims_the_lead_for_the_callers_account(self, office):
        api, p = office["api"], office["people"]

        async def seed(s):
            prop = await _add(s, _prop(9102))
            return await _add(s, Lead(property_id=prop, phone_number="09140000001", status="new"))
        lid = office["run"](seed)
        r = api("POST", f"/crm/leads/{lid}/call", "reza", json={"outcome": "no_answer"})
        assert r.status_code == 200, r.text
        assert r.json()["lead"]["assigned_to_user_id"] == p["reza"].id
        r = api("PATCH", f"/crm/leads/{lid}", "boss", json={"assigned_to": "مینا رضایی"})
        assert r.json()["assigned_to_user_id"] == p["mina"].id

    def test_a_customers_consultant_is_resolved_and_the_match_copies_the_account(self, office):
        api, p = office["api"], office["people"]
        cust = api("POST", "/crm/customers", "boss", json={
            "full_name": "خریدار گلها", "mobile1": "09121110000", "consultant_name": "مینا رضایی",
            "desired_city": "ارومیه", "desired_district": "خیابان گلها", "desired_type": "apartment",
            "deal_type": "buy", "budget_max": 5_000_000_000}).json()
        assert cust["consultant_user_id"] == p["mina"].id
        office["run"](lambda s: _add(s, _prop(9103)))

        from app.crm import match_engine
        office["run"](lambda s: match_engine.run_once(s, notify=False))
        match = office["run"](lambda s: _scalar(s, select(CustomerMatch).where(
            CustomerMatch.customer_id == cust["id"])))
        assert (match.consultant, match.consultant_user_id) == ("مینا رضایی", p["mina"].id)


async def _add(s, row):
    s.add(row)
    await s.flush()
    return row.id


async def _scalar(s, stmt):
    return (await s.execute(stmt)).scalars().first()


def test_stamp_owner_writes_the_name_the_account_is_for():
    t = Task(title="x")
    stamp_owner(t, "مینا رضایی", 7)
    assert (t.assigned_to, t.assigned_to_user_id, t.owner_resolved_from) == ("مینا رضایی", 7, "مینا رضایی")
    for model in (Property, Cabinet, CustomerMatch, Task, Lead, Customer):
        name_col, id_col = OWNERSHIP[model.__tablename__]
        assert hasattr(model, name_col) and hasattr(model, id_col) and hasattr(model, "owner_resolved_from")
