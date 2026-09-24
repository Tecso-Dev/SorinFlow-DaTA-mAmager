"""
A number whose phone is in a drawer.

Still valid, still its owner's — and rotation would still hand it a run, park
on its code prompt for the full window, and text a SIM nobody is holding.
«شاید یک شماره در دسترس نباشد و در اسکرپر چرخشی به مشکل بخوریم.»

The owner switches it off. Off means: not auto-picked, not rotated to, not
switched to mid-run, not usable by name. The session is kept as it is.
"""
import inspect
import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_enabled.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.models.cookie import Cookie  # noqa: E402
from app.scraper.divar_scraper import DivarScraper  # noqa: E402
from app.api.routes import auth as auth_routes  # noqa: E402
from app.api.routes import scraper as scraper_routes  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
JS = (ROOT / "frontend/js/app.js").read_text(encoding="utf-8")
HTML = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
DB = (ROOT / "app/database.py").read_text(encoding="utf-8")
REV = (ROOT / "migrations/versions/0009_cookie_enabled.py").read_text(encoding="utf-8")


class TestTheColumn:

    def test_it_exists_and_defaults_on(self):
        col = Cookie.__table__.c.enabled
        assert col.nullable is False
        assert str(col.server_default.arg) == "true"
        assert Cookie(phone_number="0912", cookies=[]).to_dict()["enabled"] is True

    def test_both_migrations_add_it_guarded(self):
        """create_all, the startup migration and Alembic can each get there
        first; none may fail because another did."""
        assert "_migrate_cookie_enabled," in DB
        assert "ADD COLUMN IF NOT EXISTS enabled BOOLEAN NOT NULL DEFAULT TRUE" in DB
        assert 'revision = "0009"' in REV and 'down_revision = "0008"' in REV
        assert 'any(c["name"] == "enabled"' in REV


class TestOffMeansOff:

    def test_rotation_never_picks_it(self):
        src = inspect.getsource(DivarScraper._load_rotation_pool)
        assert "CookieModel.enabled.isnot(False)" in src

    def test_the_usable_count_agrees(self):
        """Or the run absorbs prompts for a number it will never be handed."""
        src = inspect.getsource(DivarScraper._usable_account_count)
        assert "Cookie.enabled.isnot(False)" in src

    def test_the_first_pick_of_a_run_is_the_same_pool(self):
        """initialize() auto-picks from _load_rotation_pool, so it inherits
        the switch without a second rule."""
        src = inspect.getsource(DivarScraper.initialize)
        assert "_pool = await self._load_rotation_pool()" in src

    def test_naming_it_does_not_switch_it_back_on(self):
        src = inspect.getsource(scraper_routes._launch_job)
        assert "c.enabled is False" in src and "status_code=409" in src
        assert "اول در فهرست شماره‌ها روشنش کنید" in src


class TestTheSwitchIsTheOwners:

    def test_the_endpoint_exists(self):
        assert "/cookies/{cookie_id}/enabled" in [r.path for r in auth_routes.router.routes]

    def test_only_the_owner_may_flip_it_root_included(self):
        src = inspect.getsource(auth_routes.set_cookie_enabled)
        bare = re.sub(r"#.*", "", src)
        assert "cookie.owner_user_id != user.id" in bare
        assert "_sees_every_session" not in bare

    def test_somebody_elses_number_is_a_404_not_a_403(self):
        src = inspect.getsource(auth_routes.set_cookie_enabled)
        assert "status_code=404" in src and "403" not in src

    def test_the_list_carries_the_flag(self):
        src = inspect.getsource(auth_routes.list_cookies)
        assert '"enabled": c.enabled is not False' in src


class TestThePanel:

    def test_the_scraper_form_lists_each_number_with_a_switch(self):
        assert 'id="scraper-account-list"' in HTML
        assert "function _renderAccountList(" in JS
        assert 'role="switch"' in JS.split("function _renderAccountList(")[1].split("\n}")[0]

    def test_the_dropdown_greys_a_switched_off_number(self):
        assert "const usable = c.is_valid && !c.identity_required_at && c.enabled !== false;" in JS

    def test_the_sessions_page_has_the_same_switch(self):
        body = JS.split("async function loadCookies()")[1].split("\n}")[0]
        assert "toggleCookieEnabled(" in body

    def test_a_failed_flip_puts_the_switch_back(self):
        fn = JS.split("async function toggleCookieEnabled(")[1].split("\n}")[0]
        assert "input.checked = !enabled" in fn
