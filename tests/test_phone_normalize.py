"""
«۰۹۱۴ ۱۲۳ ۴۵۶۷» and «+989141234567» are the same seller. Raw-string phone
columns missed that, on every lookup and every dedupe check — a Contact
looked up by phone_number == lead.phone_number never matched unless both
sides happened to be typed identically.

app/models/phone.py is the one place that decides what "the same number"
means; every model keeps a normalized twin in sync through an ORM event, and
the one exact-match lookup that existed (app/api/routes/crm.py's
convert_lead_to_deal) now compares normalized values instead of raw ones.
"""
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_phone_norm.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

import asyncio  # noqa: E402
import pytest  # noqa: E402

from app.models.phone import normalize_phone, sync_phone_columns  # noqa: E402

_RUN = uuid.uuid4().hex[:8]


@pytest.fixture(scope="session", autouse=True)
def _tables_exist():
    """This file's own sqlite fallback DB starts empty — nothing else in the
    suite has necessarily booted the app against it yet. Every model is
    imported (mirrors init_db/migrations/env.py) so Property's own foreign
    keys (cities, categories, crm_binders) resolve — Postgres, unlike
    sqlite, refuses to CREATE TABLE against a foreign key whose target does
    not exist yet. scraping_job.py's three tables are the one exclusion:
    their UUID columns are Postgres-only and sqlite cannot render them at
    all. create_all is checkfirst, so this is a harmless no-op on the shared
    Postgres DB the full suite runs against instead.
    """
    async def _go():
        from app.database import engine, Base
        from app.models import (property, cookie, scraping_job, lead, user,  # noqa: F401
                                crm_models, app_setting, portal, email_log,
                                sms_log, forwarder, scrape_schedule, ai_usage, ai_chat)
        skip = {"scraping_jobs", "scraping_logs", "skipped_listings"}
        tables = [t for name, t in Base.metadata.tables.items() if name not in skip]
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))
    asyncio.run(_go())


class TestNormalizeMobiles:

    def test_the_tasks_own_example_pair_match(self):
        """The exact pair named in the phase spec."""
        assert normalize_phone("۰۹۱۴ ۱۲۳ ۴۵۶۷") == normalize_phone("+989141234567")

    @pytest.mark.parametrize("raw", [
        "09141234567",
        "+989141234567",
        "00989141234567",
        "989141234567",
        "9141234567",
        "۰۹۱۴۱۲۳۴۵۶۷",              # Persian digits
        "٠٩١٤١٢٣٤٥٦٧",              # Arabic-Indic digits
        "0914-123-4567",
        "0914 123 4567",
        "(0914) 123-4567",
    ])
    def test_every_written_form_of_the_same_mobile_normalizes_alike(self, raw):
        assert normalize_phone(raw) == "9141234567"

    def test_the_canonical_mobile_form_has_no_leading_zero(self):
        """Spec: 9xxxxxxxxx, ten digits — distinct in shape from a landline,
        which keeps its leading 0."""
        n = normalize_phone("09141234567")
        assert n == "9141234567" and len(n) == 10 and not n.startswith("0")


class TestNormalizeLandlines:

    @pytest.mark.parametrize("raw,expected", [
        ("02188776655", "02188776655"),
        ("021-8877-6655", "02188776655"),
        ("021 8877 6655", "02188776655"),
        ("+98 21 8877 6655", "02188776655"),
        ("0098-21-88776655", "02188776655"),
        ("2188776655", "02188776655"),      # missing its leading 0
    ])
    def test_every_written_form_of_the_same_landline_normalizes_alike(self, raw, expected):
        assert normalize_phone(raw) == expected

    def test_the_canonical_landline_form_keeps_its_leading_zero(self):
        assert normalize_phone("02188776655").startswith("0")


