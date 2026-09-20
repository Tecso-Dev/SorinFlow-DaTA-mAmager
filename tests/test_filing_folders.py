"""
کمد و زونکن as an explorer: folders inside binders, files dragged onto
the tree, a file edited from inside the box.

Before 2026-09-20 the shelf of spines pushed the files below the fold, so
clicking a binder looked like nothing happened; there was no way back to
the unfiled tray; and «اجاره» could not be split into یک‌خوابه / دوخوابه.
"""
import os
import sys
import asyncio
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_folders.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

ROOT = Path(__file__).resolve().parent.parent
JS = (ROOT / "frontend/js/app.js").read_text(encoding="utf-8")
HTML = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
CSS = (ROOT / "frontend/css/style.css").read_text(encoding="utf-8")
ROUTES = (ROOT / "app/api/routes/filing.py").read_text(encoding="utf-8")


class TestTheShape:

    def test_a_folder_is_a_binder_with_a_parent(self):
        from app.models.crm_models import Binder
        assert hasattr(Binder, "parent_id")
        db = (ROOT / "app/database.py").read_text(encoding="utf-8")
        assert "ALTER TABLE crm_binders ADD COLUMN IF NOT EXISTS parent_id INTEGER" in db
        assert "REFERENCES crm_binders(id) ON DELETE CASCADE" in db

    def test_one_level_only(self):
        fn = ROUTES[ROUTES.index("async def create_binder"):ROUTES.index("async def update_binder")]
        assert "if parent.parent_id:" in fn and "یک سطح کافی است" in fn
        assert "kind, deal = parent.kind, parent.deal_type" in fn, "a folder files what its binder files"

    def test_opening_a_binder_shows_its_folders_files_too(self):
        fn = ROUTES[ROUTES.index("async def list_files"):ROUTES.index("async def bulk_file_action")]
        assert "Property.binder_id.in_(await _binder_ids_within(db, binder_id))" in fn

    def test_deleting_a_binder_unfiles_the_folders_files_as_well(self):
        fn = ROUTES[ROUTES.index("async def delete_binder"):ROUTES.index("def _file_brief")]
        assert "_binder_ids_within(db, binder_id)" in fn and "await db.delete(folder)" in fn


class TestThePanel:

    def test_it_is_an_explorer_with_the_tree_beside_the_files(self):
        tab = HTML[HTML.index('id="crm-tab-filing"'):HTML.index('id="crm-tab-calendar"')]
        assert 'class="filing-explorer"' in tab and 'id="filing-tree"' in tab and 'id="filing-crumb"' in tab
        assert 'id="filing-new-folder-btn"' in tab and 'id="filing-select-all"' in tab
        assert "grid-template-columns: 250px minmax(0, 1fr)" in CSS
        assert "position: sticky" in CSS[CSS.index(".filing-tree {"):CSS.index(".filing-tree {") + 200]

    def test_the_tray_is_always_one_click_away(self):
        fn = JS[JS.index("function _renderTree"):JS.index("function _renderCrumb")]
        assert 'onclick="openBinder(null)"' in fn and "بدون زونکن" in fn
        assert 'data-drop="none"' in fn, "and a drop target"

    def test_files_are_dragged_onto_binders_folders_and_the_tray(self):
        blk = JS[JS.index("// ── drag & drop onto the tree"):JS.index("async function _moveFiles")]
        assert "_selectedFiles.has(id) ? [..._selectedFiles] : [id]" in blk, "a ticked card drags the whole selection"
        assert "el.addEventListener('drop'" in blk and "_moveFiles(_dragIds" in blk
        assert 'draggable="true"' in JS[JS.index("function _fileCard"):JS.index("function filterByTag")]
        assert ".drop-over {" in CSS

    def test_shift_click_selects_a_run_and_select_all_exists(self):
        blk = JS[JS.index("function onFileCardClick"):JS.index("function _updateFileBulkBar")]
        assert "e.shiftKey" in blk and "_filingShown.slice(lo, hi + 1)" in blk
        assert "function selectAllFiles" in blk

    def test_the_move_picker_lists_every_box_by_its_path(self):
        fn = JS[JS.index("function _binderChoices"):JS.index("function openBinder(")]
        assert "`${c.name} › ${b.name} › ${f.name}`" in fn
        assert "async function moveFilePick" in JS and "async function bulkMovePick" in JS
        assert 'onclick="bulkMovePick()"' in HTML
        # and the lead modal can file its property without leaving the lead
        assert "moveFilePick(${lead.property_id})" in JS

    def test_a_file_is_edited_from_inside_the_box(self):
        assert 'id="fileEditModal"' in HTML
        assert "apiCall(`/filing/files/${_fileEditId}`, { method: 'PATCH'" in JS
        assert 'onclick="openFileEdit(${f.id})"' in JS
        assert '@router.patch("/files/{property_id}")' in ROUTES and '@router.get("/files/{property_id}")' in ROUTES
        assert "if prop.is_private and not prop.created_by:" in ROUTES, "a file made private stays visible to its maker"

    def test_no_native_dialogs_in_the_block(self):
        blk = JS[JS.index("//  کمد و زونکن — filing"):JS.index("// ── جستجوی پیشرفته")]
        cleaned = blk.replace("askConfirm(", "").replace("askText(", "").replace("_askOpen(", "")
        for bad in ("prompt(", "confirm(", "alert("):
            assert bad not in cleaned

    def test_the_guide_explains_folders_and_dragging(self):
        fa = (ROOT / "README.fa.md").read_text(encoding="utf-8")
        assert "کمد ← زونکن ← پوشه" in fa and "رها کنید" in fa and "پوشهٔ جدید" in fa


