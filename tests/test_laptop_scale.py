"""
One panel, every laptop.

A 1366 laptop at Windows' 125% zoom hands the panel 1093 CSS pixels; a
27" monitor hands it 2560. The same sheet served both, so the small one
got a quarter of its width taken by the sidebar and a price column that
wrapped into four lines, and the big one got 13.5px text spread across
the whole screen. Every size in the sheet is rem, so the html font-size
is the one knob — these tests pin the knob and the two layout fixes.
"""
import re
from pathlib import Path

CSS = Path("frontend/css/style.css").read_text(encoding="utf-8")


def _block(query):
    i = CSS.index(query)
    depth, j = 0, i
    while True:
        c = CSS[j]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return CSS[i:j + 1]
        j += 1


class TestTheKnob:

    def test_body_text_is_rem_so_the_html_size_scales_it(self):
        body = _block("\nbody {")
        assert "font-size: .84375rem" in body, "13.5px would ignore the html font-size"
        assert not re.search(r"font-size:\s*13\.5px", body)

    def test_small_laptops_get_a_smaller_scale_and_a_narrower_sidebar(self):
        blk = _block("@media (min-width: 901px) and (max-width: 1199px)")
        assert "html { font-size: 15px }" in blk
        assert "--sidebar-w: 216px" in blk
        assert "#crm-main-tabs .nav-link" in blk, "eleven tabs must not wrap into a wall"

    def test_wide_screens_get_a_larger_scale_and_a_capped_content_width(self):
        blk = _block("@media (min-width: 1600px)")
        assert "html { font-size: 17px }" in blk
        assert "max-width: 1720px" in blk and "margin: 0 auto" in blk
        assert "html { font-size: 18px }" in _block("@media (min-width: 2200px)")

    def test_phones_are_untouched(self):
        # the laptop rules start where the sidebar overlay ends
        assert "@media (min-width: 901px) and (max-width: 1199px)" in CSS
        assert "@media (max-width: 900px)" in CSS
        for blk in ("@media (min-width: 1600px)", "@media (min-width: 2200px)"):
            assert "#sidebar" not in _block(blk)


class TestThePriceColumn:

    def test_a_price_never_wraps_but_the_title_still_may(self):
        blk = _block("@media (min-width: 901px) {\n  #section-properties")
        assert "#section-properties .table td { white-space: nowrap }" in blk
        assert "td:nth-child(2) { white-space: normal" in blk
        # the title is the second column
        html = Path("frontend/index.html").read_text(encoding="utf-8")
        head = html[html.index('id="properties-table"') - 900:html.index('id="properties-table"')]
        ths = re.findall(r"<th>([^<]*)</th>", head)
        assert ths[:2] == ["کد ملک", "عنوان"], ths


class TestTheUsersList:
    """Ten columns in a two-thirds card scrolled sideways and cut the username
    off one end and the actions off the other (screenshot, 2026-09-20). Now a
    full-width members table — who, role, access, contact, state, last login,
    a menu — with the create form in a modal, and stacked cards below 900px."""

    def test_it_is_a_full_width_table_with_the_form_in_a_modal(self):
        html = Path("frontend/index.html").read_text(encoding="utf-8")
        sec = html[html.index('id="section-users"'):html.index('<!-- /.content-wrapper -->')]
        assert 'class="table users-table"' in sec and 'id="users-table"' in sec
        assert "table-responsive" not in sec[sec.index("users-card"):]
        assert 'id="user-create-form"' not in sec, "the form lives in the modal now"
        modal = html[html.index('id="newUserModal"'):html.index('id="cabinetModal"')]
        assert 'id="user-create-form"' in modal and 'id="new-perms-box"' in modal
        assert 'onclick="openNewUserModal()"' in sec

    def test_search_and_filters_are_client_side(self):
        js = Path("frontend/js/app.js").read_text(encoding="utf-8")
        fn = js[js.index("function renderUsers()"):js.index("function _userRow(u)")]
        assert "users-search" in fn and "users-role-filter" in fn and "users-state-filter" in fn
        assert "_usersAll.filter(" in fn
        assert 'id="users-count"' in Path("frontend/index.html").read_text(encoding="utf-8")

    def test_one_menu_per_row_instead_of_four_buttons(self):
        js = Path("frontend/js/app.js").read_text(encoding="utf-8")
        fn = js[js.index("function _userRow(u)"):js.index("// Change an existing account's role")]
        assert 'data-bs-toggle="dropdown"' in fn and "dropdown-item" in fn and fn.count("item(`") >= 4
        assert "btn-outline-warning" not in fn and "btn-outline-danger" not in fn
        # root is never edited from here; nobody deletes their own account
        assert "u.role !== 'root' ? item(`openPermsEditor" in fn
        assert "!isSelf && u.role !== 'root' ? '<li><hr class=\"dropdown-divider\"></li>' +" in fn

    def test_the_pills_are_quiet_and_the_menu_is_dark(self):
        assert ".pill.role-root" in CSS and ".pill.role-visitor" in CSS
        blk = _block(".pill {")
        assert "color-mix(in srgb, var(--p) 12%, transparent)" in blk
        assert "--bs-dropdown-bg: var(--surface2)" in _block(".dropdown-menu {")
        assert "--bs-" not in _block(".u-kebab {")

    def test_below_900px_the_rows_are_cards(self):
        blk = CSS[CSS.index("/* narrow: each row becomes a card"):]
        blk = blk[:blk.index("/* ── Permission toggles")]
        assert "@media (max-width: 900px)" in blk
        assert ".users-table thead { display: none; }" in blk
        assert "content: attr(data-l)" in blk
        js = Path("frontend/js/app.js").read_text(encoding="utf-8")
        assert 'data-l="تماس"' in js and 'data-l="نقش"' in js


