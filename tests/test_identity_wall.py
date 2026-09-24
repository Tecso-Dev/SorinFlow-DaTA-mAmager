"""
«دیشب سبحان به مشکلی خورد. دیوار ازش احراز هویت با کد ملی خواست. اگر همچین
چیزی پیدا کردی یه پنجره‌ای باز کن تو اسکرپر و نشون بده.»

Divar sometimes asks an account to prove who it is — national ID, birth
date. From a selector's point of view it looks like every other challenge
(a dialog with an input) and it is nothing like one: no SMS is coming, no
code will satisfy it, and a run that parks there waits five minutes for
nothing while the account stays unusable.

So it is recognised by its words, recorded in three places — the cookie row
so it survives a restart and rotation can skip it, the registry the panel
polls so a dialog opens within seconds, the run log so the finish line says
why — and cleared only by a person saying they did it.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_idw.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

import pytest  # noqa: E402
from app.scraper import otp_store  # noqa: E402
from app.scraper.contact_extractor import ContactExtractor  # noqa: E402
from app.scraper.divar_scraper import DivarScraper  # noqa: E402
from app.models.cookie import Cookie  # noqa: E402
from app.api.routes import auth as auth_routes  # noqa: E402
from app.api.routes import scraper as sr  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_JS = open(os.path.join(ROOT, "frontend/js/app.js"), encoding="utf-8").read()
DB = open(os.path.join(ROOT, "app/database.py"), encoding="utf-8").read()

wall = ContactExtractor._identity_wall


class TestRecognisingIt:
    def test_the_verification_page(self):
        assert wall("احراز هویت\nبرای ادامه، کد ملی و تاریخ تولد خود را وارد کنید")

    def test_the_other_spellings(self):
        assert wall("تأیید هویت — شمارهٔ ملی")
        assert wall("هویت شما باید تایید شود. کدملی:")

    def test_a_poster_mentioning_their_national_id_is_not_a_wall(self):
        """«کد ملی نمی‌دهم» is a person's own sentence in a description."""
        assert not wall("مالک هستم، کد ملی به کسی نمی‌دهم، فقط تماس")

    def test_the_panels_own_word_alone_is_not_a_wall(self):
        assert not wall("احراز هویت دیوار")

    def test_the_code_prompt_is_not_a_wall(self):
        assert not wall("کد ۶ رقمی ارسال‌شده به شمارهٔ ۰۹۰۵… را وارد کنید")

    def test_empty(self):
        assert not wall("") and not wall(None)


class TestWhereItIsChecked:
    SRC = inspect.getsource(ContactExtractor._handle_sms_otp_if_present)

    def test_before_the_modal_is_treated_as_a_code_prompt(self):
        assert self.SRC.index("self._identity_wall(modal_text)") < self.SRC.index("otp_store.request(")

    def test_after_the_words_are_logged(self):
        """So a wall that does not match still leaves its text to read."""
        assert self.SRC.index("modal says") < self.SRC.index("self._identity_wall(modal_text)")

    def test_a_whole_page_wall_is_caught_after_the_click_too(self):
        src = inspect.getsource(ContactExtractor.get_phone_number)
        assert "self._identity_wall(_body)" in src


class TestReporting:
    @pytest.mark.asyncio
    async def test_the_extractor_records_the_channel_and_tells_the_scraper(self):
        got = []
        async def cb(text):
            got.append(text)
        e = ContactExtractor.__new__(ContactExtractor)
        e.account_phone, e.on_identity_required, e.contact_channel = "0905", cb, None
        await e._report_identity_wall("احراز هویت — کد ملی")
        assert e.contact_channel == "identity_required"
        assert got == ["احراز هویت — کد ملی"]

    @pytest.mark.asyncio
    async def test_a_failing_callback_does_not_take_the_scrape(self):
        async def cb(text):
            raise RuntimeError("db gone")
        e = ContactExtractor.__new__(ContactExtractor)
        e.account_phone, e.on_identity_required, e.contact_channel = "0905", cb, None
        await e._report_identity_wall("x")
        assert e.contact_channel == "identity_required"

    def test_the_scraper_wires_the_callback(self):
        src = inspect.getsource(DivarScraper.scrape_property_detail)
        assert "on_identity_required=self._note_identity_required" in src