class TestTwoOldBugs:
    """Found while testing this: «کمد جدید» never answered. Creating a cabinet
    touched the unloaded binders relationship on an async session
    (MissingGreenlet), and the maintenance middleware caught that, blamed
    itself, and ran the request a second time against a body that was already
    consumed — so the client waited forever."""

    def test_a_new_cabinet_does_not_touch_its_unloaded_binders(self):
        fn = ROUTES[ROUTES.index("async def create_cabinet"):ROUTES.index("async def update_cabinet")]
        assert "with_binders=True" not in fn
        assert '"binders": [], "file_count": 0' in fn

    def test_the_middleware_guards_only_its_own_check(self):
        main = (ROOT / "app/main.py").read_text(encoding="utf-8")
        fn = main[main.index("async def maintenance_middleware"):main.index("from app.services import maintenance as mt", main.index("async def maintenance_middleware"))]
        assert fn.count("await call_next(request)") == 1, "a route that raises must not be run twice"
        assert "allowed = await _maintenance_allows(request)" in fn


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


def _seed(username, n):
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.models.user import User
    from app.models.property import Property
    from app.auth.jwt import get_password_hash

    async def _go():
        eng = create_async_engine(os.environ["DATABASE_URL"])
        maker = async_sessionmaker(eng, expire_on_commit=False)
        ids = []
        try:
            async with maker() as s:
                s.add(User(username=username, full_name="مینا رضایی", role="admin",
                           permissions=["filing", "crm"], hashed_password=get_password_hash("pw123456"), is_active=True))
                await s.commit()
                for i in range(n):
                    p = Property(title=f"آپارتمان {60 + i} متری", tag_number=f"ff-{username}-{i}",
                                 divar_id=f"ff-{username}-{i}", url=f"https://divar.ir/v/ff-{i}",
                                 listing_type="rent", deposit=300_000_000, rent_price=9_000_000, area=60 + i, rooms=1)
                    s.add(p)
                    await s.flush()
                    ids.append(p.id)
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

    def test_folders_moves_and_an_edit(self, client):
        files = _seed("ff_mina", 4)
        h = _tok(client, "ff_mina")
        cab = client.post("/api/filing/cabinets", headers=h, json={"name": "اجاره"}).json()
        binder = client.post("/api/filing/binders", headers=h, json={"cabinet_id": cab["id"], "name": "گلها", "deal_type": "rent"}).json()
        one = client.post("/api/filing/binders", headers=h, json={"parent_id": binder["id"], "name": "یک‌خوابه"}).json()
        assert one["parent_id"] == binder["id"] and one["cabinet_id"] == cab["id"]
        assert one["deal_type"] == "rent", "inherited from the binder"
        r = client.post("/api/filing/binders", headers=h, json={"parent_id": one["id"], "name": "عمیق‌تر"})
        assert r.status_code == 400, "one level only"

        # two files straight into the binder, one into its folder
        assert client.post("/api/filing/files/bulk", headers=h, json={"ids": files[:2], "action": "move", "binder_id": binder["id"]}).json()["updated"] == 2
        assert client.post("/api/filing/files/bulk", headers=h, json={"ids": [files[2]], "action": "move", "binder_id": one["id"]}).json()["updated"] == 1

        tree = client.get("/api/filing/cabinets", headers=h).json()["items"]
        b = next(x for c in tree for x in c["binders"] if x["id"] == binder["id"])
        assert b["own_count"] == 2 and b["file_count"] == 3, "the spine counts its folders' files too"
        assert [f["name"] for f in b["folders"]] == ["یک‌خوابه"] and b["folders"][0]["file_count"] == 1
        assert one["id"] not in [x["id"] for c in tree for x in c["binders"]], "folders are not listed as binders"

        # opening the binder shows all three; opening the folder shows one; the tray shows the fourth
        assert client.get(f"/api/filing/files?binder_id={binder['id']}", headers=h).json()["total"] == 3
        assert client.get(f"/api/filing/files?binder_id={one['id']}", headers=h).json()["total"] == 1
        # CI runs every suite against one database, so the tray holds other tests' rows too
        tray = [x["id"] for x in client.get("/api/filing/files?unfiled=true&limit=300", headers=h).json()["items"]]
        assert files[3] in tray and not (set(files[:3]) & set(tray))

        # edit from inside the box — numbers arrive as the panel types them
        r = client.patch(f"/api/filing/files/{files[0]}", headers=h, json={
            "title": "آپارتمان ۶۰ متری — تمیز", "rooms": "2", "deposit": "350/000/000", "rent_price": "10,000,000",
            "tags": "فوری، تمیز", "binder_id": one["id"], "is_pinned": True})
        assert r.status_code == 200, r.text
        d = r.json()
        assert (d["rooms"], d["deposit"], d["rent_price"], d["tags"], d["binder_id"], d["is_pinned"]) == \
               (2, 350_000_000, 10_000_000, ["فوری", "تمیز"], one["id"], True)
        assert client.patch(f"/api/filing/files/{files[0]}", headers=h, json={"title": " "}).status_code == 400
        assert client.patch(f"/api/filing/files/{files[0]}", headers=h, json={"area": "abc"}).status_code == 400
        full = client.get(f"/api/filing/files/{files[0]}", headers=h).json()
        assert full["title"] == "آپارتمان ۶۰ متری — تمیز" and "description" in full

        # deleting the binder takes the folder with it and unfiles all three
        r = client.delete(f"/api/filing/binders/{binder['id']}", headers=h)
        assert r.status_code == 200 and r.json()["unfiled"] == 3
        tray = [x["id"] for x in client.get("/api/filing/files?unfiled=true&limit=300", headers=h).json()["items"]]
        assert set(files) <= set(tray)
        mine = next(c for c in client.get("/api/filing/cabinets", headers=h).json()["items"] if c["id"] == cab["id"])
        assert mine["binders"] == []
