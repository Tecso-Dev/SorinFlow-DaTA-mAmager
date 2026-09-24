"""
Manual-lead photo URLs, and the ~25 CRM/filing endpoints that used to take a
raw `dict` body.

/crm/upload-image is the only place a client can put a file on disk under
data/images/manual, and it always names the result uuid4().hex + ".jpg". Any
photo URL a client hands back for storage must match that shape exactly —
anything else could point the panel's <img src> anywhere the caller likes.

The dict-bodied write endpoints in crm.py and filing.py now take a Pydantic
model instead, so a wrong type or an absurd length answers 422 instead of
either crashing on write (see the FILE_TEXT truncation bug this also closes)
or silently storing whatever came in. These tests check, per group: one call
that still behaves as before, and one malformed call that now gets refused.

Runs against Postgres — see tests/test_divar_numbers_are_personal.py for why
(the client fixture is copied from there almost verbatim).
"""
import asyncio
import io
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_input_validation.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")


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


def _engine():
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    eng = create_async_engine(os.environ["DATABASE_URL"])
    return eng, async_sessionmaker(eng, expire_on_commit=False)


@pytest.fixture(scope="module")
def root_headers(client):
    """One root account — root bypasses every permission check, so this one
    user reaches every /crm and /filing route under test."""
    from app.models.user import User
    from app.auth.jwt import get_password_hash

    async def _go():
        eng, maker = _engine()
        try:
            async with maker() as s:
                s.add(User(username="iv_root", full_name="ولیداتور", role="root",
                           permissions=[], phone="09120000901", phone_verified=True,
                           hashed_password=get_password_hash("pw123456"), is_active=True))
                await s.commit()
        finally:
            await eng.dispose()
    asyncio.run(_go())
    r = client.post("/api/users/token", data={"username": "iv_root", "password": "pw123456"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# ─────────────────────────────────────────────────────────────────────────
# 1. manual lead photos
# ─────────────────────────────────────────────────────────────────────────

def _fake_jpeg_bytes():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), color=(200, 40, 40)).save(buf, format="JPEG")
    buf.seek(0)
    return buf


class TestManualLeadPhotos:

    def test_upload_names_the_file_32_hex_plus_jpg(self, client, root_headers):
        r = client.post("/api/crm/upload-image",
                        files={"file": ("photo.png", _fake_jpeg_bytes(), "image/png")},
                        headers=root_headers)
        assert r.status_code == 200, r.text
        url = r.json()["url"]
        import re
        assert re.fullmatch(r"/images/manual/[0-9a-f]{32}\.jpg", url), url
        self.uploaded_url = url

    def test_a_real_uploaded_url_is_accepted_on_the_lead(self, client, root_headers):
        up = client.post("/api/crm/upload-image",
                         files={"file": ("photo.png", _fake_jpeg_bytes(), "image/png")},
                         headers=root_headers)
        url = up.json()["url"]
        r = client.post("/api/crm/leads", headers=root_headers, json={
            "property_title": "فایل تستی با عکس", "images": [url],
        })
        assert r.status_code == 200, r.text
        lead_id = r.json()["id"]
        got = client.get(f"/api/crm/leads/{lead_id}", headers=root_headers).json()
        assert got["property_detail"]["images"] == [url]

    @pytest.mark.parametrize("bad", [
        "/images/manual/not-hex-not-32-chars.jpg",     # wrong shape entirely
        "/images/manual/" + "a" * 31 + ".jpg",           # 31 hex, not 32
        "/images/manual/" + "g" * 32 + ".jpg",           # not hex (g)
        "/images/manual/" + "a" * 32 + ".png",           # wrong extension
        "/images/other/" + "a" * 32 + ".jpg",            # wrong directory
        "https://evil.example/" + "a" * 32 + ".jpg",     # absolute URL
        "/images/manual/../../etc/passwd",               # traversal attempt
    ])
    def test_anything_else_is_refused(self, client, root_headers, bad):
        r = client.post("/api/crm/leads", headers=root_headers, json={
            "property_title": "فایل تستی", "images": [bad],
        })
        assert r.status_code == 422, r.text


# ─────────────────────────────────────────────────────────────────────────
# 2. the dict → Pydantic endpoints
# ─────────────────────────────────────────────────────────────────────────

class TestLeadsBulk:
    def test_valid_bulk_status_change_still_works(self, client, root_headers):
        lead = client.post("/api/crm/leads", headers=root_headers,
                           json={"property_title": "لید گروهی"}).json()
        r = client.post("/api/crm/leads/bulk", headers=root_headers, json={
            "ids": [lead["id"]], "action": "status", "status": "contacted"})
        assert r.status_code == 200 and r.json()["updated"] == 1, r.text

    def test_a_non_list_ids_is_refused(self, client, root_headers):
        r = client.post("/api/crm/leads/bulk", headers=root_headers,
                        json={"ids": "1,2,3", "action": "status", "status": "contacted"})
        assert r.status_code == 422, r.text


