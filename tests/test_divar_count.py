"""
«چند آگهی با این فیلترها هست؟» — the mapping onto Divar's search API.

Divar prints this above its own results and its API carries the same number at
map_data.post_count. The risk is not the request, it is the mapping: our
category slugs are not Divar's tokens («rent-residential» is «residential-rent»
there, «buy-store» is «shop-sell»), and a filter Divar silently ignores would
turn the estimate into a promise the scrape cannot keep.

Every token below was read off Divar itself and confirmed to return a count.
Pure — no network.
"""
import ast
import pytest

from app.services.divar_count import (
    CATEGORY_TOKENS, BUSINESS_TYPES, build_form_data, unsupported_filters,
)


class TestCategoryTokens:
    def test_every_config_category_is_mapped(self):
        """A category the scraper offers but cannot count would fail silently."""
        src = open("app/config.py", encoding="utf-8").read()
        cats = {}
        for node in ast.parse(src).body:
            if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "CATEGORIES":
                ns = {}
                exec(compile(ast.Module([node], []), "<c>", "exec"), ns)
                cats = ns["CATEGORIES"]
        assert cats, "CATEGORIES not found in app/config.py"
        missing = [s for s in cats if s not in CATEGORY_TOKENS]
        assert not missing, f"unmapped categories: {missing}"

    def test_tokens_are_distinct(self):
        """buy-villa and rent-villa must not collapse onto one token — they did
        while both resolved to the parent «villa»."""
        assert CATEGORY_TOKENS["buy-villa"] != CATEGORY_TOKENS["rent-villa"]
        assert len(set(CATEGORY_TOKENS.values())) == len(CATEGORY_TOKENS)

    @pytest.mark.parametrize("slug,token", [
        ("rent-residential", "residential-rent"),
        ("buy-residential", "residential-sell"),
        ("buy-villa", "house-villa-sell"),
        ("rent-villa", "house-villa-rent"),
        ("buy-old-house", "plot-old"),
        ("buy-store", "shop-sell"),
        ("rent-store", "shop-rent"),
        ("buy-commercial-property", "commercial-sell"),
    ])
    def test_known_tokens(self, slug, token):
        assert CATEGORY_TOKENS[slug] == token


class TestBuildFormData:
    def test_category_only(self):
        assert build_form_data("rent-residential") == {
            "category": {"str": {"value": "residential-rent"}}}

    def test_unknown_category_is_dropped(self):
        assert build_form_data("not-a-category") == {}

    def test_personal(self):
        f = build_form_data("rent-residential", advertiser_type="personal")
        assert f["business-type"] == {"repeated_string": {"value": ["personal"]}}

    def test_agency_uses_divars_word(self):
        f = build_form_data("rent-residential", advertiser_type="agency")
        assert f["business-type"]["repeated_string"]["value"] == ["real-estate-business"]

    def test_business_type_is_repeated_string(self):
        """A plain str is accepted by the API and silently ignored — the count
        comes back unfiltered, which is worse than an error."""
        f = build_form_data("rent-residential", advertiser_type="personal")
        assert "repeated_string" in f["business-type"]
        assert "str" not in f["business-type"]

    def test_unknown_advertiser_type_is_dropped(self):
        assert "business-type" not in build_form_data("rent-residential", advertiser_type="both")

    def test_deposit_maps_to_credit(self):
        f = build_form_data("rent-residential", max_deposit=100_000_000)
        assert f["credit"] == {"number_range": {"maximum": "100000000"}}

    def test_area_maps_to_size(self):
        f = build_form_data("buy-apartment", min_area=100, max_area=150)
        assert f["size"] == {"number_range": {"minimum": "100", "maximum": "150"}}

    def test_price_band(self):
        f = build_form_data("buy-residential", min_price=5_000_000_000, max_price=10_000_000_000)
        assert f["price"] == {"number_range":
                              {"minimum": "5000000000", "maximum": "10000000000"}}

    def test_numbers_are_sent_as_strings(self):
        f = build_form_data("buy-residential", min_price=5)
        assert f["price"]["number_range"]["minimum"] == "5"

    def test_open_ended_band(self):
        f = build_form_data("rent-residential", min_rent=1_000_000)
        assert f["rent"]["number_range"] == {"minimum": "1000000"}

    def test_no_band_when_both_ends_empty(self):
        assert "price" not in build_form_data("buy-residential")

    def test_has_photo_only_when_true(self):
        assert "has-photo" in build_form_data("buy-residential", has_images=True)
        assert "has-photo" not in build_form_data("buy-residential", has_images=False)
        assert "has-photo" not in build_form_data("buy-residential", has_images=None)

    def test_everything_together(self):
        f = build_form_data("rent-residential", advertiser_type="personal",
                            has_images=True, max_deposit=100_000_000, min_area=80)
        assert set(f) == {"category", "business-type", "has-photo", "credit", "size"}

    def test_filters_divar_cannot_apply_are_not_sent(self):
        """Sending them would not narrow anything and would overstate the count."""
        f = build_form_data("buy-apartment")
        assert "rooms" not in f and "has-parking" not in f


class TestUnsupportedFilters:
    def test_names_what_divar_will_not_narrow_on(self):
        out = unsupported_filters(min_rooms=2, has_parking=True, max_price_per_meter=9)
        assert "حداقل اتاق" in out and "پارکینگ" in out and "قیمت هر متر" in out

    def test_nothing_when_none_are_set(self):
        assert unsupported_filters(min_rooms=None, has_parking=False) == []

    def test_false_is_not_a_filter(self):
        assert unsupported_filters(has_elevator=False, has_storage=False) == []


class TestBusinessTypes:
    def test_both_kinds_known(self):
        assert set(BUSINESS_TYPES) == {"personal", "agency"}
        assert BUSINESS_TYPES["agency"] == "real-estate-business"
