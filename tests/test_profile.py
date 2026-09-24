"""
The profile page's backend, exercised against the real app.

Written as the things that must hold rather than as feature demos: a changed
password signs the other devices out, a typo'd email cannot replace a working
one, a session belongs to whoever answered Divar's code and to nobody else,
and the forwarder is its own permission.

Needs Postgres + Redis like test_auth_roles.py (skips without).
"""
import io
import os
import re
import sys
import asyncio

import pytest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_profile.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module")
def client():
    import fakeredis.aioredis
    import app.database as db
    from app.config import get_settings

    if not str(db.engine.url).startswith("postgresql"):
        pytest.skip("needs Postgres — see test_auth_roles.py", allow_module_level=True)

    cfg = get_settings()
    saved = (cfg.environment, cfg.api_key, cfg.images_path, cfg.cookies_path)
    cfg.environment = "test"
    cfg.api_key = ""
    cfg.images_path = "/tmp/sorinflow-test-images"
    cfg.cookies_path = "/tmp/sorinflow-test-cookies"   # DivarAuth mkdirs it

    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)

    async def _get_redis():
        return fake
    db.get_redis = _get_redis
    import app.services.verification as v
    v.get_redis = _get_redis

    from fastapi.testclient import TestClient
    import app.main as m

    with TestClient(m.app) as c:
        c.fake_redis = fake
        yield c

    (cfg.environment, cfg.api_key, cfg.images_path, cfg.cookies_path) = saved


def _in_fresh_loop(coro_factory):
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

    async def _go():
        eng = create_async_engine(os.environ["DATABASE_URL"])
        maker = async_sessionmaker(eng, expire_on_commit=False)
        try:
            return await coro_factory(maker)
        finally:
            await eng.dispose()
    return asyncio.run(_go())


def _mk_user(username, role, permissions=None, password="pw123456", **kw):
    from app.models.user import User
    from app.auth.jwt import get_password_hash

    async def _go(maker):
        async with maker() as s:
            u = User(username=username, full_name=username, role=role,
                     hashed_password=get_password_hash(password),
                     permissions=permissions or [], is_active=True, **kw)
            s.add(u)
            await s.commit()
            await s.refresh(u)
            return u.id
    return _in_fresh_loop(_go)


def _mk_cookie(phone, owner_id):
    from app.models.cookie import Cookie

    async def _go(maker):
        async with maker() as s:
            c = Cookie(phone_number=phone, cookies=[{"name": "token", "value": "x"}],
                       is_valid=True, owner_user_id=owner_id)
            s.add(c)
            await s.commit()
            await s.refresh(c)
            return c.id
    return _in_fresh_loop(_go)