class TestContacts:
    def test_valid_contact_round_trips(self, client, root_headers):
        r = client.post("/api/crm/contacts", headers=root_headers, json={
            "name": "علی رضایی", "phone": "09121234567", "tags": ["مالک", "ویژه"]})
        assert r.status_code == 200, r.text
        assert r.json()["name"] == "علی رضایی"

    def test_a_name_past_the_column_width_is_refused(self, client, root_headers):
        r = client.post("/api/crm/contacts", headers=root_headers,
                        json={"name": "خ" * 500})
        assert r.status_code == 422, r.text


class TestCustomers:
    def test_valid_customer_round_trips(self, client, root_headers):
        # desired_type set on purpose: a customer with no type/city at all is
        # an unconstrained match for any property (see match_service.customer_
        # _wants) and test_match_engine.py shares this Postgres database.
        r = client.post("/api/crm/customers", headers=root_headers, json={
            "full_name": "سارا محمدی", "budget_max": 5_000_000_000,
            "desired_type": "apartment", "desired_city": "شهر تست ورودی",
            "showings": [{"file_code": "12", "description": "دیدن اول"}]})
        assert r.status_code == 200, r.text
        assert r.json()["full_name"] == "سارا محمدی"

    def test_a_non_numeric_budget_is_refused(self, client, root_headers):
        r = client.post("/api/crm/customers", headers=root_headers,
                        json={"full_name": "تست", "budget_max": "خیلی زیاد"})
        assert r.status_code == 422, r.text


class TestDpa:
    def test_valid_dpa_round_trips(self, client, root_headers):
        r = client.post("/api/crm/dpa", headers=root_headers, json={
            "agent_name": "مشاور ۱", "new_files": 3, "base_tasks": {"call_20": True}})
        assert r.status_code == 200, r.text
        assert r.json()["new_files"] == 3

    def test_a_non_numeric_count_is_refused(self, client, root_headers):
        r = client.post("/api/crm/dpa", headers=root_headers,
                        json={"agent_name": "م", "new_files": "زیاد"})
        assert r.status_code == 422, r.text


class TestNotes:
    def test_valid_note_round_trips(self, client, root_headers):
        r = client.post("/api/crm/notes", headers=root_headers, json={"content": "یادداشت تستی"})
        assert r.status_code == 200 and r.json()["content"] == "یادداشت تستی", r.text
        note_id = r.json()["id"]
        r2 = client.put(f"/api/crm/notes/{note_id}", headers=root_headers,
                        json={"content": "ویرایش شد"})
        assert r2.status_code == 200 and r2.json()["content"] == "ویرایش شد"

    def test_content_must_be_text(self, client, root_headers):
        r = client.post("/api/crm/notes", headers=root_headers, json={"content": {"a": 1}})
        assert r.status_code == 422, r.text


class TestTasks:
    def test_valid_task_round_trips(self, client, root_headers):
        r = client.post("/api/crm/tasks", headers=root_headers, json={"title": "پیگیری تستی"})
        assert r.status_code == 200, r.text
        task_id = r.json()["id"]
        r2 = client.patch(f"/api/crm/tasks/{task_id}/status", headers=root_headers,
                          json={"status": "done"})
        assert r2.status_code == 200 and r2.json()["status"] == "done"

    def test_a_non_numeric_contact_id_is_refused(self, client, root_headers):
        r = client.post("/api/crm/tasks", headers=root_headers,
                        json={"title": "ت", "contact_id": "abc"})
        assert r.status_code == 422, r.text


class TestDeals:
    def test_valid_deal_round_trips(self, client, root_headers):
        r = client.post("/api/crm/deals", headers=root_headers,
                        json={"title": "معاملهٔ تستی", "amount": 1_500_000_000})
        assert r.status_code == 200 and r.json()["title"] == "معاملهٔ تستی", r.text

    def test_a_non_numeric_amount_is_refused(self, client, root_headers):
        r = client.post("/api/crm/deals", headers=root_headers,
                        json={"title": "د", "amount": "نامعلوم"})
        assert r.status_code == 422, r.text


class TestReminders:
    def test_valid_reminder_round_trips(self, client, root_headers):
        when = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        r = client.post("/api/crm/reminders", headers=root_headers,
                        json={"title": "یادآور تستی", "remind_at": when})
        assert r.status_code == 200 and r.json()["title"] == "یادآور تستی", r.text

    def test_a_non_numeric_contact_id_is_refused(self, client, root_headers):
        when = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        r = client.post("/api/crm/reminders", headers=root_headers,
                        json={"title": "ی", "remind_at": when, "contact_id": "abc"})
        assert r.status_code == 422, r.text


