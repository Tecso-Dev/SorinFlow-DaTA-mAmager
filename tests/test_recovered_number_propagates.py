"""
«اسکرپ کرد ولی باید همه جاهایی که مربوط می‌شه اضافه بشه و از این بخش هم حذف
بشه — CRM، لیست املاک، و تو قسمت اسکرپ.»

A listing failed its reveal in a run, was recorded as «بدون شماره», and then
recovered by «اسکرپ تکی». The property row got the number — that was the
only thing a re-scrape updated. The lead the CRM shows kept its «---»,
because create_lead_from_property runs once on the insert and never
again; and the skipped list kept offering the listing for a retry it no
longer needed, its count never going down.

Every save now propagates: a property with a number fills the lead that
was made without one, and clears its own skipped rows. Single scrape,
resume, next full run — one rule, one place.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_recov.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

import pytest  # noqa: E402
from app.crm import lead_service  # noqa: E402
from app.services import skipped_listings as sl  # noqa: E402
from app.scraper.divar_scraper import DivarScraper  # noqa: E402

ATTEMPT = inspect.getsource(DivarScraper._save_property_attempt)
RECOVER = inspect.getsource(DivarScraper._number_recovered)


class FakeLead:
    def __init__(self, phone=None, seller=None):
        self.id, self.phone_number, self.seller_name = 7, phone, seller


class FakeDB:
    def __init__(self, lead):
        self._lead, self.commits, self.rollbacks = lead, 0, 0
    async def execute(self, q):
        lead = self._lead
        class R:
            def scalar_one_or_none(self):
                return lead
        return R()
    async def commit(self):
        self.commits += 1
    async def rollback(self):
        self.rollbacks += 1


class Prop:
    def __init__(self, phone, seller=None, divar_id="gamyVmL9"):
        self.id, self.phone_number, self.seller_name, self.divar_id = 1, phone, seller, divar_id


class TestTheLeadIsFilled:
    @pytest.mark.asyncio
    async def test_a_phoneless_lead_gets_the_number(self):
        db = FakeDB(FakeLead(phone=None))
        assert await lead_service.fill_lead_from_property(db, Prop("09141407464")) is True
        assert db._lead.phone_number == "09141407464" and db.commits == 1

    @pytest.mark.asyncio
    async def test_a_number_typed_by_hand_is_not_overwritten(self):
        """A scrape must not undo a correction somebody made in the CRM."""
        db = FakeDB(FakeLead(phone="09120000000"))
        assert await lead_service.fill_lead_from_property(db, Prop("09141407464")) is False
        assert db._lead.phone_number == "09120000000" and db.commits == 0

    @pytest.mark.asyncio
    async def test_the_seller_name_is_filled_the_same_way(self):
        db = FakeDB(FakeLead(phone="x", seller=None))
        await lead_service.fill_lead_from_property(db, Prop("x", seller="آژانس امین"))
        assert db._lead.seller_name == "آژانس امین"

    @pytest.mark.asyncio
    async def test_no_lead_is_not_an_error(self):
        assert await lead_service.fill_lead_from_property(FakeDB(None), Prop("x")) is False

    @pytest.mark.asyncio
    async def test_a_failure_rolls_back_and_returns_false(self):
        class Boom(FakeDB):
            async def execute(self, q):
                raise RuntimeError("db gone")
        db = Boom(None)
        assert await lead_service.fill_lead_from_property(db, Prop("x")) is False
        assert db.rollbacks == 1


class TestTheSkippedRowsAreCleared:
    def test_resolve_exists_and_deletes_by_listing(self):
        src = inspect.getsource(sl.resolve)
        assert "delete(SkippedListing).where(SkippedListing.divar_id == str(divar_id))" in src

    def test_it_uses_its_own_session_and_never_raises(self):
        src = inspect.getsource(sl.resolve)
        assert "async_session_maker()" in src and "except Exception" in src

    @pytest.mark.asyncio
    async def test_no_id_is_a_no_op(self):
        assert await sl.resolve("") == 0
        assert await sl.resolve(None) == 0


class TestSaveIsTheOnePlace:
    def test_the_update_path_propagates(self):
        i = ATTEMPT.index("existing.updated_at = datetime.now()")
        assert "await self._number_recovered(existing)" in ATTEMPT[i:i + 500]

    def test_the_create_path_propagates_too(self):
        """A listing skipped in an earlier run and created fresh now is not
        skipped any more either."""
        i = ATTEMPT.index("Saved new property:")
        assert "await self._number_recovered(new_property)" in ATTEMPT[i:i + 400]

    def test_it_does_nothing_without_a_number(self):
        """Saving a row that still has no phone must not clear the skipped
        entry that is precisely about it having no phone."""
        assert 'if not (getattr(prop, "phone_number", None) or "").strip():' in RECOVER
        i = RECOVER.index('if not (getattr(prop, "phone_number"')
        assert "return" in RECOVER[i:i + 120]

    def test_it_fills_the_lead_and_clears_the_rows(self):
        assert "fill_lead_from_property(self.db_session, prop)" in RECOVER
        assert "skipped_listings.resolve(prop.divar_id)" in RECOVER

    def test_neither_half_can_take_the_save_with_it(self):
        assert RECOVER.count("except Exception") == 2

    @pytest.mark.asyncio
    async def test_driven_end_to_end_with_doubles(self, monkeypatch):
        filled, resolved = [], []
        async def _fill(db, prop):
            filled.append(prop.divar_id); return True
        async def _resolve(divar_id):
            resolved.append(divar_id); return 1
        monkeypatch.setattr(lead_service, "fill_lead_from_property", _fill)
        monkeypatch.setattr(sl, "resolve", _resolve)
        s = DivarScraper.__new__(DivarScraper); s.db_session = object()
        await s._number_recovered(Prop("09141407464"))
        assert filled == ["gamyVmL9"] and resolved == ["gamyVmL9"]

    @pytest.mark.asyncio
    async def test_a_phoneless_save_touches_nothing(self, monkeypatch):
        touched = []
        async def _fill(db, prop):
            touched.append("lead"); return True
        async def _resolve(divar_id):
            touched.append("skipped"); return 1
        monkeypatch.setattr(lead_service, "fill_lead_from_property", _fill)
        monkeypatch.setattr(sl, "resolve", _resolve)
        s = DivarScraper.__new__(DivarScraper); s.db_session = object()
        await s._number_recovered(Prop(None))
        assert touched == []