def _token(client, username, password="pw123456"):
    r = client.post("/api/users/token", data={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _auth(tok):
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture
def outbox(monkeypatch):
    """Every email the app tries to send, captured. Nothing leaves."""
    sent = []

    async def fake_send(to, subject, html, text="", db=None, cfg=None):
        sent.append({"to": to, "subject": subject, "text": text})
        return {"success": True}
    import app.services.email_service as es
    monkeypatch.setattr(es, "send", fake_send)
    return sent


# ── who I am ──────────────────────────────────────────────────────────────────

class TestEditingMyself:

    def test_the_profile_fields_round_trip(self, client):
        _mk_user("pf_edit", "admin", permissions=["crm"])
        tok = _token(client, "pf_edit")
        r = client.patch("/api/users/me", headers=_auth(tok), json={
            "full_name": "سارا احمدی", "headline": "مشاور فروش", "bio": "ده سال در بازار مسکن",
            "presence": "busy",
            "links": {"website": "https://sara.ir", "instagram": "@sara.ahmadi", "linkedin": ""},
        })
        assert r.status_code == 200, r.text
        u = r.json()["user"]
        assert u["full_name"] == "سارا احمدی" and u["headline"] == "مشاور فروش"
        assert u["presence"] == "busy"
        # a handle is stored as the URL it means, an empty link is dropped
        assert u["links"] == {"website": "https://sara.ir",
                              "instagram": "https://instagram.com/sara.ahmadi"}
        assert r.json()["access_token"] is None, "no rename, no new token"
        me = client.get("/api/users/me", headers=_auth(tok)).json()
        assert me["bio"] == "ده سال در بازار مسکن"

    def test_a_link_that_is_not_https_is_refused(self, client):
        _mk_user("pf_link", "admin")
        tok = _token(client, "pf_link")
        r = client.patch("/api/users/me", headers=_auth(tok),
                         json={"links": {"website": "javascript:alert(1)"}})
        assert r.status_code == 400

    def test_presence_is_one_of_three(self, client):
        _mk_user("pf_pres", "admin")
        tok = _token(client, "pf_pres")
        assert client.patch("/api/users/me", headers=_auth(tok),
                            json={"presence": "online"}).status_code == 400

    def test_renaming_comes_with_a_token_that_works(self, client):
        """The token names the user by username; after a rename the old one
        stops resolving on the very next request."""
        _mk_user("pf_old_name", "admin")
        tok = _token(client, "pf_old_name")
        r = client.patch("/api/users/me", headers=_auth(tok), json={"username": "pf_new_name"})
        assert r.status_code == 200, r.text
        fresh = r.json()["access_token"]
        assert fresh
        assert client.get("/api/users/me", headers=_auth(fresh)).json()["username"] == "pf_new_name"
        assert client.get("/api/users/me", headers=_auth(tok)).status_code == 401

    def test_a_taken_username_is_a_409_not_a_500(self, client):
        _mk_user("pf_taken", "admin")
        _mk_user("pf_wants_it", "admin")
        tok = _token(client, "pf_wants_it")
        r = client.patch("/api/users/me", headers=_auth(tok), json={"username": "PF_TAKEN"})
        assert r.status_code == 409


# ── password ──────────────────────────────────────────────────────────────────

class TestChangingMyPassword:

    def test_other_devices_are_signed_out_and_this_one_stays(self, client):
        _mk_user("pf_pw", "admin")
        other_device = _token(client, "pf_pw")
        this_device = _token(client, "pf_pw")
        r = client.post("/api/users/me/password", headers=_auth(this_device),
                        json={"current_password": "pw123456", "new_password": "brand-new-pw-9"})
        assert r.status_code == 200, r.text
        fresh = r.json()["access_token"]
        # every token minted before the change is dead — including the one
        # that made the change; the response carries its replacement
        assert client.get("/api/users/me", headers=_auth(other_device)).status_code == 401
        assert client.get("/api/users/me", headers=_auth(this_device)).status_code == 401
        assert client.get("/api/users/me", headers=_auth(fresh)).status_code == 200
        # and the new password is the one that logs in
        assert client.post("/api/users/token", data={"username": "pf_pw", "password": "pw123456"}).status_code == 401
        assert _token(client, "pf_pw", "brand-new-pw-9")

    def test_the_current_password_is_required(self, client):
        _mk_user("pf_pw2", "admin")
        tok = _token(client, "pf_pw2")
        r = client.post("/api/users/me/password", headers=_auth(tok),
                        json={"current_password": "nope-nope", "new_password": "brand-new-pw-9"})
        assert r.status_code == 400
        assert client.get("/api/users/me", headers=_auth(tok)).status_code == 200, "a failed attempt must not sign anyone out"


# ── email ─────────────────────────────────────────────────────────────────────

def _code_in(outbox):
    return re.search(r"(\d{4,8})", outbox[-1]["text"]).group(1)


class TestVerifyingMyEmail:

    def test_a_new_address_replaces_the_old_only_once_proven(self, client, outbox):
        _mk_user("pf_mail", "admin", email="old@example.com", email_verified=True)
        tok = _token(client, "pf_mail")
        r = client.post("/api/users/me/email/request", headers=_auth(tok),
                        json={"email": "new@example.com"})
        assert r.status_code == 200, r.text
        assert outbox[-1]["to"] == "new@example.com"
        assert "ورود" not in outbox[-1]["subject"], "a verification code must not arrive as a «login code»"
        # nothing has changed yet — a typo here must not replace a working address
        me = client.get("/api/users/me", headers=_auth(tok)).json()
        assert me["email"] == "old@example.com" and me["email_verified"] is True

        r = client.post("/api/users/me/email/verify", headers=_auth(tok), json={"code": "000000"})
        assert r.status_code == 400
        r = client.post("/api/users/me/email/verify", headers=_auth(tok), json={"code": _code_in(outbox)})
        assert r.status_code == 200, r.text
        me = client.get("/api/users/me", headers=_auth(tok)).json()
        assert me["email"] == "new@example.com" and me["email_verified"] is True

    def test_the_current_address_can_be_verified_in_place(self, client, outbox):
        _mk_user("pf_mail2", "admin", email="mine@example.com", email_verified=False)
        tok = _token(client, "pf_mail2")
        assert client.post("/api/users/me/email/request", headers=_auth(tok), json={}).status_code == 200
        assert outbox[-1]["to"] == "mine@example.com"
        r = client.post("/api/users/me/email/verify", headers=_auth(tok), json={"code": _code_in(outbox)})
        assert r.status_code == 200
        assert client.get("/api/users/me", headers=_auth(tok)).json()["email_verified"] is True

    def test_somebody_elses_address_is_refused(self, client, outbox):
        _mk_user("pf_mail3", "admin", email="taken@example.com")
        _mk_user("pf_mail4", "admin")
        tok = _token(client, "pf_mail4")
        r = client.post("/api/users/me/email/request", headers=_auth(tok), json={"email": "Taken@example.com"})
        assert r.status_code == 409
        assert not outbox


# ── avatar ────────────────────────────────────────────────────────────────────

class TestMyPicture:

    def test_whatever_is_uploaded_becomes_a_square_jpeg(self, client):
        from PIL import Image
        _mk_user("pf_pic", "admin")
        tok = _token(client, "pf_pic")
        buf = io.BytesIO()
        Image.new("RGB", (1200, 600), (200, 30, 30)).save(buf, "PNG")
        r = client.post("/api/users/me/avatar", headers=_auth(tok),
                        files={"file": ("me.png", buf.getvalue(), "image/png")})
        assert r.status_code == 200, r.text
        url = r.json()["avatar_url"]
        assert url and url.startswith("/images/avatars/") and url.endswith(".jpg")
        path = "/tmp/sorinflow-test-images/avatars/" + url.rsplit("/", 1)[1]
        assert os.path.exists(path)
        with Image.open(path) as im:
            assert im.format == "JPEG" and im.size == (400, 400)
        assert client.get("/api/users/me", headers=_auth(tok)).json()["avatar_url"] == url

        # a second upload is a new URL, and the old file is gone
        r2 = client.post("/api/users/me/avatar", headers=_auth(tok),
                         files={"file": ("me.png", buf.getvalue(), "image/png")})
        assert r2.json()["avatar_url"] != url
        assert not os.path.exists(path)

        assert client.delete("/api/users/me/avatar", headers=_auth(tok)).status_code == 200
        assert client.get("/api/users/me", headers=_auth(tok)).json()["avatar_url"] is None

    def test_a_file_that_is_not_an_image_is_refused(self, client):
        _mk_user("pf_pic2", "admin")
        tok = _token(client, "pf_pic2")
        r = client.post("/api/users/me/avatar", headers=_auth(tok),
                        files={"file": ("me.png", b"<html>not a picture</html>", "image/png")})
        assert r.status_code == 400


# ── admin asks somebody to verify ─────────────────────────────────────────────

class TestAskingSomebodyToVerify:

    def test_the_owner_is_told_where_to_go_once_an_hour(self, client, outbox):
        target = _mk_user("pf_nudge_t", "admin", email="lazy@example.com", email_verified=False)
        _mk_user("pf_nudge_sa", "super_admin")
        tok = _token(client, "pf_nudge_sa")
        r = client.post(f"/api/users/{target}/verification-request", headers=_auth(tok))
        assert r.status_code == 200, r.text
        assert r.json()["sent"]["email"] is True
        assert outbox[-1]["to"] == "lazy@example.com"
        assert "#/profile" in outbox[-1]["text"]
        assert not re.search(r"\b\d{6}\b", outbox[-1]["text"]), "a 3-minute code in a nudge is dead on arrival"
        # once an hour
        assert client.post(f"/api/users/{target}/verification-request", headers=_auth(tok)).status_code == 429

    def test_nothing_left_to_verify_is_said_so(self, client, outbox):
        target = _mk_user("pf_nudge_done", "admin", email="done@example.com", email_verified=True)
        tok = _token(client, "pf_nudge_sa")
        assert client.post(f"/api/users/{target}/verification-request", headers=_auth(tok)).status_code == 400

    def test_an_admin_may_not(self, client, outbox):
        target = _mk_user("pf_nudge_t2", "admin", email="lazy2@example.com")
        _mk_user("pf_nudge_adm", "admin", permissions=["crm"])
        tok = _token(client, "pf_nudge_adm")
        assert client.post(f"/api/users/{target}/verification-request", headers=_auth(tok)).status_code == 403


# ── the forwarder is its own permission ───────────────────────────────────────

class TestTheForwarderPermission:

    def test_it_is_in_the_catalog(self):
        from app.auth.permissions import PERMISSIONS
        assert PERMISSIONS["forwarder"] == "فرستندهٔ پیامک"
        assert "sms" in PERMISSIONS, "the SMS panel stays a separate key"

    def test_divar_auth_alone_no_longer_opens_it(self, client):
        _mk_user("pf_fw_divar", "admin", permissions=["divar_auth"])
        tok = _token(client, "pf_fw_divar")
        assert client.get("/api/forwarder/devices", headers=_auth(tok)).status_code == 403

    def test_the_key_does(self, client):
        _mk_user("pf_fw_yes", "admin", permissions=["forwarder"])
        tok = _token(client, "pf_fw_yes")
        assert client.get("/api/forwarder/devices", headers=_auth(tok)).status_code == 200


# ── a session is usable by its owner only ─────────────────────────────────────

class TestSessionsAreNotShared:

    def test_root_does_not_get_somebody_elses_session_as_its_active_one(self, client):
        """super_admin sees every session in the list — and used to have any
        of them handed back as «the active cookie»."""
        owner = _mk_user("pf_sess_owner", "admin", permissions=["divar_auth"], divar_phone="09121110001")
        _mk_cookie("09121110001", owner)
        _mk_user("pf_sess_sa", "super_admin", divar_phone="09121110001")   # points at a number that is not theirs
        tok = _token(client, "pf_sess_sa")
        st = client.get("/api/auth/status", headers=_auth(tok)).json()
        assert st["phone_number"] != "09121110001" and not st["is_valid"]
        # the list still shows it, for reassignment
        listed = client.get("/api/auth/cookies", headers=_auth(tok)).json()
        assert listed["sees_every_session"] is True
        assert any(c["phone_number"] == "09121110001" for c in listed["cookies"])
        # but nothing may be done to it
        assert client.post("/api/auth/refresh?phone_number=09121110001", headers=_auth(tok)).status_code == 403
        assert client.post("/api/auth/logout?phone_number=09121110001", headers=_auth(tok)).status_code == 403
        # and the panel asks for what root may USE — which is none of these
        mine = client.get("/api/auth/cookies?mine=1", headers=_auth(tok)).json()
        assert mine["sees_every_session"] is False and mine["cookies"] == []
        health = client.get("/api/monitoring/cookies", headers=_auth(tok)).json()
        assert all(i["phone_number"] != "09121110001" for i in health["items"])
        assert client.post("/api/monitoring/cookies/check?phone=09121110001", headers=_auth(tok)).status_code == 404

    def test_the_panel_never_reads_the_pool(self):
        """Every panel reader of the session list — the header pill, the
        status box, the saved list, the scraper picker, the rotation count —
        acts on «my» sessions, so it asks for exactly those. root was shown a
        colleague's number as «شمارهٔ فعال» because these read the pool."""
        js = (ROOT / "frontend/js/app.js").read_text(encoding="utf-8")
        assert "apiCall('/auth/cookies')" not in js
        fn = js[js.index("async function _getActiveSession"):js.index("async function checkCookieStatus")]
        assert "apiCall('/auth/cookies?mine=1')" in fn
        assert "_digits(_currentUser?.divar_phone)" in fn, "the profile's default number comes first"

    def test_the_owner_sees_their_own(self, client):
        tok = _token(client, "pf_sess_owner")
        st = client.get("/api/auth/status", headers=_auth(tok)).json()
        assert st["phone_number"] == "09121110001"

    def test_a_run_on_somebody_elses_number_is_refused_for_root_too(self):
        import inspect
        from app.api.routes import scraper as scraper_routes
        assert '("root", "super_admin")' not in inspect.getsource(scraper_routes._launch_job)


# ── the panel side (no database needed) ──────────────────────────────────────

class TestThePanelSide:

    def _js(self):
        from pathlib import Path
        return Path("frontend/js/app.js").read_text(encoding="utf-8")

    def _html(self):
        from pathlib import Path
        return Path("frontend/index.html").read_text(encoding="utf-8")

    def test_the_forwarder_nav_and_section_are_gated_on_the_new_key(self):
        js = self._js()
        assert "'nav-link-forwarder':  'forwarder'" in js
        assert "forwarder: 'forwarder'" in js
        assert "forwarder:  { title: 'فرستندهٔ پیامک'" in js

    def test_there_is_a_profile_section_reachable_from_the_sidebar(self):
        html, js = self._html(), self._js()
        assert 'id="section-profile"' in html
        assert "onclick=\"showSection('profile')\"" in html
        assert "'profile']" in js and "case 'profile':    loadProfile(); break;" in js

    def test_the_phone_block_moved_out_of_the_2fa_modal(self):
        html = self._html()
        modal = html[html.index('id="twoFAModal"'):]
        modal = modal[:modal.index("</div>\n</div>")] if "</div>\n</div>" in modal else modal[:6000]
        assert 'id="phone-result"' not in modal
        assert html.count('id="phone-result"') == 1
        section = html[html.index('id="section-profile"'):html.index('id="section-forwarder"')]
        assert 'id="phone-result"' in section

    def test_a_rename_or_password_change_keeps_this_device_signed_in(self):
        js = self._js()
        for fn in ("pfSaveProfile", "pfChangePassword"):
            body = js[js.index(f"async function {fn}"):]
            body = body[:body.index("\nasync function")]
            assert "if (r.access_token) setToken(r.access_token);" in body

    def test_the_avatar_helper_escapes_what_it_shows(self):
        """full_name is attacker-reachable: a visitor picks it at sign-up.
        avatar_url goes through safeUrl(), not bare esc(): it is an href/src,
        so a stored «javascript:» value has to be rejected outright, not just
        HTML-escaped."""
        js = self._js()
        fn = js[js.index("function avatarHtml"):js.index("// ═══ My profile")]
        assert "safeUrl(u.avatar_url)" in fn and "esc(name)" in fn and "esc(initial)" in fn

    def test_the_upload_does_not_force_a_json_content_type(self):
        js = self._js()
        fn = js[js.index("async function pfUploadAvatar"):js.index("async function pfRemoveAvatar")]
        assert "fetch(`${API_BASE}/users/me/avatar`" in fn
        assert "Content-Type" not in fn

    def test_the_users_list_offers_the_nudge_only_where_something_is_unverified(self):
        js = self._js()
        assert "const unverified = (u.email && !u.email_verified) || (u.phone && !u.phone_verified);" in js
        assert "nudgeVerify(${esc(u.id)})" in js

    def test_no_native_dialogs_were_introduced(self):
        js = self._js()
        blk = js[js.index("// ═══ Avatars"):js.index("async function loadPhoneState")]
        for bad in ("prompt(", "confirm(", "alert("):
            assert bad not in blk.replace("askConfirm(", "").replace("askText(", "")

    def test_the_styles_use_the_panel_tokens(self):
        from pathlib import Path
        css = Path("frontend/css/style.css").read_text(encoding="utf-8")
        blk = css[css.index("/* ── My profile"):]
        assert "var(--input-bg)" in blk and "var(--border)" in blk
        assert "--bs-secondary-bg" not in blk and "--bs-body-bg" not in blk

    def test_the_cache_busters_moved(self):
        """At least the version this shipped with — not exactly it, or the
        next bump by anyone breaks a test about a different change. The
        date-letter format compares as a string."""
        import re
        html = self._html()
        css = re.search(r"css/style\.css\?v=([0-9a-z]+)", html).group(1)
        js = re.search(r"js/app\.js\?v=([0-9a-z]+)", html).group(1)
        assert css >= "20260915d" and js >= "20260915d"


class TestManyDivarNumbersPerPerson:
    """A person logs in with as many numbers as they have; the pool rotates
    through all of them and one is the primary."""

    def test_a_second_login_does_not_replace_the_primary(self):
        import inspect
        from app.api.routes import auth as auth_routes
        src = inspect.getsource(auth_routes.verify_otp)
        assert 'if current_user and not (current_user.divar_phone or "").strip():' in src

    def test_the_profile_lists_only_my_own_sessions(self):
        from pathlib import Path
        js = Path("frontend/js/app.js").read_text(encoding="utf-8")
        fn = js[js.index("async function pfLoadDivarAccounts"):js.index("async function pfSetPrimaryDivar")]
        assert "apiCall('/auth/cookies?mine=1')" in fn, "an admin's unscoped list carries everybody's rows"
        assert "پیش‌فرض" in fn
        assert "pfDeleteDivar(${esc(c.id)})" in fn

    def test_adding_a_number_is_logging_in(self):
        from pathlib import Path
        js = Path("frontend/js/app.js").read_text(encoding="utf-8")
        fn = js[js.index("function pfAddDivarAccount"):]
        fn = fn[:fn.index("\n}") + 2]
        assert "showSection('auth')" in fn
        html = Path("frontend/index.html").read_text(encoding="utf-8")
        assert 'onclick="pfAddDivarAccount()"' in html and 'id="pf-divar-list"' in html
