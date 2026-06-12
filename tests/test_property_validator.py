"""Unit tests for PropertyDataValidator."""
import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.scraper.property_validator import PropertyDataValidator, ValidationResult


@pytest.fixture
def validator():
    return PropertyDataValidator()


VALID_RENT_BASE = {
    "title": "آپارتمان ۸۰ متری در تهران",
    "url": "https://divar.ir/v/abc123",
    "divar_id": "abc123",
    "city_name": "تهران",
}

VALID_SALE_BASE = {
    "title": "آپارتمان فروشی ۱۰۰ متری",
    "url": "https://divar.ir/v/xyz456",
    "divar_id": "xyz456",
    "city_name": "تهران",
}


class TestValidateRentProperty:
    def test_valid_rent_passes(self, validator):
        data = {**VALID_RENT_BASE, "rent_price": 5_000_000, "deposit": 100_000_000, "area": 80, "rooms": 2}
        result = validator.validate_property(data, property_type="rent")
        assert result.is_valid
        assert result.errors == []
        assert result.confidence_score > 0.5

    def test_missing_rent_price_fails(self, validator):
        data = {**VALID_RENT_BASE}
        result = validator.validate_property(data, property_type="rent")
        assert not result.is_valid
        assert any("rent" in e.lower() or "price" in e.lower() for e in result.errors)

    def test_unusual_rent_price_adds_warning(self, validator):
        data = {**VALID_RENT_BASE, "rent_price": 1}  # absurdly low
        result = validator.validate_property(data, property_type="rent")
        assert len(result.warnings) > 0

    def test_deposit_much_lower_than_rent_warns(self, validator):
        data = {**VALID_RENT_BASE, "rent_price": 10_000_000, "deposit": 500_000}
        result = validator.validate_property(data, property_type="rent")
        assert any("deposit" in w.lower() or "rent" in w.lower() for w in result.warnings)


class TestValidateSaleProperty:
    def test_valid_sale_passes(self, validator):
        data = {**VALID_SALE_BASE, "total_price": 5_000_000_000, "area": 100, "rooms": 3}
        result = validator.validate_property(data, property_type="sale")
        assert result.is_valid

    def test_missing_total_price_fails(self, validator):
        data = {**VALID_SALE_BASE}
        result = validator.validate_property(data, property_type="sale")
        assert not result.is_valid


class TestValidateCommonFields:
    def test_short_title_fails(self, validator):
        data = {**VALID_RENT_BASE, "title": "آ", "rent_price": 5_000_000}
        result = validator.validate_property(data, property_type="rent")
        assert not result.is_valid

    def test_missing_title_fails(self, validator):
        data = {**VALID_RENT_BASE, "title": "", "rent_price": 5_000_000}
        result = validator.validate_property(data, property_type="rent")
        assert not result.is_valid

    def test_missing_divar_id_fails(self, validator):
        data = {**VALID_RENT_BASE, "divar_id": None, "rent_price": 5_000_000}
        result = validator.validate_property(data, property_type="rent")
        assert not result.is_valid
        assert any("divar" in e.lower() for e in result.errors)

    def test_invalid_url_fails(self, validator):
        data = {**VALID_RENT_BASE, "url": "https://other.com/listing", "rent_price": 5_000_000}
        result = validator.validate_property(data, property_type="rent")
        assert not result.is_valid

    def test_confidence_bounded_between_zero_and_one(self, validator):
        data = {}
        result = validator.validate_property(data, property_type="rent")
        assert 0.0 <= result.confidence_score <= 1.0

    def test_validation_result_fields(self, validator):
        data = {**VALID_RENT_BASE, "rent_price": 5_000_000}
        result = validator.validate_property(data, property_type="rent")
        assert isinstance(result, ValidationResult)
        assert isinstance(result.errors, list)
        assert isinstance(result.warnings, list)
        assert isinstance(result.is_valid, bool)
        assert isinstance(result.confidence_score, float)

    def test_unusual_area_adds_warning(self, validator):
        data = {**VALID_RENT_BASE, "rent_price": 5_000_000, "area": 50000}  # too large
        result = validator.validate_property(data, property_type="rent")
        assert any("area" in w.lower() for w in result.warnings)
