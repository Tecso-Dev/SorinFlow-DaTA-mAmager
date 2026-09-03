"""
Wiring for the «هوش تصویری» page.

A dashboard fails quietly. getElementById returns null for a typo, the line
throws, and the rest of the render never runs — so half the page is blank and
nothing anywhere says why. These parse the markup and the script and assert
that every id the code reaches for actually exists, that the section is
registered everywhere a section has to be registered, and that the page keeps
its one rule: a value the server could not compute renders «—», not zero.
"""
import os
import re

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_JS = open(os.path.join(BASE, "frontend", "js", "app.js"), encoding="utf-8").read()
INDEX = open(os.path.join(BASE, "frontend", "index.html"), encoding="utf-8").read()

# The insights half of app.js — everything after its banner.
INS_JS = APP_JS[APP_JS.index("هوش تصویری — CRM insights"):]


def html_ids():
    return set(re.findall(r'id="([^"]+)"', INDEX))


class TestTheSectionIsRegisteredEverywhere:
    """Four separate places. Miss one and the page is unreachable, or
    reachable but never loads, or loads for accounts that should not see it."""

    def test_it_has_a_title_and_subtitle(self):
        assert re.search(r"insights:\s*\{\s*title:", APP_JS)

    def test_it_is_permission_gated(self):
        """CRM data, so it rides the CRM permission rather than being open."""
        assert re.search(r"insights:\s*'crm'", APP_JS)

    def test_the_router_loads_it(self):
        assert "case 'insights':" in APP_JS
        i = APP_JS.index("case 'insights':")
        assert "loadInsights()" in APP_JS[i:i + 120]

    def test_the_nav_link_exists(self):
        assert "showSection('insights')" in INDEX
        assert 'id="nav-link-insights"' in INDEX

    def test_the_section_container_exists(self):
        assert 'id="section-insights"' in INDEX

    def test_the_container_starts_hidden(self):
        i = INDEX.index('id="section-insights"')
        assert 'display:none' in INDEX[i:i + 120]

    def test_the_nav_id_matches_the_section_name(self):
        """showSection() derives nav-link-<name> and section-<name>; a mismatch
        highlights the wrong row or shows nothing."""
        assert 'id="nav-link-insights"' in INDEX
        assert 'id="section-insights"' in INDEX


class TestEveryIdTheScriptTouchesExists:
    def test_no_getelementbyid_points_at_nothing(self):
        ids = html_ids()
        missing = sorted({
            m for m in re.findall(r"getElementById\('([^']+)'\)", INS_JS)
            if m not in ids
        })
        assert not missing, f"the insights code reaches for ids that do not exist: {missing}"

    def test_the_canvases_the_charts_bind_to_exist(self):
        ids = html_ids()
        for canvas in ("ins-temp-chart", "ins-trend-chart", "ins-city-chart"):
            assert canvas in ids, f"missing canvas: {canvas}"
            assert re.search(rf'<canvas[^>]+id="{canvas}"', INDEX), \
                f"{canvas} exists but is not a <canvas>"

    def test_the_tables_the_rows_are_written_into_exist(self):
        ids = html_ids()
        assert "ins-agents" in ids and "ins-stalled-list" in ids

    def test_the_window_selector_exists_and_reloads(self):
        assert 'id="ins-window"' in INDEX
        i = INDEX.index('id="ins-window"')
        assert "loadInsights()" in INDEX[i:i + 200]


class TestUnknownsRenderAsDashesNotZeros:
    """The one failure a dashboard cannot recover from: a zero that is really
    an unknown looks exactly like an answer."""

    def test_the_number_formatter_guards_null(self):
        i = INS_JS.index("function _insNum")
        body = INS_JS[i:i + 220]
        assert "null" in body and "'—'" in body

    def test_the_percent_formatter_guards_null(self):
        i = INS_JS.index("function _insPct")
        body = INS_JS[i:i + 220]
        assert "null" in body and "'—'" in body

    def test_the_money_formatter_guards_null(self):
        i = INS_JS.index("function _insToman")
        body = INS_JS[i:i + 260]
        assert "null" in body and "'—'" in body

    def test_showings_per_close_is_dashed_when_absent(self):
        assert "a.showings_per_close === null ? '—'" in INS_JS

    def test_the_headline_cells_start_as_dashes(self):
        """Before the first response lands, the page must not read as zeros."""
        for el in ("ins-leads", "ins-conv", "ins-stalled", "ins-commission"):
            i = INDEX.index(f'id="{el}"')
            assert "—" in INDEX[i:i + 60], f"{el} does not start as a dash"


class TestItSurvivesEmptyAndHostileData:
    def test_every_render_helper_defaults_its_argument(self):
        """A missing key in the payload must not throw and blank the page."""
        for call, default in [("_insRenderFunnel", "|| []"),
                              ("_insRenderTemp", "|| []"),
                              ("_insRenderTrend", "|| {}"),
                              ("_insRenderCities", "|| []"),
                              ("_insRenderAgents", "|| []"),
                              ("_insRenderStalled", "|| {}")]:
            i = INS_JS.index(f"{call}(d.")
            assert default in INS_JS[i:i + 60], f"{call} does not default its argument"

    def test_empty_tables_say_so_rather_than_showing_nothing(self):
        assert "هنوز عملکرد روزانه‌ای ثبت نشده است" in INS_JS
        assert "هیچ لید معطلی نیست" in INS_JS

    def test_an_empty_funnel_says_so(self):
        assert "هنوز لیدی ثبت نشده است" in INS_JS

    def test_a_failed_request_does_not_leave_a_half_drawn_page(self):
        i = INS_JS.index("async function loadInsights")
        body = INS_JS[i:i + 700]
        assert "catch" in body and "return" in body


class TestUserContentIsEscaped:
    """Agent names, city names and seller names are operator-entered."""

    @pytest.mark.parametrize("field", [
        "a.agent", "l.city_name", "l.status_label",
    ])
    def test_field_is_escaped(self, field):
        assert f"esc({field}" in INS_JS, f"{field} is interpolated unescaped"

    def test_the_funnel_label_is_escaped(self):
        assert "esc(s.label)" in INS_JS

    def test_the_seller_name_is_escaped(self):
        assert "esc(l.seller_name" in INS_JS


class TestChartsAreReplacedNotStacked:
    """Re-rendering without destroying leaks a canvas and makes the tooltip
    read from whichever chart answers first."""

    @pytest.mark.parametrize("chart", ["insTempChart", "insTrendChart", "insCityChart"])
    def test_the_previous_chart_is_destroyed(self, chart):
        assert f"if ({chart}) {chart}.destroy()" in INS_JS

    def test_charts_follow_the_panel_theme(self):
        assert "chartColors()" in INS_JS
