"""
برچسب‌زن عکس — what is in the photos, not only how sharp they are.

The scraper puts the gallery on disk and nothing read it. The vision job
looks at the first three, tiny, and answers a fixed vocabulary; the answer
lives on the row with its prompt version and its model. Off the request
path, gated and metered by app/services/llm like every agent: no key, the
switch off, or the cap full ends a pass quietly; a listing the model could
not answer for is tried again later, and one with no photos is stamped once.
"""
import base64
import io
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_ai_photo.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from sqlalchemy import select  # noqa: E402

from app.ai import photo_tagger as pt  # noqa: E402
from app.models.property import Property  # noqa: E402
from app.services import llm, secret_box  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
_REAL_CLIENT = httpx.AsyncClient

GOOD = {"condition": "renovated", "furnished": True, "rooms_shown": ["kitchen", "living"],
        "exterior_shown": False, "floor_plan": False, "text_or_logo": False,
        "watermark": "املاک سورین", "quality": 4, "notes": "آشپزخانهٔ نوساز", "confidence": 0.8}


def _gateway(monkeypatch, handler):
    class Fake(_REAL_CLIENT):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **kw)
    monkeypatch.setattr(llm.httpx, "AsyncClient", Fake)


def _answer(content, cost=0.0002):
    return httpx.Response(200, json={
        "model": "test/model", "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 900, "completion_tokens": 60, "cost": cost, "total_cost_toman": 60}})


def _asked(req) -> str:
    """The listing's title, as the question carried it."""
    text = json.loads(req.read())["messages"][1]["content"][0]["text"]
    return text.split("\n")[0].split(": ", 1)[1]


@pytest.fixture
def configured(monkeypatch):
    """A gateway that exists and a ledger that records. The settings rows,
    the cursor and the cap use the real app_settings table on the test's
    own sqlite file — no stubs between the tagger and the gate."""
    monkeypatch.setattr(llm.settings, "llm_api_key", "k-test", raising=False)
    monkeypatch.setattr(llm.settings, "llm_base_url", "https://ai.liara.ir/api/6aa50e58b5e9e82406b93188/v1", raising=False)
    monkeypatch.setattr(llm.settings, "llm_model", "openai/gpt-4.1-mini", raising=False)
    monkeypatch.setattr(llm.settings, "llm_model_vision", "z-ai/glm-5.3-flash", raising=False)
    ledger = []

    async def record(agent, job, model, usage, ms, ok, error=""):
        ledger.append({"agent": agent, "job": job, "model": model, "ok": ok, "error": error})
    monkeypatch.setattr(llm, "_record", record)
    return {"ledger": ledger}


@pytest.fixture
async def db(tmp_path):
    """A session on a fresh sqlite file with the three tables the tagger
    touches: the listings, the settings (cursor, cap, switch), the ledger.
    Not the whole metadata — scraping_jobs carries a Postgres UUID."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from app.database import Base
    from app.models.ai_usage import AiUsage
    from app.models.app_setting import AppSetting
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'photo.db'}")
    tables = [Property.__table__, AppSetting.__table__, AiUsage.__table__]
    async with eng.begin() as c:
        await c.run_sync(lambda conn: Base.metadata.create_all(conn, tables=tables))
    session = async_sessionmaker(eng, expire_on_commit=False)()
    yield session
    await session.close()
    await eng.dispose()


def _jpeg(path: Path, size=(1600, 1200)):
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (200, 120, 80)).save(path, "JPEG", quality=85)


_n = [0]


def _prop(title="آپارتمان ۱۰۰ متری", divar_id=None, **kw):
    _n[0] += 1
    divar_id = divar_id or f"d{_n[0]}"
    base = dict(tag_number=f"T{_n[0]}", divar_id=divar_id, title=title, url=f"https://divar.ir/v/{divar_id}",
                images=[], has_images=False, images_downloaded=False, is_active=True)
    base.update(kw)
    return Property(**base)


def _with_photos(root: Path, divar_id: str, n=2, **kw):
    """A listing whose gallery is on disk, the way the scraper leaves it."""
    for i in range(n):
        _jpeg(root / divar_id / f"{i}.jpg")
    return _prop(divar_id=divar_id, images=[f"/images/{divar_id}/{i}.jpg" for i in range(n)],
                 has_images=True, images_downloaded=True, **kw)


class TestPreparingThePhotos:

    def test_small_rgb_jpegs_with_the_aspect_kept_and_the_missing_one_skipped(self, tmp_path):
        from PIL import Image
        root = tmp_path / "images"
        _jpeg(root / "abc" / "1.jpg", (1600, 1200))
        Image.new("RGBA", (300, 900), (0, 0, 255, 128)).save(root / "abc" / "2.png")
        p = _prop(images=["/images/abc/1.jpg", "/images/abc/missing.jpg", "/images/abc/2.png"])
        out = pt.prepare_images(p, root)
        assert len(out) == 2
        first, second = (Image.open(io.BytesIO(b)) for b in out)
        assert first.format == "JPEG" and first.mode == "RGB" and first.size == (512, 384)
        assert second.format == "JPEG" and second.mode == "RGB", "a PNG with alpha becomes a plain JPEG"
        assert max(second.size) == pt.MAX_SIDE and second.size[0] < second.size[1]
        assert len(out[0]) < (root / "abc" / "1.jpg").stat().st_size, "smaller than the download"

    def test_at_most_three_and_nothing_for_what_is_not_on_disk(self, tmp_path):
        root = tmp_path / "images"
        assert len(pt.prepare_images(_with_photos(root, "many", n=5), root)) == pt.MAX_PHOTOS == 3
        remote = _prop(images=["https://s100.divarcdn.com/static/photo/x.jpg",
                               "/images/../etc/passwd", "/other/a.jpg", "/images/abc"])
        assert pt.prepare_images(remote, root) == []
        assert pt.prepare_images(_prop(images=None), root) == []

    def test_a_corrupt_download_is_skipped_not_fatal(self, tmp_path):
        root = tmp_path / "images"
        p = _with_photos(root, "bad", n=2)
        (root / "bad" / "0.jpg").write_bytes(b"not a picture")
        assert len(pt.prepare_images(p, root)) == 1


class TestTheQuestion:

    def test_the_photos_travel_as_data_urls_and_the_title_is_masked(self):
        msgs = pt.build_messages(_prop(title="آپارتمان ۱۰۰ متری، تماس 09143495300"), [b"\xff\xd8x", b"\xff\xd8y"])
        assert msgs[0]["role"] == "system" and "JSON" in msgs[0]["content"]
        for word in ("renovated", "under_construction", "floor_plan", "text_or_logo", "watermark", "بازسازی"):
            assert word in msgs[0]["content"]
        text, *images = msgs[1]["content"]
        assert msgs[1]["role"] == "user" and text["type"] == "text"
        assert "09143495300" not in text["text"] and "آپارتمان ۱۰۰ متری" in text["text"]
        assert [i["type"] for i in images] == ["image_url", "image_url"]
        assert images[0]["image_url"]["url"] == "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8x").decode()


class TestTaggingOne:

    async def test_the_answer_is_stored_with_its_version_and_model(self, db, tmp_path, configured, monkeypatch):
        root = tmp_path / "images"
        p = _with_photos(root, "abc")
        db.add(p)
        await db.commit()
        seen = []

        def handler(req):
            seen.append(json.loads(req.read()))
            return _answer(json.dumps(GOOD))
        _gateway(monkeypatch, handler)

        tags = await pt.tag_property(db, p, images_root=root)
        assert tags == {**GOOD, "photos": 2, "prompt_version": pt.PROMPT_VERSION, "model": "test/model"}
        assert p.ai_photos_at is not None
        row = (await db.execute(select(Property.ai_photo_tags).where(Property.id == p.id))).scalar_one()
        assert row == tags, "on the row, not only in memory"
        body = seen[0]
        assert body["model"] == "z-ai/glm-5.3-flash", "the vision job's model"
        assert body["temperature"] == 0 and body["max_tokens"] == 300
        assert body["response_format"] == {"type": "json_object"}
        assert sum(1 for part in body["messages"][1]["content"] if part["type"] == "image_url") == 2
        assert configured["ledger"][-1]["agent"] == "vision" and configured["ledger"][-1]["job"] == "vision"

    async def test_no_photos_on_disk_is_a_stamp_not_a_call(self, db, tmp_path, configured, monkeypatch):
        p = _prop(images=["/images/gone/0.jpg"], has_images=True, images_downloaded=True)
        db.add(p)
        await db.commit()
        calls = []
        _gateway(monkeypatch, lambda r: calls.append(1) or _answer(json.dumps(GOOD)))
        assert await pt.tag_property(db, p, images_root=tmp_path / "images") is None
        assert p.ai_photo_tags == {"skipped": "no_photos", "prompt_version": pt.PROMPT_VERSION}
        assert p.ai_photos_at is not None
        assert calls == [] and configured["ledger"] == []

    async def test_an_answer_outside_the_vocabulary_stores_nothing(self, db, tmp_path, configured, monkeypatch):
        root = tmp_path / "images"
        p = _with_photos(root, "abc")
        db.add(p)
        await db.commit()
        calls = []

        def handler(req):
            calls.append(1)
            return _answer('{"condition": "palace", "quality": 9}')
        _gateway(monkeypatch, handler)
        assert await pt.tag_property(db, p, images_root=root) is None
        assert p.ai_photo_tags is None and p.ai_photos_at is None
        assert len(calls) == 2, "the gateway's one retry, then the refusal"
        assert [r["ok"] for r in configured["ledger"]] == [False, False]

    async def test_the_gate_comes_through_so_the_pass_can_stop(self, db, tmp_path, configured, monkeypatch):
        root = tmp_path / "images"
        p = _with_photos(root, "abc")
        db.add(p)
        await db.commit()
        await secret_box.put(db, llm.KEY_CAP, "0", "test")
        _gateway(monkeypatch, lambda r: _answer(json.dumps(GOOD)))
        with pytest.raises(llm.BudgetExceeded):
            await pt.tag_property(db, p, images_root=root)
        assert p.ai_photos_at is None


class TestThePass:

    async def test_the_cursor_moves_past_tags_and_stamps_only(self, db, tmp_path, configured, monkeypatch):
        """A: photos, fine. B: never had pictures — not even scanned. C: had
        pictures, none on disk — stamped. D: the gateway fails — retried next
        pass. E: fine, after D — tagged now, and never paid for again."""
        root = tmp_path / "images"
        monkeypatch.setattr(pt.settings, "images_path", str(root))
        a = _with_photos(root, "a", title="A")
        b = _prop(title="B")
        c = _prop(title="C", images=["/images/c/0.jpg"], has_images=True, images_downloaded=True)
        d = _with_photos(root, "d", title="D")
        e = _with_photos(root, "e", title="E")
        db.add_all([a, b, c, d, e])
        await db.commit()
        failing = {"D"}

        def handler(req):
            if _asked(req) in failing:
                return httpx.Response(500, json={"error": "boom"})
            return _answer(json.dumps(GOOD))
        _gateway(monkeypatch, handler)

        res = await pt.run_once(db)
        assert res == {"scanned": 4, "tagged": 2, "skipped": 1, "failed": 1, "cursor": c.id, "stopped": None}
        assert (await secret_box.get_many(db, (pt.KEY_CURSOR,)))[pt.KEY_CURSOR] == str(c.id)
        assert a.ai_photo_tags["condition"] == "renovated" and e.ai_photo_tags["condition"] == "renovated"
        assert c.ai_photo_tags == {"skipped": "no_photos", "prompt_version": pt.PROMPT_VERSION}
        assert b.ai_photos_at is None and d.ai_photos_at is None

        failing.clear()
        res = await pt.run_once(db)
        assert res == {"scanned": 1, "tagged": 1, "skipped": 0, "failed": 0, "cursor": d.id, "stopped": None}, \
            "D alone: E was done, and is not paid for twice"
        assert await pt.run_once(db) == {"scanned": 0, "tagged": 0, "skipped": 0, "failed": 0,
                                         "cursor": d.id, "stopped": None}

    async def test_the_cap_and_the_switch_end_the_pass_quietly(self, db, tmp_path, configured, monkeypatch):
        root = tmp_path / "images"
        monkeypatch.setattr(pt.settings, "images_path", str(root))
        a, b = _with_photos(root, "a"), _with_photos(root, "b")
        db.add_all([a, b])
        await db.commit()
        calls = []
        _gateway(monkeypatch, lambda r: calls.append(1) or _answer(json.dumps(GOOD)))

        await secret_box.put(db, llm.KEY_CAP, "0", "test")
        res = await pt.run_once(db)
        assert res == {"scanned": 2, "tagged": 0, "skipped": 0, "failed": 0, "cursor": 0, "stopped": "BudgetExceeded"}
        assert calls == [] and a.ai_photos_at is None and b.ai_photos_at is None
        assert pt.KEY_CURSOR not in await secret_box.get_many(db, (pt.KEY_CURSOR,)), "nothing was judged"

        await secret_box.put(db, llm.KEY_CAP, "5", "test")
        await secret_box.put(db, llm.KEY_ENABLED, "false", "test")
        assert (await pt.run_once(db))["stopped"] == "Disabled" and calls == []

    def test_it_runs_off_the_request_path_and_can_be_switched_off(self):
        src = (ROOT / "app/ai/photo_tagger.py").read_text(encoding="utf-8")
        assert 'getattr(settings, "match_engine", True)' in src and "asyncio.sleep(240)" in src
        assert "TICK_SECONDS = 300" in src and "BATCH = 30" in src
        assert "ZoneInfo" not in src, "the production image has no tz database"
        assert "async_session_maker()" in src[src.index("async def tick"):]


class TestTheLabels:

    def test_persian_chips_from_a_stored_answer(self):
        assert pt.tags_fa({**GOOD, "photos": 2, "model": "m"}) == [
            "بازسازی‌شده", "مبله", "فضاها: آشپزخانه، پذیرایی", "لوگوی مشاور: املاک سورین", "کیفیت ۴/۵"]
        more = pt.tags_fa({"condition": "old", "furnished": False, "exterior_shown": True,
                           "rooms_shown": ["exterior", "yard", "yard"], "floor_plan": True,
                           "text_or_logo": True, "quality": 2})
        assert more == ["قدیمی", "خالی", "نمای بیرونی", "فضاها: حیاط", "نقشه به‌جای عکس",
                        "تصویر متنی یا لوگو", "کیفیت ۲/۵"]
        assert pt.tags_fa({"skipped": "no_photos", "prompt_version": 1}) == ["عکسی روی دیسک نیست"]
        assert pt.tags_fa(None) == [] and pt.tags_fa({}) == []
        assert pt.tags_fa({"condition": "castle", "rooms_shown": ["hall"]}) == [], "unknown words are not chips"


class TestThePanelsSide:

    async def test_the_status_counts(self, db, tmp_path, configured):
        from app.api.routes import ai_photo
        now = datetime.now(timezone.utc)
        done = _prop(images=["/images/x/0.jpg"], has_images=True, ai_photo_tags={**GOOD, "photos": 1}, ai_photos_at=now)
        stamped = _prop(images=["/images/y/0.jpg"], has_images=True,
                        ai_photo_tags={"skipped": "no_photos", "prompt_version": 1}, ai_photos_at=now)
        waiting = _prop(images=["/images/z/0.jpg"], has_images=True)
        bare = _prop()
        db.add_all([done, stamped, waiting, bare])
        await db.commit()
        s = await ai_photo.photo_status(db=db, _=None)
        assert s["tagged"] == 1 and s["skipped"] == 1 and s["behind"] == 1 and s["cursor"] == 0
        assert s["version"] == pt.PROMPT_VERSION and s["model"] == "z-ai/glm-5.3-flash"
        assert s["configured"] is True and s["enabled"] is True and s["last_at"]

    async def test_one_listing_read_and_tagged_again(self, db, tmp_path, configured, monkeypatch):
        from fastapi import HTTPException
        from app.api.routes import ai_photo
        root = tmp_path / "images"
        monkeypatch.setattr(pt.settings, "images_path", str(root))
        p = _with_photos(root, "abc")
        db.add(p)
        await db.commit()
        me = SimpleNamespace(username="boss")

        assert await ai_photo.photo_tags(property_id=p.id, db=db, _=None) == \
            {"id": p.id, "tags": None, "labels": [], "tagged_at": None}
        with pytest.raises(HTTPException) as e:
            await ai_photo.photo_tags(property_id=p.id + 99, db=db, _=None)
        assert e.value.status_code == 404
        with pytest.raises(HTTPException) as e:
            await ai_photo.photo_retag(property_id=p.id + 99, db=db, user=me)
        assert e.value.status_code == 404

        _gateway(monkeypatch, lambda r: _answer(json.dumps(GOOD)))
        got = await ai_photo.photo_retag(property_id=p.id, db=db, user=me)
        assert got["labels"][0] == "بازسازی‌شده" and got["tags"]["model"] == "test/model" and got["tagged_at"]

        _gateway(monkeypatch, lambda r: httpx.Response(500, json={"error": "boom"}))
        with pytest.raises(HTTPException) as e:
            await ai_photo.photo_retag(property_id=p.id, db=db, user=me)
        assert e.value.status_code == 502 and "مدل" in e.value.detail
        assert p.ai_photo_tags["condition"] == "renovated", "the old answer stays"

        await secret_box.put(db, llm.KEY_ENABLED, "false", "test")
        with pytest.raises(HTTPException) as e:
            await ai_photo.photo_retag(property_id=p.id, db=db, user=me)
        assert e.value.status_code == 502 and "خاموش" in e.value.detail

    def test_the_routes_are_for_the_two_top_roles_and_in_the_right_order(self):
        src = (ROOT / "app/api/routes/ai_photo.py").read_text(encoding="utf-8")
        assert '_role_dep("root", "super_admin")' in src
        assert src.count("_super_admin") >= 5, "status, run, read, retag"
        assert src.index('"/status"') < src.index('"/{property_id}"'), "«status» is not a listing id"
        assert src.index('"/run"') < src.index('"/{property_id}"')

    def test_the_columns_and_the_dict(self):
        assert "ai_photo_tags" in Property.__table__.c and "ai_photos_at" in Property.__table__.c
        d = _prop().to_dict()
        assert d["ai_photo_tags"] is None and d["ai_photos_at"] is None

    def test_the_panel_script(self):
        js = (ROOT / "frontend/js/ai/photo.js").read_text(encoding="utf-8")
        for fn in ("function aiRenderPhotoTags", "async function aiLoadPhotoTags",
                   "async function aiRetagPhoto", "async function aiLoadPhotoStatus"):
            assert fn in js
        assert "apiCall(`/ai/photo/${propertyId}`, { method: 'POST' })" in js
        assert "apiCall('/ai/photo/status')" in js and "برچسب‌زنی دوباره" in js
        for bad in ("prompt(", "confirm(", "alert(", "--bs-"):
            assert bad not in js
