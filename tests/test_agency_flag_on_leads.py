"""
The label has to reach the CRM, not just the property list.

A lead is a phone number somebody is about to call. Whether the person on
the other end is an agency changes the call, so it travels with the lead
rather than being something you go and look up in لیست املاک.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_agl.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.schemas import LeadResponse  # noqa: E402
from app.api.routes import crm  # noqa: E402

ATTACH = inspect.getsource(crm._attach_property_columns) \
    if hasattr(crm, "_attach_property_columns") else ""


class TestTheLeadCarriesIt:
    def test_the_response_has_both_fields(self):
        assert "agency_suspected" in LeadResponse.model_fields
        assert "agency_evidence" in LeadResponse.model_fields

    def test_they_are_optional_so_an_orphaned_lead_still_validates(self):
        """A lead whose property was deleted has no answer, and «no answer»
        is not «not an agency»."""
        lead = LeadResponse.model_construct()
        assert lead.agency_suspected is None


class TestTheListEndpointFillsThem:
    def _attach(self):
        for name in ("_attach_property_columns", "_attach_property_fields"):
            fn = getattr(crm, name, None)
            if fn:
                return inspect.getsource(fn)
        raise AssertionError("the property-column attach helper was renamed")

    def test_both_columns_are_selected(self):
        src = self._attach()
        assert "Property.agency_suspected" in src
        assert "Property.agency_evidence" in src

    def test_both_are_copied_onto_the_lead(self):
        src = self._attach()
        assert "item.agency_suspected = bool(p.agency_suspected)" in src
        assert "item.agency_evidence = p.agency_evidence" in src

    def test_the_flag_is_coerced_to_a_boolean(self):
        """The column is NULL on every row scraped before this existed, and a
        null renders as nothing rather than as «no»."""
        src = self._attach()
        assert "bool(p.agency_suspected)" in src


class TestTheLeadDetailAlreadyHadIt:
    def test_the_full_snapshot_comes_from_to_dict(self):
        """_lead_with_property serialises the whole property, so the two
        fields ride along with no extra wiring — pinned so a future
        hand-rolled field list does not silently drop them."""
        src = inspect.getsource(crm._lead_with_property)
        assert "prop.to_dict()" in src

    def test_to_dict_includes_them(self):
        from app.models.property import Property
        assert "agency_suspected" in Property().to_dict()
        assert "agency_evidence" in Property().to_dict()
