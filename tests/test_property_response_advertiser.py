"""
The label has to survive the response schema.

Both tables and both modals were supposed to show it. Two of them read from
PropertyResponse, which lists its fields explicitly — and did not list these.
So لیست املاک rendered no badge at all, and the property modal's
«آگهی‌دهنده» row rendered blank whatever the column held. That row is older
than any of this work: it has never once displayed a value.

Pydantic drops what a response model does not declare, silently, which is
exactly why this needs a test rather than a reading.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_pra.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.schemas import PropertyResponse  # noqa: E402
from app.models.property import Property  # noqa: E402


class TestTheFieldsAreDeclared:
    def test_divars_declaration(self):
        assert "advertiser_type" in PropertyResponse.model_fields

    def test_our_reading(self):
        assert "agency_suspected" in PropertyResponse.model_fields

    def test_the_evidence(self):
        assert "agency_evidence" in PropertyResponse.model_fields


class TestTheySurviveSerialisation:
    def _row(self, **kw):
        """A row with the columns the schema requires, and nothing more."""
        p = Property(id=1, tag_number="T1", divar_id="abc",
                     url="https://divar.ir/v/abc", title="t",
                     has_elevator=False, has_parking=False, has_storage=False,
                     has_balcony=False, has_images=False, is_active=True,
                     images=[], features=[], amenities=[])
        for k, v in kw.items():
            setattr(p, k, v)
        return PropertyResponse.model_validate(p)

    def test_an_agency_listing_arrives_labelled(self):
        r = self._row(agency_suspected=True, agency_evidence="املاک هستم",
                      advertiser_type="personal")
        assert r.agency_suspected is True
        assert r.agency_evidence == "املاک هستم"
        assert r.advertiser_type == "personal"

    def test_the_disagreement_is_visible_from_the_two_together(self):
        """Red in the panel means exactly this pair: Divar said personal and
        the words said otherwise. Neither field alone can express it."""
        r = self._row(agency_suspected=True, advertiser_type="personal")
        assert r.agency_suspected and r.advertiser_type == "personal"

    def test_a_row_never_looked_at_stays_unknown(self):
        """NULL is «not checked», and must not arrive as False."""
        r = self._row()
        assert r.agency_suspected is None

    def test_a_private_listing_arrives_as_false_not_null(self):
        r = self._row(agency_suspected=False)
        assert r.agency_suspected is False
