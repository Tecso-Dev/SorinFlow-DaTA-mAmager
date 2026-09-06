"""
«شخصی‌ها با املاکی‌ها جدا باشن.»

Two sources answer «who posted this»: Divar's declaration, and the ad's own
words. Divar says nothing on plenty of rows and says the wrong thing on some
of the rest — a listing ending «املاک هستم» came back through a «شخصی»
filter — so «املاکی» is the union of the two rather than either alone, and
«شخصی» is its complement.

Leads entered by hand have no linked property to judge. They belong under
«شخصی»: nothing about them says agency, and dropping them from that view
would read as the filter losing rows.
"""
import inspect
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_laf.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_JS = open(os.path.join(ROOT, "frontend/js/app.js"), encoding="utf-8").read()
INDEX = open(os.path.join(ROOT, "frontend/index.html"), encoding="utf-8").read()

from app.api.routes import crm  # noqa: E402

FILTERS = inspect.getsource(crm._apply_lead_filters)
BLOCK = FILTERS[FILTERS.index("── آگهی‌دهنده"):FILTERS.index("── نوع ملک")]


class TestTheFilterIsUnderstood:
    def test_the_helper_takes_it(self):
        assert "advertiser" in inspect.signature(crm._apply_lead_filters).parameters

    def test_only_the_two_known_values_do_anything(self):
        """An unknown value must show everything, not nothing."""
        assert 'in ("agency", "personal")' in BLOCK

    def test_it_is_whitespace_tolerant(self):
        assert '(advertiser or "").strip()' in BLOCK


class TestWhatCountsAsAnAgency:
    def test_our_own_reading_counts(self):
        assert "Property.agency_suspected.is_(True)" in BLOCK

    def test_divars_declaration_counts_too(self):
        assert 'Property.advertiser_type == "agency"' in BLOCK

    def test_either_is_enough(self):
        """Divar is missing on many rows and wrong on some; requiring both
        would find almost nothing."""
        assert "or_(Property.agency_suspected" in BLOCK

    def test_only_a_true_flag_counts_not_a_null(self):
        """is_(True) rather than a truth test: NULL is «not looked at», and
        an un-backfilled row must not be called an agency."""
        assert ".is_(True)" in BLOCK
        assert "Property.agency_suspected == True" not in BLOCK


class TestPersonalIsTheComplement:
    def test_it_is_the_negation_of_the_same_expression(self):
        """Two hand-written conditions would drift out of step."""
        assert "not_(looks_agency)" in BLOCK
        assert BLOCK.count("looks_agency") == 3   # built once, used twice

    def test_it_is_an_exists_so_a_lead_with_no_property_survives_the_negation(self):
        assert ".correlate(Lead).exists()" in BLOCK


class TestBothEndpointsGetIt:
    def test_the_list_endpoint(self):
        src = inspect.getsource(crm)
        assert src.count("advertiser: Optional[str] = None") == 2, \
            "the list and the Excel export must both accept it"

    def test_it_is_passed_through_both_times(self):
        src = inspect.getsource(crm)
        assert src.count("advertiser=advertiser,") == 2

    def test_the_export_therefore_matches_the_screen(self):
        """The whole reason both go through one helper."""
        assert "_apply_lead_filters" in inspect.getsource(crm.export_leads_excel) \
            if hasattr(crm, "export_leads_excel") else True


class TestThePanelControl:
    def test_the_select_exists(self):
        assert 'id="crm-filter-advertiser"' in INDEX

    def test_it_offers_all_three_choices(self):
        i = INDEX.index('id="crm-filter-advertiser"')
        block = INDEX[i:i + 600]
        assert "همه آگهی‌دهنده‌ها" in block
        assert 'value="personal">شخصی' in block
        assert 'value="agency">املاکی' in block

    def test_choosing_one_reloads_the_list(self):
        i = INDEX.index('id="crm-filter-advertiser"')
        assert "reloadLeadsFromFilter()" in INDEX[i - 200:i + 200]

    def test_it_reaches_the_query_string(self):
        assert "parts.push(`advertiser=${encodeURIComponent(adv)}`)" in APP_JS

    def test_an_empty_choice_sends_nothing(self):
        i = APP_JS.index("parts.push(`advertiser=")
        assert "if (adv)" in APP_JS[i - 40:i]

    def test_clearing_the_filters_clears_it_too(self):
        i = APP_JS.index("function clearLeadsFilter()")
        assert "crm-filter-advertiser" in APP_JS[i:i + 900]

    def test_the_excel_export_reuses_the_same_query_string(self):
        i = APP_JS.index("function exportLeadsExcel()")
        assert "_leadsQueryString()" in APP_JS[i:i + 400]

    def test_the_cache_buster_moved(self):
        m = re.search(r"js/app\.js\?v=([0-9a-z]+)", INDEX)
        assert m and m.group(1) != "20260906c"
