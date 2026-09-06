"""
«هرجایی که اطلاعات ثبت میشه حتما درج بشه که این املاکیه.»

Four places show a scraped listing: the property table, the property modal,
the CRM lead table, and the CRM lead modal. A label present in three of them
is worse than none, because the fourth then reads as «checked, and clean».

Divar's declaration and ours are shown as two answers rather than one
merged one. Red is the disagreement — the ad arrived through a «شخصی»
filter and should not have.
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_JS = open(os.path.join(ROOT, "frontend/js/app.js"), encoding="utf-8").read()
INDEX = open(os.path.join(ROOT, "frontend/index.html"), encoding="utf-8").read()

BADGE = APP_JS[APP_JS.index("function agencyBadge(p) {"):]
BADGE = BADGE[:BADGE.index("\n}\n") + 2]


class TestTheBadgeItself:
    def test_a_clean_listing_shows_nothing(self):
        """Not «شخصی» — nothing. Every row carrying a label makes the label
        invisible."""
        assert "if (!p || !p.agency_suspected) return '';" in BADGE

    def test_the_disagreement_with_divar_is_the_loud_case(self):
        assert "bg-danger" in BADGE
        assert "'personal'" in BADGE

    def test_an_agency_that_declared_itself_is_quiet(self):
        assert "bg-secondary" in BADGE

    def test_the_evidence_is_in_the_tooltip(self):
        """A label nobody can check is a label nobody will trust."""
        assert "agency_evidence" in BADGE
        assert "title=" in BADGE

    def test_the_tooltip_says_divar_disagreed(self):
        assert "دیوار آن را «شخصی» ثبت کرده" in BADGE

    def test_the_tooltip_is_escaped(self):
        """The phrase comes from an ad someone else wrote."""
        assert "esc(title)" in BADGE

    def test_it_uses_the_users_own_word(self):
        assert ">املاکی</span>" in BADGE


class TestAllFourPlacesShowIt:
    def test_the_property_table_row(self):
        i = APP_JS.index("${esc(property.title.substring(0, 40))}")
        assert "agencyBadge(property)" in APP_JS[i:i + 200]

    def test_the_property_modal(self):
        """_renderPropertyDetails is shared with the CRM lead modal, so this
        one covers two of the four."""
        assert "آگهی‌دهنده (متن آگهی)" in APP_JS

    def test_the_crm_lead_table_row(self):
        i = APP_JS.index("${esc((lead.property_title || '---').substring(0, 35))}")
        assert "agencyBadge(lead)" in APP_JS[i:i + 200]

    def test_the_crm_lead_modal_header(self):
        i = APP_JS.index("<label class=\"text-muted small\">عنوان ملک</label>")
        assert "agencyBadge(lead)" in APP_JS[i:i + 300]

    def test_the_badge_is_used_in_at_least_four_spots(self):
        """Three call sites plus the one inside the modal's own row."""
        assert APP_JS.count("agencyBadge(") >= 5   # 1 definition + 4 uses


class TestDivarsAnswerIsNotOverwritten:
    def test_divars_own_field_is_still_shown(self):
        assert "آگهی‌دهنده (دیوار)" in APP_JS

    def test_it_is_labelled_as_divars(self):
        """Two answers, each attributed. Merging them would hide the one
        thing worth seeing."""
        i = APP_JS.index("آگهی‌دهنده (دیوار)")
        assert "آگهی‌دهنده (متن آگهی)" in APP_JS[i:i + 700]

    def test_ours_is_blank_when_there_is_nothing_to_say(self):
        i = APP_JS.index("آگهی‌دهنده (متن آگهی)")
        assert "p.agency_suspected" in APP_JS[i:i + 400]


class TestThePanelWillBeReloaded:
    def test_the_cache_buster_moved(self):
        m = re.search(r"js/app\.js\?v=([0-9a-z]+)", INDEX)
        assert m and m.group(1) != "20260906b"