class TestSms:
    def test_valid_send_still_reaches_the_service(self, client, root_headers):
        # no provider key is configured in this environment, so delivery
        # itself fails — the point here is that a well-formed body still
        # reaches send_sms() and gets logged, exactly as before.
        r = client.post("/api/crm/sms/send", headers=root_headers,
                        json={"to_number": "09121234567", "message": "سلام"})
        assert r.status_code == 200 and "log_id" in r.json(), r.text

    def test_a_non_string_message_is_refused(self, client, root_headers):
        r = client.post("/api/crm/sms/send", headers=root_headers,
                        json={"to_number": "09121234567", "message": 12345})
        assert r.status_code == 422, r.text


class TestCalendar:
    def test_valid_event_round_trips(self, client, root_headers):
        start = datetime.now(timezone.utc).isoformat()
        r = client.post("/api/crm/calendar", headers=root_headers,
                        json={"title": "بازدید تستی", "start_at": start})
        assert r.status_code == 200 and r.json()["title"] == "بازدید تستی", r.text
        event_id = r.json()["id"]
        r2 = client.patch(f"/api/crm/calendar/{event_id}", headers=root_headers,
                          json={"location": "آدرس تستی"})
        assert r2.status_code == 200 and r2.json()["location"] == "آدرس تستی"

    def test_a_non_numeric_remind_before_is_refused(self, client, root_headers):
        start = datetime.now(timezone.utc).isoformat()
        r = client.post("/api/crm/calendar", headers=root_headers,
                        json={"title": "ب", "start_at": start, "remind_before": "زیاد"})
        assert r.status_code == 422, r.text

    def test_calendar_sms_rejects_a_non_string_to(self, client, root_headers):
        start = datetime.now(timezone.utc).isoformat()
        ev = client.post("/api/crm/calendar", headers=root_headers,
                         json={"title": "ب", "start_at": start}).json()
        r = client.post(f"/api/crm/calendar/{ev['id']}/sms", headers=root_headers,
                        json={"to": 12345})
        assert r.status_code == 422, r.text


class TestCabinetsAndBinders:
    def test_valid_cabinet_and_binder_round_trip(self, client, root_headers):
        cab = client.post("/api/filing/cabinets", headers=root_headers,
                          json={"name": "کمد تستی"})
        assert cab.status_code == 200, cab.text
        cab_id = cab.json()["id"]
        binder = client.post("/api/filing/binders", headers=root_headers,
                             json={"name": "زونکن تستی", "cabinet_id": cab_id})
        assert binder.status_code == 200 and binder.json()["name"] == "زونکن تستی", binder.text

    def test_a_cabinet_name_past_the_column_width_is_refused(self, client, root_headers):
        r = client.post("/api/filing/cabinets", headers=root_headers, json={"name": "ک" * 200})
        assert r.status_code == 422, r.text

    def test_a_non_numeric_cabinet_id_on_a_binder_is_refused(self, client, root_headers):
        r = client.post("/api/filing/binders", headers=root_headers,
                        json={"name": "ز", "cabinet_id": "abc"})
        assert r.status_code == 422, r.text


class TestFilesBulkAndUpdate:
    def test_valid_bulk_pin_and_field_edit_still_work(self, client, root_headers):
        lead = client.post("/api/crm/leads", headers=root_headers,
                           json={"property_title": "فایل بایگانی تستی"}).json()
        prop_id = lead["property_id"]
        r = client.post("/api/filing/files/bulk", headers=root_headers,
                        json={"ids": [prop_id], "action": "pin"})
        assert r.status_code == 200 and r.json()["updated"] == 1, r.text
        r2 = client.patch(f"/api/filing/files/{prop_id}", headers=root_headers,
                          json={"title": "عنوان ویرایش‌شده"})
        assert r2.status_code == 200 and r2.json()["title"] == "عنوان ویرایش‌شده", r2.text

    def test_a_non_list_ids_on_bulk_is_refused(self, client, root_headers):
        r = client.post("/api/filing/files/bulk", headers=root_headers,
                        json={"ids": "1", "action": "pin"})
        assert r.status_code == 422, r.text

    def test_a_corner_type_past_its_real_column_width_is_refused(self, client, root_headers):
        """corner_type is String(20) on the model; the old handler truncated
        every FILE_TEXT field to 500 chars regardless of its own column, so
        this used to reach Postgres and 500 instead of a clean 422."""
        lead = client.post("/api/crm/leads", headers=root_headers,
                           json={"property_title": "فایل تستی دیگر"}).json()
        r = client.patch(f"/api/filing/files/{lead['property_id']}", headers=root_headers,
                         json={"corner_type": "خ" * 25})
        assert r.status_code == 422, r.text
