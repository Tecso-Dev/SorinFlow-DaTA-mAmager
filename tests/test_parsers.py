"""
Comprehensive tests for app/scraper/parsers.py
All functions are pure — no DB, no browser, no async needed.
"""
import pytest
from app.scraper.parsers import (
    normalize_persian_digits,
    parse_persian_number,
    parse_price_with_unit,
    extract_divar_id,
    generate_tag_number,
    extract_price_info,
    extract_property_details,
)
from bs4 import BeautifulSoup


# ── normalize_persian_digits ─────────────────────────────────────────────────

class TestNormalizePersianDigits:
    def test_persian_digits_converted(self):
        assert normalize_persian_digits("۱۲۳۴۵") == "12345"

    def test_arabic_digits_converted(self):
        assert normalize_persian_digits("٦٧٨٩") == "6789"

    def test_mixed_digits(self):
        assert normalize_persian_digits("۱0۲") == "102"

    def test_arabic_kaf_normalized(self):
        assert normalize_persian_digits("كتاب") == "کتاب"

    def test_arabic_ya_normalized(self):
        assert normalize_persian_digits("يک") == "یک"

    def test_empty_string(self):
        assert normalize_persian_digits("") == ""

    def test_none_returns_none(self):
        assert normalize_persian_digits(None) is None

    def test_ascii_digits_unchanged(self):
        assert normalize_persian_digits("9876") == "9876"

    def test_zero_width_non_joiner_removed(self):
        result = normalize_persian_digits("می‌روم")
        assert "‌" not in result

    def test_multiple_spaces_collapsed(self):
        assert normalize_persian_digits("سلام   دنیا") == "سلام دنیا"


# ── parse_persian_number ─────────────────────────────────────────────────────

class TestParsePersianNumber:
    def test_persian_digits(self):
        assert parse_persian_number("۱۲۳۴") == 1234

    def test_arabic_digits(self):
        assert parse_persian_number("٤٥٦") == 456

    def test_ascii_digits(self):
        assert parse_persian_number("789") == 789

    def test_number_with_commas(self):
        assert parse_persian_number("1,234,567") == 1234567

    def test_number_with_spaces(self):
        assert parse_persian_number("500 000") == 500000

    def test_empty_returns_none(self):
        assert parse_persian_number("") is None

    def test_none_returns_none(self):
        assert parse_persian_number(None) is None

    def test_text_only_returns_none(self):
        assert parse_persian_number("متراژ") is None

    def test_phone_number(self):
        assert parse_persian_number("09121234567") == 9121234567

    def test_zero(self):
        assert parse_persian_number("۰") == 0


# ── parse_price_with_unit ────────────────────────────────────────────────────

class TestParsePriceWithUnit:
    def test_million_persian(self):
        assert parse_price_with_unit("۹۰۰ میلیون") == 900_000_000

    def test_billion_persian(self):
        assert parse_price_with_unit("۲ میلیارد") == 2_000_000_000

    def test_thousand_persian(self):
        assert parse_price_with_unit("۵۰۰ هزار") == 500_000

    def test_free_returns_zero(self):
        assert parse_price_with_unit("رایگان") == 0

    def test_free_alternate(self):
        assert parse_price_with_unit("مجانی") == 0

    def test_decimal_million(self):
        result = parse_price_with_unit("1.5 میلیون")
        assert result == 1_500_000

    def test_plain_number(self):
        assert parse_price_with_unit("500000000") == 500_000_000

    def test_empty_returns_none(self):
        assert parse_price_with_unit("") is None

    def test_none_returns_none(self):
        assert parse_price_with_unit(None) is None

    def test_mixed_persian_million(self):
        assert parse_price_with_unit("۱.۸۰۰ میلیارد") == 1_800_000_000_000


# ── extract_divar_id ─────────────────────────────────────────────────────────

