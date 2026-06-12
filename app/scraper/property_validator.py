"""
Property Data Validator - Validates extracted property data
Smart validation with fuzzy matching and sanity checks
"""
from typing import Dict, Tuple, Optional, List, Any
from dataclasses import dataclass
import re
from loguru import logger


@dataclass
class ValidationResult:
    """Validation result for a property"""
    is_valid: bool
    errors: List[str]
    warnings: List[str]
    confidence_score: float  # 0.0 to 1.0


class PropertyDataValidator:
    """
    Validate property data extracted from Divar
    Hybrid approach with multiple validation rules
    """
    
    # Valid ranges for Iranian properties
    VALID_AREA_RANGE = (10, 10000)  # متر مربع
    VALID_PRICE_RANGE = (1_000_000, 100_000_000_000)  # ریال
    VALID_RENT_RANGE = (100_000, 100_000_000)  # ریال
    VALID_ROOMS_RANGE = (0, 20)
    VALID_FLOORS_RANGE = (-5, 100)
    
    def __init__(self):
        """Initialize validator"""
        logger.info("Property Data Validator initialized")
    
    def validate_property(
        self,
        property_data: Dict[str, Any],
        property_type: str = 'rent'  # rent or sale
    ) -> ValidationResult:
        """
        Validate a complete property data
        
        Args:
            property_data: Dictionary with property details
            property_type: 'rent' or 'sale'
        
        Returns:
            ValidationResult with validation status
        """
        errors = []
        warnings = []
        confidence_score = 1.0
        
        # Check title
        title = property_data.get('title', '')
        if not title or len(title) < 3:
            errors.append("Title missing or too short")
            confidence_score -= 0.2
        
        # Check required fields based on type
        if property_type == 'rent':
            errors_rent, warnings_rent, conf_rent = self._validate_rent_property(property_data)
            errors.extend(errors_rent)
            warnings.extend(warnings_rent)
            confidence_score *= conf_rent
        elif property_type == 'sale':
            errors_sale, warnings_sale, conf_sale = self._validate_sale_property(property_data)
            errors.extend(errors_sale)
            warnings.extend(warnings_sale)
            confidence_score *= conf_sale
        
        # Check common fields
        errors_common, warnings_common, conf_common = self._validate_common_fields(property_data)
        errors.extend(errors_common)
        warnings.extend(warnings_common)
        confidence_score *= conf_common
        
        # Ensure confidence is in valid range
        confidence_score = max(0.0, min(1.0, confidence_score))
        
        # Property is valid if no critical errors
        is_valid = len(errors) == 0
        
        return ValidationResult(
            is_valid=is_valid,
            errors=errors,
            warnings=warnings,
            confidence_score=confidence_score
        )
    
    def _validate_rent_property(self, data: Dict) -> Tuple[List[str], List[str], float]:
        """Validate rent property specific fields"""
        errors = []
        warnings = []
        confidence = 1.0
        
        # Rent price validation
        rent_price = data.get('rent_price')
        if not rent_price:
            errors.append("Rent price missing")
            confidence -= 0.3
        elif not self._is_valid_rent_price(rent_price):
            warnings.append(f"Rent price seems unusual: {rent_price}")
            confidence -= 0.1
        
        # Deposit validation (optional but common)
        deposit = data.get('deposit')
        if deposit and rent_price:
            if deposit < rent_price:
                warnings.append(f"Deposit ({deposit}) is less than rent ({rent_price})")
                confidence -= 0.05
            elif deposit > rent_price * 50:
                warnings.append(f"Deposit ({deposit}) is unusually high compared to rent")
                confidence -= 0.1
        
        # Total price shouldn't exist for rent
        if data.get('total_price'):
            warnings.append("Total price exists for rent property (should be removed)")
            confidence -= 0.1
        
        return errors, warnings, confidence
    
    def _validate_sale_property(self, data: Dict) -> Tuple[List[str], List[str], float]:
        """Validate sale property specific fields"""
        errors = []
        warnings = []
        confidence = 1.0
        
        # Total price validation
        total_price = data.get('total_price') or data.get('price')
        if not total_price:
            errors.append("Total price missing for sale property")
            confidence -= 0.3
        elif not self._is_valid_sale_price(total_price):
            warnings.append(f"Sale price seems unusual: {total_price}")
            confidence -= 0.1
        
        # Price per meter validation
        price_per_meter = data.get('price_per_meter')
        area = data.get('area')
        
        if total_price and area and price_per_meter:
            calculated_price = (price_per_meter or 0) * area
            if calculated_price and total_price:
                diff_ratio = abs(calculated_price - total_price) / total_price
                if diff_ratio > 0.1:  # More than 10% difference
                    warnings.append(f"Price mismatch: total vs (price_per_meter × area)")
                    confidence -= 0.05
        
        # Rent price shouldn't exist for sale
        if data.get('rent_price'):
            warnings.append("Rent price exists for sale property (should be removed)")
            confidence -= 0.1
        
        return errors, warnings, confidence
    
    def _validate_common_fields(self, data: Dict) -> Tuple[List[str], List[str], float]:
        """Validate common property fields"""
        errors = []
        warnings = []
        confidence = 1.0
        
        # Area validation
        area = data.get('area')
        if area:
            if not isinstance(area, (int, float)):
                errors.append(f"Area must be numeric, got {type(area)}")
            elif not self._is_in_range(area, self.VALID_AREA_RANGE):
                warnings.append(f"Area {area}m² is outside typical range")
                confidence -= 0.1
        
        # Rooms validation
        rooms = data.get('rooms')
        if rooms is not None:
            if not isinstance(rooms, (int, float)):
                errors.append(f"Rooms must be numeric")
            elif not self._is_in_range(rooms, self.VALID_ROOMS_RANGE):
                warnings.append(f"Rooms count {rooms} seems unusual")
                confidence -= 0.1
        
        # Floor validation
        floor = data.get('floor')
        if floor is not None:
            if not isinstance(floor, (int, float)):
                errors.append("Floor must be numeric")
            elif floor < self.VALID_FLOORS_RANGE[0] or floor > self.VALID_FLOORS_RANGE[1]:
                warnings.append(f"Floor {floor} outside valid range")
                confidence -= 0.1
        
        # Location validation
        city = data.get('city_name')
        if not city or len(str(city)) < 2:
            warnings.append("City/Location unclear")
            confidence -= 0.1
        
        # URL validation
        url = data.get('url')
        if not url or '/v/' not in url:
            errors.append("Invalid or missing URL")
            confidence -= 0.2
        
        # Divar ID validation
        divar_id = data.get('divar_id')
        if not divar_id or len(str(divar_id)) < 2:
            errors.append("Invalid or missing Divar ID")
            confidence -= 0.2
        
        return errors, warnings, confidence
    
    def _is_valid_rent_price(self, price: Any) -> bool:
        """Check if rent price is in valid range"""
        try:
            p = int(price) if isinstance(price, str) else price
            return self.VALID_RENT_RANGE[0] <= p <= self.VALID_RENT_RANGE[1]
        except:
            return False
    
    def _is_valid_sale_price(self, price: Any) -> bool:
        """Check if sale price is in valid range"""
        try:
            p = int(price) if isinstance(price, str) else price
            return self.VALID_PRICE_RANGE[0] <= p <= self.VALID_PRICE_RANGE[1]
        except:
            return False
    
    def _is_in_range(self, value: Any, valid_range: Tuple[float, float]) -> bool:
        """Check if value is in valid range"""
        try:
            v = float(value)
            return valid_range[0] <= v <= valid_range[1]
        except:
            return False
    
    def get_validation_report(self, result: ValidationResult) -> str:
        """Get human-readable validation report"""
        report = []
        
        if result.is_valid:
            report.append("✅ VALID PROPERTY DATA")
        else:
            report.append("❌ INVALID PROPERTY DATA")
        
        report.append(f"Confidence Score: {result.confidence_score:.1%}")
        
        if result.errors:
            report.append("\n🔴 Errors:")
            for error in result.errors:
                report.append(f"  - {error}")
        
        if result.warnings:
            report.append("\n🟡 Warnings:")
            for warning in result.warnings:
                report.append(f"  - {warning}")
        
        if not result.errors and not result.warnings:
            report.append("No issues found!")
        
        return "\n".join(report)


# Singleton instance
validator = PropertyDataValidator()


def validate_property_data(
    property_data: Dict[str, Any],
    property_type: str = 'rent'
) -> ValidationResult:
    """Helper function to validate property data"""
    return validator.validate_property(property_data, property_type)
