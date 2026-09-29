"""
Prices, areas and deposits the way Divar writes them.

Divar prints numbers with Persian punctuation: «۶٬۲۰۰٬۰۰۰٬۰۰۰ تومان» uses the
Arabic thousands separator «٬» (U+066C), and the compact forms use the Arabic
decimal separator «٫» (U+066B), «۱٫۸ میلیارد». Neither was normalised, so the
digit regex stopped at the first separator and the price came out as its first
digit group:

    «۱٫۸ میلیارد»                → 1 000 000 000     (should be 1 800 000 000)
    «۱٬۲۰۰ میلیون»               → 1 000 000         (should be 1 200 000 000)
    «۲ میلیارد و ۵۰۰ میلیون»      → 2 000 000 000     (should be 2 500 000 000)
    «۱٬۵۰۰٬۰۰۰ تومان»            → 1                 (should be 1 500 000)

Every one of those is a price that lands in a filter band, so a wrong figure is
either a listing thrown away or one that should have been thrown away and was
kept.
"""
import os
import sys

import pytest
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.scraper.parsers import (  # noqa: E402
    enrich_price_from_features,
    extract_price_info,
    extract_property_details,
    normalize_persian_digits,
    parse_persian_number,
    parse_price_with_unit,
)


class TestNormalisingTheSeparators:
    def test_the_arabic_decimal_separator_becomes_a_dot(self):
        assert normalize_persian_digits("۱٫۸") == "1.8"

    def test_the_arabic_thousands_separator_becomes_a_comma(self):
        assert normalize_persian_digits("۱٬۲۰۰") == "1,200"

    def test_both_in_one_number(self):
        assert normalize_persian_digits("۱٬۲۰۰٫۵") == "1,200.5"

    def test_the_persian_comma_between_list_items_is_left_alone(self):
        """«،» separates words, and callers strip it from the ends of cells."""
        assert normalize_persian_digits("سه خواب، پارکینگ") == "سه خواب، پارکینگ"


class TestPricesWithAUnit:
    @pytest.mark.parametrize("text,expected", [
        # the four the review reproduced
        ("۱٫۸ میلیارد", 1_800_000_000),
        ("۱٬۲۰۰ میلیون", 1_200_000_000),
        ("۲ میلیارد و ۵۰۰ میلیون", 2_500_000_000),
        ("۱٬۵۰۰٬۰۰۰ تومان", 1_500_000),
        # the same numbers typed with ASCII punctuation
        ("1,200 میلیون", 1_200_000_000),
        ("1.8 میلیارد", 1_800_000_000),
        ("2 میلیارد و 500 میلیون", 2_500_000_000),
        # a price built from more than one unit
        ("۳ میلیون و ۵۰۰ هزار تومان", 3_500_000),
        ("۱ میلیارد و ۲۰۰ میلیون و ۵۰ هزار", 1_200_050_000),
        ("۲ میلیارد ۵۰۰ میلیون", 2_500_000_000),
        # what already worked
        ("۹۰۰ میلیون", 900_000_000),
        ("۹۵۰ میلیون تومان", 950_000_000),
        ("۲ میلیارد", 2_000_000_000),
        ("۵۰۰ هزار", 500_000),
        ("۱٫۵ میلیون", 1_500_000),
        ("۱.۸۰۰ میلیارد", 1_800_000_000),
        ("۱٫۸۰۰ میلیارد", 1_800_000_000),
        ("500000000", 500_000_000),
    ])
    def test_it_reads_what_a_person_reads(self, text, expected):
        assert parse_price_with_unit(text) == expected

    def test_two_separate_prices_in_one_text_are_not_added_together(self):
        """Only a price written as one figure — descending units, joined by
        «و» — is summed. «۲۰۰ میلیون … ۵ میلیون» is two figures."""
        assert parse_price_with_unit("ودیعه ۲۰۰ میلیون - اجاره ۵ میلیون") == 200_000_000

    def test_a_number_that_belongs_to_something_else_is_not_the_price(self):
        assert parse_price_with_unit("۲ خوابه ۹۰۰ میلیون") == 900_000_000

    @pytest.mark.parametrize("text", ["رایگان", "مجانی"])
    def test_free_is_zero(self, text):
        assert parse_price_with_unit(text) == 0

    @pytest.mark.parametrize("text", ["توافقی", "قیمت توافقی", "", None, "میلیون"])
    def test_no_figure_is_none(self, text):
        assert parse_price_with_unit(text) is None


class TestPricesWithoutAUnit:
    @pytest.mark.parametrize("text,expected", [
        ("۶٬۲۰۰٬۰۰۰٬۰۰۰ تومان", 6_200_000_000),
        ("6,200,000,000 تومان", 6_200_000_000),
        ("۴۵۰,۰۰۰,۰۰۰", 450_000_000),
        ("۱.۲۰۰.۰۰۰ تومان", 1_200_000),
        ("۲۵۰۰۰۰۰۰", 25_000_000),
    ])
    def test_grouped_digits_are_one_number(self, text, expected):
        assert parse_price_with_unit(text) == expected

    def test_a_lone_dot_before_three_digits_is_a_thousands_mark(self):
        """«۵۰۰.۰۰۰ تومان» is five hundred thousand, not five hundred."""
        assert parse_price_with_unit("۵۰۰.۰۰۰ تومان") == 500_000