class TestNormalizeEdgeCases:

    @pytest.mark.parametrize("raw", [None, "", "   ", "—", "no digits here"])
    def test_nothing_parseable_is_none(self, raw):
        assert normalize_phone(raw) is None

    def test_it_is_one_function_reused_by_every_model(self):
        """Not re-implemented per table — see app/models/lead.py,
        crm_models.py and property.py, which all import this same one."""
        import inspect
        for mod_name in ("app.models.lead", "app.models.crm_models", "app.models.property"):
            src = inspect.getsource(__import__(mod_name, fromlist=["_"]))
            assert "from app.models.phone import sync_phone_columns" in src, mod_name


class TestSyncPhoneColumnsListener:

    def test_it_copies_normalized_values_for_every_pair_given(self):
        class Target:
            phone_number = "09141234567"
            phone_number_normalized = None
        t = Target()
        sync_phone_columns(("phone_number", "phone_number_normalized"))(None, None, t)
        assert t.phone_number_normalized == "9141234567"

    def test_it_handles_more_than_one_pair_in_the_same_call(self):
        """Customer needs both mobile1 and mobile2 — one listener call, one
        registration, not two."""
        class Target:
            mobile1, mobile1_normalized = "09141234567", None
            mobile2, mobile2_normalized = "09141234568", None
        t = Target()
        sync_phone_columns(("mobile1", "mobile1_normalized"), ("mobile2", "mobile2_normalized"))(None, None, t)
        assert t.mobile1_normalized == "9141234567"
        assert t.mobile2_normalized == "9141234568"

    def test_a_blank_raw_value_clears_the_normalized_one(self):
        class Target:
            phone_number = ""
            phone_number_normalized = "9141234567"  # stale from a previous value
        t = Target()
        sync_phone_columns(("phone_number", "phone_number_normalized"))(None, None, t)
        assert t.phone_number_normalized is None


def _seed_and_fetch(model_name, create_kwargs, raw_attr, norm_attr):
    """Round-trip through a real session: insert, then read back the
    normalized column the ORM event should have filled."""
    async def _go():
        from app.database import async_session_maker

        if model_name == "Lead":
            from app.models.lead import Lead as M
            from app.models.property import Property
            async with async_session_maker() as db:
                prop = Property(tag_number=f"pn-{_RUN}-lead", divar_id=f"pn-{_RUN}-lead",
                                 title="x", url="https://divar.ir/v/pn-lead", is_active=True)
                db.add(prop)
                await db.flush()
                create_kwargs["property_id"] = prop.id
                row = M(**create_kwargs)
                db.add(row)
                await db.commit()
                await db.refresh(row)
                return getattr(row, norm_attr), row.id
        elif model_name == "Property":
            from app.models.property import Property as M
        elif model_name == "Contact":
            from app.models.crm_models import Contact as M
        elif model_name == "Customer":
            from app.models.crm_models import Customer as M
        else:
            raise ValueError(model_name)

        async with async_session_maker() as db:
            row = M(**create_kwargs)
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return getattr(row, norm_attr), row.id
    return asyncio.run(_go())


class TestTheEventFillsEveryModelOnInsert:

    def test_lead_phone_number(self):
        norm, _id = _seed_and_fetch(
            "Lead", {"phone_number": "+989141234568"}, "phone_number", "phone_number_normalized")
        assert norm == "9141234568"

    def test_property_phone_number(self):
        norm, _id = _seed_and_fetch(
            "Property",
            {"tag_number": f"pn-{_RUN}-prop", "divar_id": f"pn-{_RUN}-prop", "title": "x",
             "url": "https://divar.ir/v/pn-prop", "is_active": True, "phone_number": "0098 914 1234569"},
            "phone_number", "phone_number_normalized")
        assert norm == "9141234569"

    def test_property_owner_phone_has_no_normalized_twin(self):
        """owner_phone is which of OUR Divar accounts scraped the listing,
        never matched against a lead or a customer — see app/models/phone.py
        and property.py's comment on why it is excluded."""
        from app.models.property import Property
        assert not hasattr(Property, "owner_phone_normalized")

    def test_contact_phone(self):
        norm, _id = _seed_and_fetch(
            "Contact", {"name": "فروشنده", "phone": "۰۹۱۴۱۲۳۴۵۷۰"}, "phone", "phone_normalized")
        assert norm == "9141234570"

    def test_customer_mobile1_and_mobile2(self):
        async def _go():
            from app.database import async_session_maker
            from app.models.crm_models import Customer
            async with async_session_maker() as db:
                c = Customer(full_name="مشتری", mobile1="09141234571", mobile2="+989141234572")
                db.add(c)
                await db.commit()
                await db.refresh(c)
                return c.mobile1_normalized, c.mobile2_normalized
        m1, m2 = asyncio.run(_go())
        assert m1 == "9141234571"
        assert m2 == "9141234572"


