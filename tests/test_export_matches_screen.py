"""
An Excel file that does not match the screen it was exported from.

The leads export was fixed for this once, with a shared _apply_lead_filters
and the note «exporting a filtered view handed back rows the screen was not
showing». An audit of every other export found the same thing in five more
places — the customers export took no filters at all, so exporting a list
narrowed to «داغ» handed back every customer on file.

This file grows one class per surface as each is repaired.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_exp.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_JS = open(os.path.join(ROOT, "frontend/js/app.js"), encoding="utf-8").read()
INDEX = open(os.path.join(ROOT, "frontend/index.html"), encoding="utf-8").read()

from app.api.routes import crm  # noqa: E402


def params(fn):
    return [p for p in inspect.signature(fn).parameters
            if p not in ("db", "current_user", "_")]


class TestCustomers:
    FILTERS = ("search", "temperature", "source", "sort")

    def test_the_list_and_the_export_take_the_same_filters(self):
        listed = set(params(crm.list_customers)) - {"limit", "offset"}
        exported = set(params(crm.export_customers_excel))
        assert listed == exported, f"list={listed} export={exported}"

    def test_the_export_accepts_each_one(self):
        got = params(crm.export_customers_excel)
        for f in self.FILTERS:
            assert f in got, f

    def test_both_go_through_one_helper(self):
        """Two hand-written filter blocks drift apart; that is how this bug
        was born the first time."""
        assert "_apply_customer_filters(" in inspect.getsource(crm.list_customers)
        assert "_apply_customer_filters(" in inspect.getsource(crm.export_customers_excel)

    def test_the_helper_applies_all_four(self):
        src = inspect.getsource(crm._apply_customer_filters)
        assert "Customer.temperature ==" in src
        assert "Customer.source ==" in src
        assert "if search:" in src
        assert "order_by(order)" in src

    def test_the_panel_builds_one_query_string_for_both(self):
        assert "function _customersQueryString()" in APP_JS
        i = APP_JS.index("async function loadCustomers()")
        assert "_customersQueryString()" in APP_JS[i:i + 300]

    def test_the_export_button_uses_it(self):
        assert "function exportCustomersExcel()" in APP_JS
        i = APP_JS.index("function exportCustomersExcel()")
        assert "_customersQueryString()" in APP_JS[i:i + 300]

    def test_the_button_no_longer_calls_the_filterless_export(self):
        assert "exportExcel('customers')" not in INDEX
        assert "exportCustomersExcel()" in INDEX


class TestContacts:
    def test_both_exports_take_the_list_s_filters(self):
        listed = set(params(crm.list_contacts)) - {"limit", "offset"}
        for fn in (crm.export_contacts_excel, crm.export_contacts_json):
            assert set(params(fn)) == listed, fn.__name__

    def test_the_json_export_was_not_forgotten(self):
        """Two exports, one of them easy to miss — and a JSON dump of the
        whole book is the bigger leak of the two."""
        assert "contact_type" in params(crm.export_contacts_json)

    def test_all_three_go_through_one_helper(self):
        for fn in (crm.list_contacts, crm.export_contacts_excel,
                   crm.export_contacts_json):
            assert "_apply_contact_filters(" in inspect.getsource(fn), fn.__name__

    def test_the_panel_shares_one_query_string(self):
        assert "function _contactsQueryString()" in APP_JS
        for fn in ("async function loadContacts()", "function exportContactsExcel()"):
            i = APP_JS.index(fn)
            assert "_contactsQueryString()" in APP_JS[i:i + 300], fn

    def test_the_button_no_longer_calls_the_filterless_export(self):
        assert "exportExcel('contacts')" not in INDEX


class TestDeals:
    def test_both_exports_take_the_list_s_filters(self):
        listed = set(params(crm.list_deals)) - {"limit", "offset"}
        for fn in (crm.export_deals_excel, crm.export_deals_json):
            assert set(params(fn)) == listed, fn.__name__

    def test_all_three_go_through_one_helper(self):
        for fn in (crm.list_deals, crm.export_deals_excel, crm.export_deals_json):
            assert "_apply_deal_filters(" in inspect.getsource(fn), fn.__name__

    def test_the_panel_shares_one_query_string(self):
        assert "function _dealsQueryString()" in APP_JS
        for fn in ("async function loadDeals()", "function exportDealsExcel()"):
            i = APP_JS.index(fn)
            assert "_dealsQueryString()" in APP_JS[i:i + 300], fn

    def test_the_button_no_longer_calls_the_filterless_export(self):
        assert "exportExcel('deals')" not in INDEX


class TestTheGenericExportIsNotSilentlyReintroduced:
    """exportExcel(type) sends no filters at all. Every list that has filters
    needs its own, so this pins which lists may still use the generic one."""

    def test_no_filtered_list_uses_it_any_more(self):
        import re
        used = set(re.findall(r"exportExcel\('([a-z]+)'\)", INDEX))
        assert used == set(), \
            f"a filtered list is back on the filterless export: {used}"


class TestDailyPerformance:
    def test_the_export_takes_the_list_s_filters(self):
        listed = set(params(crm.list_dpa)) - {"limit", "offset"}
        assert set(params(crm.export_dpa_excel)) == listed

    def test_both_go_through_one_helper(self):
        for fn in (crm.list_dpa, crm.export_dpa_excel):
            assert "_apply_dpa_filters(" in inspect.getsource(fn), fn.__name__

    def test_searching_one_agent_no_longer_exports_the_team(self):
        src = inspect.getsource(crm._apply_dpa_filters)
        assert "DailyPerformance.agent_name.ilike" in src

    def test_the_panel_shares_one_query_string(self):
        assert "function _dpaQueryString()" in APP_JS
        for fn in ("async function loadDpa()", "function exportDpaExcel()"):
            i = APP_JS.index(fn)
            assert "_dpaQueryString()" in APP_JS[i:i + 300], fn


class TestCalendar:
    def test_the_export_accepts_the_type_the_screen_filters_by(self):
        assert "event_type" in params(crm.export_calendar_excel)

    def test_it_applies_it(self):
        src = inspect.getsource(crm.export_calendar_excel)
        assert "CalendarEvent.event_type == event_type" in src

    def test_the_screen_and_the_file_filter_on_the_same_column(self):
        """The screen filters inside _calendar_rows, which the export cannot
        reuse — it deliberately excludes the task/reminder overlays. So the
        one thing to pin is that both narrow on the same column."""
        rows = inspect.getsource(crm._calendar_rows)
        assert "CalendarEvent.event_type == event_type" in rows
        assert "CalendarEvent.event_type == event_type" in \
            inspect.getsource(crm.export_calendar_excel)

    def test_the_export_still_excludes_the_overlays(self):
        """Tasks and reminders are drawn on the calendar but are not
        appointments; the file is appointments."""
        assert "include_overlay" not in inspect.getsource(crm.export_calendar_excel)

    def test_the_button_sends_the_current_type(self):
        i = APP_JS.index("function exportCalendarExcel()")
        block = APP_JS[i:i + 700]
        assert "_calType" in block and "event_type=" in block

    def test_it_sends_nothing_when_no_type_is_chosen(self):
        i = APP_JS.index("function exportCalendarExcel()")
        assert "_calType ?" in APP_JS[i:i + 700]


class TestProperties:
    """لیست املاک — the last of the six, and the one whose gap was partial:
    four of its six filters travelled and the two rental bands did not."""

    def _export(self):
        from app.api.routes import properties
        return properties.export_properties_excel

    def test_the_export_accepts_both_rental_bands(self):
        got = params(self._export())
        for p in ("min_deposit", "max_deposit", "min_rent_price", "max_rent_price"):
            assert p in got, p

    def test_it_still_accepts_the_four_that_already_worked(self):
        got = params(self._export())
        for p in ("city", "category", "listing_type", "search"):
            assert p in got, p

    def test_it_applies_them(self):
        src = inspect.getsource(self._export())
        assert "Property.deposit >= min_deposit" in src
        assert "Property.deposit <= max_deposit" in src
        assert "Property.rent_price >= min_rent_price" in src
        assert "Property.rent_price <= max_rent_price" in src

    def test_the_export_and_the_list_name_the_bands_identically(self):
        """min_rent_price, not min_rent — one letter apart from the scraper's
        own name for the same idea, and a mismatch here is silent."""
        from app.api.routes import properties
        listed = set(params(properties.list_properties)) if hasattr(
            properties, "list_properties") else None
        if listed:
            for p in ("min_deposit", "max_deposit", "min_rent_price", "max_rent_price"):
                assert p in listed, p

    def test_the_button_sends_all_four(self):
        i = APP_JS.index("function exportPropertiesExcel()")
        block = APP_JS[i:i + 1400]
        for p in ("min_deposit", "max_deposit", "min_rent_price", "max_rent_price"):
            assert p in block, p

    def test_it_reads_the_same_controls_the_list_does(self):
        i = APP_JS.index("function exportPropertiesExcel()")
        block = APP_JS[i:i + 1400]
        for el in ("filter-min-deposit", "filter-max-deposit",
                   "filter-min-rent", "filter-max-rent"):
            assert el in block, el


class TestEverySurfaceIsCovered:
    """The audit that found these, kept as a list so a new export cannot be
    added without someone deciding whether it needs filters."""

    SURFACES = ("leads", "customers", "contacts", "deals", "dpa",
                "calendar", "properties")

    def test_each_has_an_export_that_takes_at_least_one_filter(self):
        from app.api.routes import properties
        fns = {
            "leads": crm.export_leads_excel,
            "customers": crm.export_customers_excel,
            "contacts": crm.export_contacts_excel,
            "deals": crm.export_deals_excel,
            "dpa": crm.export_dpa_excel,
            "calendar": crm.export_calendar_excel,
            "properties": properties.export_properties_excel,
        }
        for name in self.SURFACES:
            assert params(fns[name]), f"{name} export takes no filters"