class TestThePriceRows:
    """The same figures, read through the page rows the scraper actually uses."""

    @staticmethod
    def _row(title, value):
        return (f'<div class="kt-base-row kt-unexpandable-row">'
                f'<p class="kt-base-row__title kt-unexpandable-row__title">{title}</p>'
                f'<p class="kt-unexpandable-row__value">{value}</p></div>')

    def _read(self, *rows):
        return extract_price_info(BeautifulSoup("".join(self._row(*r) for r in rows), "html.parser"))

    def test_a_compound_total_price(self):
        got = self._read(("قیمت کل", "۲ میلیارد و ۵۰۰ میلیون تومان"))
        assert got["total_price"] == 2_500_000_000
        assert got["price"] == 2_500_000_000

    def test_a_decimal_price_per_metre(self):
        assert self._read(("قیمت هر متر", "۴۵٫۵ میلیون تومان"))["price_per_meter"] == 45_500_000

    def test_a_grouped_deposit_with_a_unit(self):
        got = self._read(("ودیعه", "۱٬۲۰۰ میلیون تومان"), ("اجارهٔ ماهانه", "۲۵ میلیون تومان"))
        assert got["deposit"] == 1_200_000_000
        assert got["rent_price"] == 25_000_000

    def test_full_digits_with_the_arabic_separator(self):
        got = self._read(("قیمت کل", "۶٬۲۰۰٬۰۰۰٬۰۰۰ تومان"), ("قیمت هر متر", "۷۲٬۰۰۰٬۰۰۰ تومان"))
        assert got["total_price"] == 6_200_000_000
        assert got["price_per_meter"] == 72_000_000

    def test_a_free_rent_is_zero_and_not_missing(self):
        got = self._read(("ودیعه", "۹۰۰٬۰۰۰٬۰۰۰ تومان"), ("اجارهٔ ماهانه", "مجانی"))
        assert got["deposit"] == 900_000_000
        assert got["rent_price"] == 0


class TestTheFeaturesFallback:
    """enrich_price_from_features reads the same way and had the same bug."""

    def test_a_deposit_and_a_rent_with_the_arabic_separator(self):
        data = {"features": ["ودیعه و اجاره", "۲٬۰۰۰٬۰۰۰٬۰۰۰ تومان", "۴۵٬۰۰۰٬۰۰۰ تومان"]}
        enrich_price_from_features(data)
        assert data["deposit"] == 2_000_000_000
        assert data["rent_price"] == 45_000_000

    def test_compact_figures(self):
        data = {"features": ["ودیعه قابل تبدیل", "۱٫۲ میلیارد", "۳۵ میلیون"]}
        enrich_price_from_features(data)
        assert data["deposit"] == 1_200_000_000
        assert data["rent_price"] == 35_000_000

    def test_a_row_already_read_is_not_overwritten(self):
        data = {"deposit": 500_000_000, "rent_price": 10_000_000,
                "features": ["ودیعه", "۱٬۵۰۰٬۰۰۰ تومان"]}
        enrich_price_from_features(data)
        assert (data["deposit"], data["rent_price"]) == (500_000_000, 10_000_000)


class TestAreas:
    """The area is read with parse_persian_number, which threw every separator
    away — including the decimal one."""

    @staticmethod
    def _details(value, title="متراژ"):
        html = (f'<div class="kt-base-row kt-unexpandable-row">'
                f'<p class="kt-base-row__title kt-unexpandable-row__title">{title}</p>'
                f'<p class="kt-unexpandable-row__value">{value}</p></div>')
        return extract_property_details(BeautifulSoup(html, "html.parser"))

    def test_a_decimal_area_is_not_ten_times_too_big(self):
        """«۹۲٫۵ متر» read as 925 would sail through a max-area filter."""
        assert self._details("۹۲٫۵ متر")["area"] == 93

    def test_a_grouped_land_area(self):
        assert self._details("۲٬۵۰۰ متر", title="متراژ زمین")["land_area"] == 2500

    def test_a_plain_area(self):
        assert self._details("۸۵ متر")["area"] == 85

    @pytest.mark.parametrize("text,expected", [
        ("۹۲٫۵", 93), ("۱۲۰٫۲۵ متر", 120), ("۲٬۵۰۰", 2500), ("1,234,567", 1234567),
        ("۱.۵۰۰", 1500), ("500 000", 500000), ("۰۹۰۰۰۰۰۰۰۰۰", 9000000000),
    ])
    def test_parse_persian_number(self, text, expected):
        assert parse_persian_number(text) == expected

    def test_two_numbers_in_one_cell_are_still_concatenated_as_before(self):
        """«۳ از ۶» is split by the caller on «از»; this must not change."""
        assert parse_persian_number("۳ از ۶") == 36
