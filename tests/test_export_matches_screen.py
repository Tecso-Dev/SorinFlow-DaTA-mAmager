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

    def test_only_dpa_still_uses_it(self):
        import re
        used = set(re.findall(r"exportExcel\('([a-z]+)'\)", INDEX))
        assert used <= {"dpa"}, f"a filtered list is back on the generic export: {used}"