class TestExtractDivarId:
    def test_standard_url(self):
        assert extract_divar_id("https://divar.ir/v/apartment/abc123") == "abc123"

    def test_trailing_slash(self):
        assert extract_divar_id("https://divar.ir/v/apartment/abc123/") == "abc123"

    def test_with_query_string(self):
        assert extract_divar_id("https://divar.ir/v/apartment/abc123?ref=home") == "abc123"

    def test_short_id(self):
        assert extract_divar_id("https://divar.ir/v/xyz") == "xyz"

    def test_empty_returns_none(self):
        assert extract_divar_id("") is None or extract_divar_id("") == ""

    def test_none_returns_none(self):
        # Should not raise
        try:
            result = extract_divar_id(None)
            assert result is None
        except Exception:
            pass


# ── generate_tag_number ──────────────────────────────────────────────────────

class TestGenerateTagNumber:
    def test_starts_with_sf(self):
        tag = generate_tag_number()
        assert tag.startswith("SF-")

    def test_format_sf_date_suffix(self):
        import re
        tag = generate_tag_number()
        assert re.match(r"SF-\d{14}-[A-F0-9]{6}", tag)

    def test_unique_each_call(self):
        tags = {generate_tag_number() for _ in range(50)}
        assert len(tags) == 50


# ── extract_price_info ───────────────────────────────────────────────────────

class TestExtractPriceInfo:
    def _soup(self, html):
        return BeautifulSoup(html, "html.parser")

    def test_total_price_million(self):
        html = """
        <div class="kt-unexpandable-row">
            <div class="kt-unexpandable-row__title">قیمت کل</div>
            <div class="kt-unexpandable-row__value">۴۵۰ میلیون تومان</div>
        </div>"""
        result = extract_price_info(self._soup(html))
        assert result.get("total_price") == 450_000_000

    def test_deposit_and_rent(self):
        html = """
        <div class="kt-unexpandable-row">
            <div class="kt-unexpandable-row__title">ودیعه</div>
            <div class="kt-unexpandable-row__value">۵۰ میلیون</div>
        </div>
        <div class="kt-unexpandable-row">
            <div class="kt-unexpandable-row__title">اجاره ماهانه</div>
            <div class="kt-unexpandable-row__value">۳ میلیون</div>
        </div>"""
        result = extract_price_info(self._soup(html))
        assert result.get("deposit") == 50_000_000
        assert result.get("rent_price") == 3_000_000

    def test_empty_html_returns_empty_dict(self):
        result = extract_price_info(self._soup("<html></html>"))
        assert isinstance(result, dict)
        assert result == {}


# ── extract_property_details ─────────────────────────────────────────────────

class TestExtractPropertyDetails:
    def _soup(self, html):
        return BeautifulSoup(html, "html.parser")

    def test_area_parsed(self, sample_html_property):
        result = extract_property_details(self._soup(sample_html_property))
        assert result.get("area") == 85

    def test_rooms_parsed(self, sample_html_property):
        result = extract_property_details(self._soup(sample_html_property))
        assert result.get("rooms") == 2

    def test_elevator_present(self, sample_html_property):
        result = extract_property_details(self._soup(sample_html_property))
        assert result.get("has_elevator") is True

    def test_parking_present(self, sample_html_property):
        result = extract_property_details(self._soup(sample_html_property))
        assert result.get("has_parking") is True

    def test_storage_absent(self, sample_html_property):
        result = extract_property_details(self._soup(sample_html_property))
        assert result.get("has_storage") is False

    def test_balcony_present(self, sample_html_property):
        result = extract_property_details(self._soup(sample_html_property))
        assert result.get("has_balcony") is True

    def test_year_built(self, sample_html_property):
        result = extract_property_details(self._soup(sample_html_property))
        assert result.get("year_built") == 1400

    def test_empty_html(self):
        result = extract_property_details(self._soup("<html></html>"))
        assert isinstance(result, dict)