class TestTheEventFillsOnUpdateToo:

    def test_changing_the_raw_column_updates_the_normalized_one(self):
        async def _go():
            from app.database import async_session_maker
            from app.models.crm_models import Contact
            async with async_session_maker() as db:
                c = Contact(name="اول", phone="09141234580")
                db.add(c)
                await db.commit()
                first = c.phone_normalized
                c.phone = "+989141234581"
                await db.commit()
                await db.refresh(c)
                return first, c.phone_normalized
        first, second = asyncio.run(_go())
        assert first == "9141234580"
        assert second == "9141234581"


class TestConvertLeadToDealReusesTheContactByNormalizedPhone:
    """The one raw-string equality lookup this phase found and fixed
    (app/api/routes/crm.py, convert_lead_to_deal). Needs the real app
    (crm.router requires the `crm` permission) — a full lifespan boot only
    works against Postgres, same reason test_digest.py's own client fixture
    skips on sqlite (_guard()'s SET lock_timeout is Postgres-only)."""

    def test_a_contact_typed_differently_is_still_found(self):
        import app.database as db
        if not str(db.engine.url).startswith("postgresql"):
            pytest.skip("needs Postgres — see test_digest.py's client fixture")

        import app.main as m
        from fastapi.testclient import TestClient
        with TestClient(m.app) as client:
            from app.database import async_session_maker
            from app.models.crm_models import Contact
            from app.models.property import Property
            from app.models.lead import Lead
            from app.models.user import User
            from app.auth.jwt import get_password_hash

            async def _seed():
                async with async_session_maker() as s:
                    s.add(User(username=f"cvt_{_RUN}", full_name="Convert Tester", role="super_admin",
                               hashed_password=get_password_hash("pw123456"), is_active=True))
                    contact = Contact(name="فروشنده قبلی", phone="09141234590")
                    s.add(contact)
                    prop = Property(tag_number=f"pn-{_RUN}-cvt", divar_id=f"pn-{_RUN}-cvt", title="x",
                                    url="https://divar.ir/v/pn-cvt", is_active=True)
                    s.add(prop)
                    await s.flush()
                    # the lead carries the SAME number, written the other way
                    lead = Lead(property_id=prop.id, phone_number="+989141234590", status="new")
                    s.add(lead)
                    await s.commit()
                    await s.refresh(contact)
                    await s.refresh(lead)
                    return contact.id, lead.id
            contact_id, lead_id = asyncio.run(_seed())

            tok = client.post("/api/users/token", data={"username": f"cvt_{_RUN}", "password": "pw123456"})
            assert tok.status_code == 200, tok.text
            headers = {"Authorization": f"Bearer {tok.json()['access_token']}"}

            r = client.post(f"/api/crm/leads/{lead_id}/convert-to-deal", headers=headers)
            assert r.status_code == 200, r.text
            assert r.json()["deal"]["seller_contact_id"] == contact_id, \
                "a differently-formatted but equal number must reuse the existing contact"

            async def _count_matching_contacts():
                # scoped to this test's own number: other tests in this file
                # write to the same table, so a whole-table count is not safe
                from sqlalchemy import select, func
                async with async_session_maker() as s:
                    return (await s.execute(select(func.count(Contact.id)).where(
                        Contact.phone_normalized == "9141234590"))).scalar()
            assert asyncio.run(_count_matching_contacts()) == 1, "no duplicate contact was created"
