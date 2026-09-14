"""
«اگر آگهی شماره نداشت (شماره مخفی در چت) اشاره شود که شماره ندارد.»

Three different facts hid behind one blank in the phone column: the poster
took contact through chat only, the reveal failed, or the row predates the
distinction. The first is the poster's choice and no run will ever fill it;
the second is ours and is worth a retry. Treating them alike meant every
chat-only listing was re-opened on every run to fill a gap that is not a
gap — a reveal spent, an account worn, and the same nothing produced.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_cc.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

import pytest  # noqa: E402
from app.scraper.contact_extractor import ContactExtractor  # noqa: E402
from app.scraper.divar_scraper import DivarScraper  # noqa: E402
from app.models.property import Property  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_JS = open(os.path.join(ROOT, "frontend/js/app.js"), encoding="utf-8").read()
DB = open(os.path.join(ROOT, "app/database.py"), encoding="utf-8").read()


class FakeEl:
    def __init__(self, visible=True):
        self._v = visible
    async def is_visible(self):
        return self._v


class FakePage:
    def __init__(self, body="", elements=None):
        self._body = body
        self._els = elements or {}
    async def inner_text(self, sel):
        return self._body
    async def query_selector(self, sel):
        return self._els.get(sel)


def extractor(page):
    e = ContactExtractor.__new__(ContactExtractor)
    e.page = page
    e.contact_channel = None
    return e


class TestDetectingChatOnly:
    @pytest.mark.asyncio
    async def test_divar_saying_it_in_words(self):
        assert await extractor(FakePage(body="… امکان تماس تلفنی وجود ندارد …"))._chat_only()

    @pytest.mark.asyncio
    async def test_the_hidden_number_wording(self):
        assert await extractor(FakePage(body="شماره تماس مخفی است"))._chat_only()

    @pytest.mark.asyncio
    async def test_a_chat_button_is_NOT_evidence(self):
        """This test used to assert the opposite, and the opposite was a bug.

        «چت و تماس» is in Divar's site header on every listing page, so a lone
        chat control proves nothing about whether a number is on offer. When
        Divar restricted the account and hid «اطلاعات تماس», the header link
        was still there and twenty listings were written off as «فقط چت» —
        permanently, since property_exists refuses to re-scrape those. One was
        checked by hand: the number was on the page.
        """
        page = FakePage(body="چت و تماس", elements={'button:has-text("چت")': FakeEl()})
        assert not await extractor(page)._chat_only()

    @pytest.mark.asyncio
    async def test_only_what_divar_says_counts(self):
        page = FakePage(body="چت و تماس\nشماره مخفی است")
        assert await extractor(page)._chat_only()

    @pytest.mark.asyncio
    async def test_an_ordinary_page_is_not_chat_only(self):
        assert not await extractor(FakePage(body="اطلاعات تماس"))._chat_only()

    @pytest.mark.asyncio
    async def test_a_page_that_throws_reads_as_not_chat_only(self):
        """Then it falls through to «unavailable», which retries. Safer than
        marking a listing permanently phone-less on an exception."""
        class Exploding:
            async def inner_text(self, sel):
                raise RuntimeError("closed")
            async def query_selector(self, sel):
                raise RuntimeError("closed")
        assert not await extractor(Exploding())._chat_only()


class TestTheExtractorReportsHowItEnded:
    def _src(self):
        return inspect.getsource(ContactExtractor.get_phone_number)

    def test_no_button_is_judged_before_being_called_a_failure(self):
        """It is now judged twice: an identity demand first — which is ours
        and temporary — and only then the poster's own choice, which is
        permanent."""
        src = self._src()
        i = src.index("if not contact_button:")
        j = src.index("phone cannot be extracted", i)
        blk = src[i:j]
        assert "_chat_only(" in blk
        assert "_needs_identity(" in blk
        assert blk.index("_needs_identity(") < blk.index("_chat_only(")

    def test_a_revealed_number_is_recorded_as_phone(self):
        assert 'self.contact_channel = "phone"' in self._src()

    def test_a_modal_offering_only_chat_is_recorded(self):
        src = self._src()
        i = src.index('self.contact_channel = "phone"')
        assert '"chat_only"' in src[i:i + 600]

    def test_everything_else_is_unavailable(self):
        assert 'self.contact_channel = "unavailable"' in self._src()

    def test_the_field_starts_unset(self):
        assert "self.contact_channel = None" in inspect.getsource(ContactExtractor.__init__)


class TestTheRowRecordsIt:
    def test_the_column_exists_and_is_indexed(self):
        assert "contact_channel" in Property.__table__.c
        assert Property.__table__.c.contact_channel.index is True

    def test_it_is_serialised(self):
        assert "contact_channel" in Property().to_dict()

    def test_the_scraper_writes_it(self):
        src = inspect.getsource(DivarScraper.scrape_property_detail)
        assert 'property_data["contact_channel"]' in src
        assert 'contact_extractor.contact_channel or "unavailable"' in src

    def test_a_phone_wins_whatever_the_extractor_said(self):
        src = inspect.getsource(DivarScraper.scrape_property_detail)
        assert '"phone" if phone_number' in src

    def test_the_migration_is_registered(self):
        assert "_migrate_contact_channel," in DB
        assert "ADD COLUMN IF NOT EXISTS contact_channel VARCHAR(16)" in DB

    def test_old_rows_are_left_null_not_guessed(self):
        i = DB.index("async def _migrate_contact_channel")
        block = DB[i:i + 900]
        # wrapped across a line in the docstring, so match the two halves
        assert "guessing «chat_only» would stop" in block and "the retry" in block


class TestChatOnlyIsNotRetried:
    def test_property_exists_treats_it_as_complete(self):
        src = inspect.getsource(DivarScraper.property_exists)
        i = src.index('== "chat_only"')
        assert "return True" in src[i:i + 300]

    def test_but_unavailable_still_is(self):
        """The retry that closes real gaps must survive."""
        src = inspect.getsource(DivarScraper.property_exists)
        assert "re-scraping to fill it in" in src


class TestThePanelSaysWhich:
    def test_there_is_one_renderer(self):
        assert "function noPhoneCell(p)" in APP_JS

    def test_chat_only_gets_a_badge(self):
        i = APP_JS.index("function noPhoneCell(p)")
        assert "فقط چت" in APP_JS[i:i + 900]

    def test_a_failed_reveal_says_it_will_retry(self):
        i = APP_JS.index("function noPhoneCell(p)")
        assert "گرفته نشد" in APP_JS[i:i + 900]
        assert "دوباره تلاش" in APP_JS[i:i + 900]

    def test_an_old_row_still_shows_a_dash(self):
        i = APP_JS.index("function noPhoneCell(p)")
        assert "---" in APP_JS[i:i + 900]

    def test_all_three_phone_cells_use_it(self):
        assert APP_JS.count("noPhoneCell(") >= 4   # 1 definition + 3 uses

    def test_the_crm_lead_carries_the_field(self):
        from app.schemas import LeadResponse
        assert "contact_channel" in LeadResponse.model_fields