class TestTheScraperSetsTheAccountAside:
    SRC = inspect.getsource(DivarScraper._note_identity_required)

    def test_the_registry_the_panel_polls(self):
        assert "_os.note_identity_required(" in self.SRC

    def test_the_cookie_row_so_it_survives_a_restart(self):
        assert "row.identity_required_at = datetime.now(timezone.utc)" in self.SRC

    def test_the_run_log(self):
        assert "احراز هویت با کد ملی می‌خواهد" in self.SRC
        assert 'level="error"' in self.SRC

    def test_and_rotates_away(self):
        assert "self._force_rotate = True" in self.SRC

    def test_rotation_never_offers_it_back(self):
        src = inspect.getsource(DivarScraper._usable_accounts_query)
        assert "CookieModel.identity_required_at.is_(None)" in src
        assert "_usable_accounts_query" in inspect.getsource(DivarScraper._load_rotation_pool)

    def test_the_column_and_its_migration(self):
        assert "identity_required_at" in Cookie.__table__.c
        assert "_migrate_identity_required," in DB


class TestTheRegistry:
    def test_note_list_clear(self):
        otp_store._identity.clear()
        otp_store.note_identity_required("0905-833-3026", job_id="j1", text="کد ملی")
        items = otp_store.identity_required()
        assert len(items) == 1 and items[0]["phone"] == "0905-833-3026" and items[0]["job_id"] == "j1"
        assert otp_store.clear_identity_required("09058333026") is True
        assert otp_store.identity_required() == []

    def test_it_is_in_the_poll(self):
        """It rides the same poll — now filtered to the person who owns the
        number, so the source reads through that filter rather than straight
        off the store."""
        src = inspect.getsource(sr.get_otp_pending)
        assert "otp_store.identity_required()" in src
        assert '"identity_required": identity' in src and "_my_prompts(" in src


class TestClearingIt:
    def test_the_endpoint_exists(self):
        assert "/cookies/{cookie_id}/identity-cleared" in [r.path for r in auth_routes.router.routes]

    def test_only_a_person_clears_it(self):
        """Not time, not a code."""
        src = inspect.getsource(auth_routes.identity_cleared)
        assert "cookie.identity_required_at = None" in src
        assert "otp_store.clear_identity_required(" in src

    def test_own_accounts_only(self):
        src = inspect.getsource(auth_routes.identity_cleared)
        assert "status_code=403" in src


class TestThePanel:
    def test_the_poll_shows_the_wall_before_any_code_prompt(self):
        i = APP_JS.index("async function pollDivarOtp()")
        block = APP_JS[i:APP_JS.index("\n}\n", i)]     # the whole function, not 800 chars
        assert block.index("_showIdentityWall(") < block.index("pending.find(")

    def test_the_dialog_names_the_number_and_what_to_do(self):
        i = APP_JS.index("async function _showIdentityWall(")
        block = APP_JS[i:i + 2200]
        assert "کد ملی" in block and "divar.ir/my-divar" in block
        assert "انجام شد" in block and "بعداً" in block

    def test_it_is_the_danger_tone(self):
        i = APP_JS.index("async function _showIdentityWall(")
        assert "tone: 'danger'" in APP_JS[i:i + 800]

    def test_it_never_stacks(self):
        i = APP_JS.index("async function _showIdentityWall(")
        assert "if (_identityOpen) return;" in APP_JS[i:i + 300]

    def test_done_clears_it_on_the_server(self):
        i = APP_JS.index("async function _identityCleared(")
        assert "/identity-cleared`, { method: 'POST' }" in APP_JS[i:i + 900]

    def test_the_picker_refuses_the_number(self):
        i = APP_JS.index("async function loadScraperAccounts()")
        block = APP_JS[i:i + 1600]
        assert "احراز هویت لازم" in block
        # one rule for «may this number be picked», shared with the switch dialog
        assert "_divarUsable(c) ? '' : ' disabled'" in block
        j = APP_JS.index("function _divarUsable(")
        assert "!c.identity_required_at" in APP_JS[j:j + 200]

    def test_the_account_list_shows_it_with_a_way_out(self):
        i = APP_JS.index("async function loadCookies()")
        block = APP_JS[i:i + 2000]
        assert "احراز هویت لازم" in block and "_identityCleared(" in block
