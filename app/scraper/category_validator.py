"""
Category Validator - Smart Category Detection & Filtering
Handles category validation, tag detection, and data filtering for Divar scraping
"""
import re
from typing import Dict, List, Set, Optional, Tuple
from enum import Enum
from loguru import logger


class RealEstateCategory(Enum):
    """بخش های ملکی Divar"""
    SALE = "فروش"
    RENT = "اجاره"
    SALE_SUMMER = "فروش تابستانی"
    SHARE = "سهم"
    MORTGAGE = "رهن و اجاره"


class PropertyType(Enum):
    """نوع ملک"""
    APARTMENT = "آپارتمان"
    HOUSE = "خانه"
    VILLA = "ویلا"
    LAND = "زمین"
    OFFICE = "دفتر"
    SHOP = "مغازه"
    WAREHOUSE = "انبار"
    OTHER = "سایر"


class CategoryValidator:
    """
    Smart validator برای تشخیص صحیح دسته و filter کردن نتایج غلط
    Hybrid approach: URL-based + Content-based + Fuzzy matching
    """
    
    def __init__(self):
        """Initialize validator with category mappings"""
        # دسته های معتبر برای اجاره مسکن
        self.rent_keywords = {
            'اجاره', 'اجاره‌بها', 'اجارهٔ', 'ماهانه', 'رهن', 'ودیعه',
            'پیش‌پرداخت', 'سکونت', 'مستأجر', 'اجاره‌ای', 'rent'
        }
        
        # دسته های معتبر برای فروش
        self.sale_keywords = {
            'فروش', 'فروختن', 'فروشاندن', 'قیمت', 'خریدو',
            'سند', 'مالک', 'share', 'سهم', 'sell', 'price'
        }
        
        # نوع‌های ملک معتبر
        self.property_types = {
            'آپارتمان': ['apartment', 'apt', 'پارتمان', 'اپارتمان'],
            'خانه': ['house', 'home', 'villa', 'خانه‌ای'],
            'ویلا': ['villa', 'villas', 'ویلایی'],
            'زمین': ['land', 'plot', 'زمین‌های'],
            'دفتر': ['office', 'دفتر‌کار'],
            'مغازه': ['shop', 'store', 'مغازه‌ای'],
        }
        
        # تگ های اشتباهی که باید filter شوند
        self.invalid_tags = {
            'خودرو', 'ماشین', 'موتورسیکلت', 'موبایل', 'گوشی',
            'تلویزیون', 'کالای دیجیتال', 'وسایل شخصی', 'خدمات',
            'کار و تجارت', 'استخدام', 'حیوانات', 'پرنده', 'ماهی',
            'آموزش', 'ورزش', 'سفر', 'بلیت', 'تور', 'ایونت',
            'ملک نیست', 'قابل حذف', 'تبلیغات'
        }
        
        logger.info("Category Validator initialized")
    
    def is_valid_rent_property(self, listing: Dict) -> Tuple[bool, str]:
        """
        Validate اگه یک listing واقعا اجاره مسکن هستش
        Returns: (is_valid, reason)
        """
        # Check 1: نوع ملک
        title = listing.get('title', '').lower()
        descriptions = listing.get('descriptions', [])
        
        # Check برای invalid tags
        all_text = (title + ' ' + ' '.join(descriptions)).lower()
        for invalid_tag in self.invalid_tags:
            if invalid_tag.lower() in all_text:
                return False, f"Invalid tag found: {invalid_tag}"
        
        # Check 2: آیا یکی از rent keywords وجود داره
        has_rent_keyword = any(kw in all_text for kw in self.rent_keywords)
        has_sale_keyword = any(kw in all_text for kw in self.sale_keywords)
        
        # اگه sale keyword بیشتر ظاهر شد، غلطه
        if has_sale_keyword and not has_rent_keyword:
            return False, "Property seems to be for SALE, not RENT"
        
        # Check 3: نوع ملک معتبر
        is_valid_type = self._check_property_type(all_text)
        if not is_valid_type:
            return False, "Invalid or unclear property type"
        
        # Check 4: Price validation
        descriptions_text = ' '.join(descriptions).lower()
        if 'متر' in descriptions_text or 'بدون متراژ' in descriptions_text:
            # خوب هستش - معمولا اجاره منازل متراژ نمی‌ده
            pass
        
        return True, "Valid rent property"
    
    def is_valid_sale_property(self, listing: Dict) -> Tuple[bool, str]:
        """
        Validate اگه یک listing واقعا فروش مسکن هستش
        """
        title = listing.get('title', '').lower()
        descriptions = listing.get('descriptions', [])
        
        # Check برای invalid tags
        all_text = (title + ' ' + ' '.join(descriptions)).lower()
        for invalid_tag in self.invalid_tags:
            if invalid_tag.lower() in all_text:
                return False, f"Invalid tag found: {invalid_tag}"
        
        # Check برای rent-specific keywords
        if any(kw in all_text for kw in self.rent_keywords):
            # اگه یکی از rent keywords بیشتر ظاهر شد
            rent_count = sum(all_text.count(kw) for kw in self.rent_keywords)
            sale_count = sum(all_text.count(kw) for kw in self.sale_keywords)
            
            if rent_count > sale_count:
                return False, "Property seems to be for RENT, not SALE"
        
        # Check نوع ملک
        is_valid_type = self._check_property_type(all_text)
        if not is_valid_type:
            return False, "Invalid or unclear property type"
        
        return True, "Valid sale property"
    
    def _check_property_type(self, text: str) -> bool:
        """Check اگه property type معتبر و درست identify شده"""
        text_lower = text.lower()
        
        # حداقل یکی از نوع‌های معتبر باید وجود داشته باشه
        for prop_type, keywords in self.property_types.items():
            for kw in keywords:
                if kw in text_lower:
                    return True
        
        # اگه keyword نیافت، شاید فقط "ملک" یا "اجاره" یا "فروش" هستش
        # در این صورت معتبره
        return any(word in text_lower for word in ['ملک', 'اجاره', 'فروش', 'سکونت'])
    
    def extract_category_hints(self, listing: Dict) -> Dict[str, any]:
        """
        Extract category hints از listing
        برای بیشتر اطمینان pattern matching
        """
        title = listing.get('title', '').lower()
        descriptions = listing.get('descriptions', [])
        all_text = (title + ' ' + ' '.join(descriptions)).lower()
        
        hints = {
            'is_rent': False,
            'is_sale': False,
            'property_types': [],
            'amenities': [],
            'price_indicators': [],
            'confidence_score': 0.0
        }
        
        # نمرات
        rent_score = 0
        sale_score = 0
        
        # Check rent keywords
        for kw in self.rent_keywords:
            count = all_text.count(kw)
            rent_score += count
        
        # Check sale keywords
        for kw in self.sale_keywords:
            count = all_text.count(kw)
            sale_score += count
        
        # Set rent/sale
        if rent_score > sale_score:
            hints['is_rent'] = True
        elif sale_score > rent_score:
            hints['is_sale'] = True
        
        # Extract property types
        for prop_type, keywords in self.property_types.items():
            for kw in keywords:
                if kw in all_text:
                    hints['property_types'].append(prop_type)
                    break
        
        # Extract amenities
        amenity_keywords = {
            'آسانسور': 'elevator',
            'پارکینگ': 'parking',
            'بالکن': 'balcony',
            'انباری': 'storage',
            'حیاط': 'yard',
            'باغ': 'garden',
            'سونا': 'sauna',
            'تجهیزات': 'furnished'
        }
        
        for fa, en in amenity_keywords.items():
            if fa in all_text:
                hints['amenities'].append(en)
        
        # Calculate confidence
        total_score = rent_score + sale_score
        if total_score > 0:
            hints['confidence_score'] = max(rent_score, sale_score) / total_score
        
        return hints
    
    def filter_listings_by_category(
        self,
        listings: List[Dict],
        category: str,
        strict_mode: bool = False
    ) -> Tuple[List[Dict], List[Dict]]:
        """
        Filter listings by category
        Returns: (valid_listings, invalid_listings)
        
        Args:
            listings: لیست listings برای filter
            category: دسته مورد نظر (rent, sale)
            strict_mode: اگه True باشه، فقط listings که 100% مطمئن باشیم accept می‌کنیم
        """
        valid_listings = []
        invalid_listings = []
        
        for listing in listings:
            if category.lower() in ['rent', 'اجاره', 'اجاره‌بها']:
                is_valid, reason = self.is_valid_rent_property(listing)
            elif category.lower() in ['sale', 'فروش']:
                is_valid, reason = self.is_valid_sale_property(listing)
            else:
                is_valid, reason = self._basic_validation(listing)
            
            if is_valid:
                # In strict mode, check confidence score
                if strict_mode:
                    hints = self.extract_category_hints(listing)
                    if hints['confidence_score'] >= 0.7:  # 70% confidence
                        listing['validation_reason'] = reason
                        valid_listings.append(listing)
                    else:
                        listing['rejection_reason'] = f"{reason} (Low confidence: {hints['confidence_score']:.1%})"
                        invalid_listings.append(listing)
                else:
                    listing['validation_reason'] = reason
                    valid_listings.append(listing)
            else:
                listing['rejection_reason'] = reason
                invalid_listings.append(listing)
        
        logger.info(f"Filtered {len(listings)} listings: {len(valid_listings)} valid, {len(invalid_listings)} invalid")
        
        return valid_listings, invalid_listings
    
    def _basic_validation(self, listing: Dict) -> Tuple[bool, str]:
        """Basic validation برای unknown categories"""
        title = listing.get('title', '').lower()
        
        # At least check برای invalid tags
        all_text = title + ' ' + ' '.join(listing.get('descriptions', []))
        for invalid_tag in self.invalid_tags:
            if invalid_tag.lower() in all_text.lower():
                return False, f"Invalid tag: {invalid_tag}"
        
        # باید یکی از نوع‌های ملک باشه
        return self._check_property_type(all_text), "Unknown category"


# Singleton instance
validator = CategoryValidator()


def validate_listings(
    listings: List[Dict],
    category: str,
    strict_mode: bool = False
) -> Tuple[List[Dict], List[Dict]]:
    """Helper function برای validate listings"""
    return validator.filter_listings_by_category(listings, category, strict_mode)