class TestTheAuditOfTheOtherSections:
    """2026-09-20: every section and every CRM tab measured at 1093px with
    seeded data. Two tables still scrolled sideways — scraper jobs (nine
    columns in a two-thirds card) and CRM leads (eleven columns). Both fit
    now; the leads list also gained a menu like the members table."""

    def test_the_leads_table_has_nine_columns_and_no_scroll_wrapper(self):
        html = Path("frontend/index.html").read_text(encoding="utf-8")
        i = html.index('id="crm-leads-table"')
        head = html[html.rindex('<table class="table leads-table">', 0, i):i]
        assert head.count("<th ") + head.count("<th>") == 9   # "<thead" is not a column
        assert "<th>شهر</th>" not in head and "اطلاع‌رسانی</th>" not in head, "the city rides under the title, the notice under the date"
        assert "table-responsive" not in html[i - 1500:i]
        js = Path("frontend/js/app.js").read_text(encoding="utf-8")
        fn = js[js.index("async function loadLeads()"):js.index("_renderLeadsPagination(data.total ?? data.items.length)")]
        assert 'colspan="9"' in fn and "const where = [lead.city_name" in fn
        assert 'data-bs-toggle="dropdown"' in fn and "deleteLead(${lead.id})" in fn
        assert "moveFilePick(${lead.property_id})" in fn, "a lead files its property from the list too"

    def test_the_specs_column_steps_aside_on_small_laptops(self):
        blk = _block("@media (max-width: 1199px) {\n  .leads-table .leads-spec")
        assert ".leads-table .leads-spec { display: none; }" in blk

    def test_the_jobs_table_keeps_its_markup_but_fits(self):
        js = Path("frontend/js/app.js").read_text(encoding="utf-8")
        fn = js[js.index("function _renderJobsTable"):js.index("const _dismissedOtpKeys")]
        assert 'class="job-id"' in fn and 'class="job-reason"' in fn and 'class="job-actions"' in fn
        assert "max-width:190px" not in fn, "the reason is clamped by CSS now"
        blk = _block("@media (min-width: 901px) and (max-width: 1399px) {\n  /* the id is a tooltip-only detail")
        assert "td:first-child { display: none; }" in blk
        assert '<table class="table jobs-table">' in Path("frontend/index.html").read_text(encoding="utf-8")


class TestDealsNoLongerFiveHundred:
    """Found by the audit: Deal.to_dict read self.buyer / self.seller, lazy
    relationships on an async session — MissingGreenlet, and the whole
    «معاملات» tab answered 500 for any deal that had a contact."""

    def test_to_dict_reads_only_what_is_loaded(self):
        src = Path("app/models/crm_models.py").read_text(encoding="utf-8")
        fn = src[src.index("class Deal(Base)"):src.index("class Note(Base)")]
        assert 'self.__dict__.get("buyer")' in fn and "self.buyer.name" not in fn

    def test_the_list_loads_the_contacts_up_front(self):
        src = Path("app/api/routes/crm.py").read_text(encoding="utf-8")
        fn = src[src.index("async def list_deals"):src.index('@router.post("/deals")')]
        assert "selectinload(Deal.buyer), selectinload(Deal.seller)" in fn

    def test_the_table_shows_names_not_ids(self):
        js = Path("frontend/js/app.js").read_text(encoding="utf-8")
        assert "d.buyer_name ? esc(d.buyer_name)" in js and "d.seller_name ? esc(d.seller_name)" in js

    def test_through_the_model(self):
        from app.models.crm_models import Deal
        d = Deal(title="x", buyer_contact_id=1, seller_contact_id=2)
        row = d.to_dict()      # nothing loaded → nothing touched, no IO
        assert row["buyer_name"] is None and row["buyer_contact_id"] == 1
