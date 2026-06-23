"""
Pure parsing helpers for Divar property data.
All functions are stateless — no browser, no DB, no async.
"""
import re
import json
import uuid
import hashlib
from datetime import datetime
from typing import Optional, Dict, List, Any
from urllib.parse import urljoin

from loguru import logger

DIVAR_BASE_URL = "https://divar.ir"


# ---------------------------------------------------------------------------
# Digit / text normalisation
# ---------------------------------------------------------------------------

def normalize_persian_digits(text: str) -> str:
    """Convert Persian/Arabic digits to ASCII; clean ZWNJ, NBSP, etc."""
    if not text:
        return text
    text = str(text)
    persian = '۰۱۲۳۴۵۶۷۸۹'
    arabic  = '٠١٢٣٤٥٦٧٨٩'
    english = '0123456789'
    table = str.maketrans(persian + arabic, english * 2)
    text = text.translate(table)
    text = text.replace('ك', 'ک').replace('ي', 'ی')
    text = text.replace('‌', '').replace(' ', ' ')
    text = ' '.join(text.split())
    return text


def parse_persian_number(text: str) -> Optional[int]:
    """Convert a Persian/Arabic digit string to int."""
    if not text:
        return None
    normalized = normalize_persian_digits(str(text))
    cleaned = re.sub(r'[^\d]', '', normalized)
    try:
        return int(cleaned) if cleaned else None
    except ValueError:
        return None


def parse_price_with_unit(text: str) -> Optional[int]:
    """Parse '۹۰۰ میلیون', '۱.۸۰۰ میلیارد', 'رایگان' etc. → int (Tomans)."""
    if not text:
        return None
    normalized = normalize_persian_digits(text)
    if any(w in normalized for w in ["رایگان", "مجانی"]):
        return 0
    multiplier = 1
    if "میلیارد" in normalized:
        multiplier = 10 ** 9
    elif "میلیون" in normalized:
        multiplier = 10 ** 6
    elif "هزار" in normalized:
        multiplier = 10 ** 3
    match = re.search(r"[0-9]+(?:\.[0-9]+)?", normalized)
    if match:
        try:
            number = float(match.group(0))
        except ValueError:
            number = None
    else:
        number = None
    if number is None:
        base_int = parse_persian_number(normalized)
        return (base_int * multiplier) if base_int is not None else None
    return int(round(number * multiplier))


# ---------------------------------------------------------------------------
# ID / tag generation
# ---------------------------------------------------------------------------

def extract_divar_id(url: str) -> Optional[str]:
    """Extract the short listing ID from a Divar URL."""
    try:
        parts = url.rstrip('/').split('/')
        last = parts[-1] if parts else None
        if last and '?' in last:
            last = last.split('?')[0]
        return last
    except Exception:
        return None


def generate_tag_number() -> str:
    """Generate a unique SF-YYYYMMDDHHMMSS-XXXXXX tag."""
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    suffix = uuid.uuid4().hex[:6].upper()
    return f"SF-{timestamp}-{suffix}"


# ---------------------------------------------------------------------------
# Listing-card parsing
# ---------------------------------------------------------------------------

def parse_listing_card(card, base_url: str = DIVAR_BASE_URL) -> Optional[Dict[str, Any]]:
    """Parse a BeautifulSoup listing-card element into a basic dict."""
    try:
        href = card.get('href', '')
        if not href or '/v/' not in href:
            return None
        url = urljoin(base_url, href).split('?')[0]
        divar_id = extract_divar_id(url)
        title_elem = card.select_one('.kt-post-card__title, .post-title, h2, h3')
        title = title_elem.get_text(strip=True) if title_elem else None
        descriptions = card.select('.kt-post-card__description, .post-description, span.description')
        desc_texts = [d.get_text(strip=True) for d in descriptions]
        img_elem = card.select_one('.kt-image-block__image, img')
        thumbnail_url = (img_elem.get('src') or img_elem.get('data-src')) if img_elem else None
        bottom_desc = card.select_one('.kt-post-card__bottom-description, .post-location')
        category_hint = bottom_desc.get_text(strip=True) if bottom_desc else None
        return {
            "url": url,
            "divar_id": divar_id,
            "title": title,
            "descriptions": desc_texts,
            "thumbnail_url": thumbnail_url,
            "category_hint": category_hint,
        }
    except Exception as e:
        logger.warning(f"Failed to parse card: {e}")
        return None


# ---------------------------------------------------------------------------
# Price extraction
# ---------------------------------------------------------------------------

def extract_price_info(soup) -> Dict[str, Any]:
    """Extract total_price / deposit / rent_price from a property page."""
    price_info: Dict[str, Any] = {}

    try:
        def _set_price(key: str, raw: str) -> None:
            if price_info.get(key) is not None:
                return
            normalized_raw = normalize_persian_digits(raw)
            has_unit = any(w in normalized_raw for w in ['میلیارد', 'میلیون', 'هزار', 'رایگان', 'مجانی'])
            if has_unit:
                # Compact format: "500 میلیون" or "6 میلیارد" or "رایگان"
                parsed_unit = parse_price_with_unit(raw)
                if parsed_unit is not None:
                    price_info[key] = parsed_unit
                return
            # Full number format: "500,000,000 تومان"
            # Reject implausibly small values (< 100,000 Tomans) — these are
            # mis-parsed short numbers without unit (e.g. "۱٬۸۰۰" → 1800 Tomans).
            parsed = parse_persian_number(raw)
            if parsed is not None and parsed >= 100_000:
                price_info[key] = parsed

        def _apply_price_pair(title_raw: str, value_text: str) -> None:
            t = title_raw.strip()
            if 'قیمت کل' in t:
                _set_price('total_price', value_text)
            elif 'قیمت هر متر' in t:
                _set_price('price_per_meter', value_text)
            elif 'قیمت' in t and 'متر' not in t and 'total_price' not in price_info:
                _set_price('total_price', value_text)
            elif any(w in t for w in ['ودیعه', 'رهن', 'پیش پرداخت']):
                _set_price('deposit', value_text)
            elif 'اجاره' in t:
                _set_price('rent_price', value_text)

        # Pass 1: table-based (headers in <thead>, values in <tbody>)
        # On Divar, deposit/rent often appear as table columns next to area/rooms
        for table in soup.select('table.kt-group-row'):
            header_cells = table.select('thead th .kt-group-row-item__title')
            data_row = table.select_one('tbody tr')
            if not header_cells or not data_row:
                continue
            value_cells = data_row.select('td.kt-group-row-item') or data_row.select('td')
            if len(header_cells) != len(value_cells):
                continue
            for idx, hcell in enumerate(header_cells):
                _apply_price_pair(
                    hcell.get_text(strip=True),
                    value_cells[idx].get_text(strip=True),
                )

        # Pass 2: row-based (.kt-base-row / .kt-unexpandable-row / individual cells)
        rows = soup.select('.kt-base-row, .kt-unexpandable-row, .kt-group-row-item')

        for row in rows:
            title = row.select_one(
                '.kt-base-row__title, .kt-unexpandable-row__title, .kt-group-row-item__title'
            )
            value = row.select_one(
                '.kt-unexpandable-row__value, .kt-base-row__end, .kt-group-row-item__value'
            )
            if not title or not value:
                cells = row.select('td, div, span')
                if len(cells) >= 2:
                    title = title or cells[0]
                    value = value or cells[1]
            if not title or not value:
                continue
            _apply_price_pair(title.get_text(strip=True), value.get_text(strip=True))

        # Fallback: window.__PRELOADED_STATE__

        # Fallback: window.__PRELOADED_STATE__
        if not (price_info.get('deposit') and price_info.get('rent_price') and price_info.get('total_price')):
            try:
                for script in soup.find_all('script'):
                    txt = script.get_text() or ''
                    if '__PRELOADED_STATE__' not in txt or 'business_data' not in txt:
                        continue
                    dep_m = re.search(r'"deposit(?:_toman)?"\s*:\s*"?(\d+)"?', txt)
                    rent_m = re.search(r'"rent(?:_toman)?"\s*:\s*"?(\d+)"?', txt)
                    if dep_m and not price_info.get('deposit'):
                        try:
                            price_info['deposit'] = int(dep_m.group(1))
                        except Exception:
                            pass
                    if rent_m and not price_info.get('rent_price'):
                        try:
                            price_info['rent_price'] = int(rent_m.group(1))
                        except Exception:
                            pass
                    if not price_info.get('total_price'):
                        total_m = re.search(r'"price"\s*:\s*"?(\d+)"?', txt)
                        if total_m:
                            try:
                                price_info['total_price'] = int(total_m.group(1))
                            except Exception:
                                pass
                    if price_info.get('deposit') or price_info.get('rent_price') or price_info.get('total_price'):
                        break
            except Exception:
                pass

        if 'total_price' in price_info and 'price' not in price_info:
            price_info['price'] = price_info['total_price']
        elif 'rent_price' in price_info and 'price' not in price_info:
            price_info['price'] = price_info['rent_price']

    except Exception as e:
        logger.warning(f"Failed to extract price info: {e}")

    return price_info


# ---------------------------------------------------------------------------
# Room count from text
# ---------------------------------------------------------------------------

def _is_year_value(val: Optional[int]) -> bool:
    """Return True when val looks like a construction year, not a room count."""
    return val is not None and val >= 1300


def extract_rooms_from_text(text: str) -> Optional[int]:
    """Best-effort room count from arbitrary Persian text."""
    if not text:
        return None
    normalized = normalize_persian_digits(text)
    if re.search(r"(بدون\s*خواب|بدون\s*اتاق|فاقد\s*اتاق|سوئیت)", normalized):
        return 0
    m = re.search(r"تعداد\s*اتاق\s*(\d+)", normalized)
    if m:
        try:
            v = int(m.group(1))
            if not _is_year_value(v):
                return v
        except ValueError:
            pass
    # Digit followed by خواب or اتاق‌خواب (e.g. "2 خواب", "3خواب", "2 اتاق خواب")
    m = re.search(r"(\d+)\s*(?:اتاق\s*خواب|خواب)", normalized)
    if not m:
        m = re.search(r"اتاق\s*خواب\s*(\d+)", normalized)
    if m:
        try:
            v = int(m.group(1))
            if not _is_year_value(v):
                return v
        except ValueError:
            pass
    # Short abbreviation "خ" used in agent titles: "3خ", "۲خ"
    m = re.search(r"(\d+)\s*خ\b", normalized)
    if m:
        try:
            v = int(m.group(1))
            if not _is_year_value(v) and v <= 20:
                return v
        except ValueError:
            pass
    word_map = {"یک": 1, "دو": 2, "سه": 3, "چهار": 4, "پنج": 5,
                "شش": 6, "هفت": 7, "هشت": 8, "نه": 9, "ده": 10}
    for word, num in word_map.items():
        if re.search(fr"{word}\s*(?:اتاق\s*خواب|خواب|اتاقه|کله|خ\b)", normalized):
            return num
    return None


# ---------------------------------------------------------------------------
# Property detail extraction
# ---------------------------------------------------------------------------

def extract_property_details(soup, title: str = "") -> Dict[str, Any]:
    """Extract area, rooms, floor, amenities, etc. from a property page."""
    details: Dict[str, Any] = {}

    try:
        # Table-layout: headers in <thead>, values in <tbody>
        try:
            for table in soup.select('table.kt-group-row'):
                header_cells = table.select('thead th .kt-group-row-item__title')
                data_row = table.select_one('tbody tr')
                if not header_cells or not data_row:
                    continue
                value_cells = data_row.select('td.kt-group-row-item') or data_row.select('td')
                if not value_cells:
                    continue
                headers = [h.get_text(strip=True) for h in header_cells]
                values  = [v.get_text(strip=True) for v in value_cells]
                # Only use positional matching when counts match; a mismatch means
                # some cells were skipped by the selector and indices would be wrong.
                if len(headers) != len(values):
                    continue
                for idx, header_text in enumerate(headers):
                    value_text = values[idx]
                    if not header_text or not value_text:
                        continue
                    if 'متراژ' in header_text and not details.get('area'):
                        details['area'] = parse_persian_number(value_text)
                    elif 'اتاق' in header_text and not details.get('rooms'):
                        val = parse_persian_number(value_text)
                        if _is_year_value(val):
                            # Positional mismatch: year ended up in اتاق column
                            if not details.get('year_built'):
                                details['year_built'] = val
                        else:
                            details['rooms'] = val
                    elif 'ساخت' in header_text and not details.get('year_built'):
                        details['year_built'] = parse_persian_number(value_text)
                if details.get('area') is not None and details.get('rooms') is not None:
                    break
        except Exception:
            pass

        info_rows = soup.select('.kt-group-row-item, .kt-base-row, .kt-unexpandable-row')
        for row in info_rows:
            title_elem = row.select_one(
                '.kt-group-row-item__title, .kt-base-row__title, .kt-unexpandable-row__title'
            )
            value = row.select_one(
                '.kt-group-row-item__value, .kt-base-row__end, .kt-unexpandable-row__value'
            )
            if not title_elem or not value:
                title_elem = row.select_one('td.kt-group-row-item__title')
                value      = row.select_one('td.kt-group-row-item__value')
            if not title_elem or not value:
                children = row.select('div, td, span')
                if len(children) >= 2:
                    for i, child in enumerate(children[:3]):
                        child_text = child.get_text(strip=True)
                        if any(w in child_text for w in ['متراژ', 'اتاق', 'طبقه', 'قیمت', 'سند',
                                                          'ودیعه', 'پارکینگ', 'آسانسور', 'سن']):
                            title_elem = child
                            if i + 1 < len(children):
                                value = children[i + 1]
                            break
            if not title_elem or not value:
                continue

            tt = title_elem.get_text(strip=True)
            vt = value.get_text(strip=True)

            if 'متراژ زمین' in tt:
                details['land_area'] = parse_persian_number(vt)
            elif 'متراژ مفید' in tt:
                details['area'] = parse_persian_number(vt)
            elif 'متراژ کل' in tt:
                details['area'] = parse_persian_number(vt)
            elif 'متراژ' in tt and 'زمین' not in tt:
                details['area'] = parse_persian_number(vt)
            elif 'زیربنا' in tt:
                details['built_area'] = parse_persian_number(vt)
            elif 'مساحت' in tt:
                details['area'] = parse_persian_number(vt)
            elif 'متر مربع' in tt:
                details['area'] = parse_persian_number(vt)
            elif 'تعداد اتاق' in tt:
                val = parse_persian_number(vt)
                if not _is_year_value(val):
                    details['rooms'] = val
            elif 'اتاق خواب' in tt:
                val = parse_persian_number(vt)
                if not _is_year_value(val):
                    details['rooms'] = val
            elif 'اتاق' in tt:
                val = parse_persian_number(vt)
                if val is None and 'بدون اتاق' in vt:
                    val = 0
                if not _is_year_value(val):
                    details['rooms'] = val
            elif 'خواب' in tt:
                val = parse_persian_number(vt)
                if not _is_year_value(val):
                    details['rooms'] = val
            elif 'ساخت' in tt or ('سال' in tt and 'year_built' not in details):
                details['year_built'] = parse_persian_number(vt)
            elif 'طبقه' in tt:
                if 'از' in vt:
                    parts = vt.split('از')
                    details['floor'] = parse_persian_number(parts[0])
                    details['total_floors'] = parse_persian_number(parts[1])
                else:
                    details['floor'] = parse_persian_number(vt)
            elif 'آسانسور' in tt:
                _neg = any(n in tt for n in ('بدون ', 'فاقد ')) or any(n in vt for n in ('ندارد', 'خیر', 'ندارند', 'بدون'))
                details['has_elevator'] = not _neg
            elif 'پارکینگ' in tt:
                _neg = any(n in tt for n in ('بدون ', 'فاقد ')) or any(n in vt for n in ('ندارد', 'خیر', 'ندارند', 'بدون'))
                details['has_parking'] = not _neg
            elif 'انباری' in tt:
                _neg = any(n in tt for n in ('بدون ', 'فاقد ')) or any(n in vt for n in ('ندارد', 'خیر', 'ندارند', 'بدون'))
                details['has_storage'] = not _neg
            elif 'بالکن' in tt:
                _neg = any(n in tt for n in ('بدون ', 'فاقد ')) or any(n in vt for n in ('ندارد', 'خیر', 'ندارند', 'بدون'))
                details['has_balcony'] = not _neg
            elif 'تصویر' in tt or 'عکس' in tt:
                details['has_images'] = 'بله' in vt or 'دارد' in vt
            elif 'جهت' in tt:
                details['building_direction'] = vt
            elif 'بر' in tt and 'متر' in vt:
                details['frontage'] = parse_persian_number(vt)
            elif 'وضعیت' in tt:
                details['unit_status'] = vt
            elif 'سند' in tt:
                details['document_type'] = vt
            elif 'نوع کاربری' in tt:
                details['usage_type'] = vt
            elif 'سن بنا' in tt:
                details['building_age'] = vt
            elif 'نوع ملک' in tt:
                details['property_type'] = vt

        # Fallback: infer area from title
        if not details.get('area') and title:
            nt = normalize_persian_digits(title)
            for pattern in [
                r'(\d+)\s*متری', r'متراژ\s*(\d+)', r'(\d+)\s*(?:مترمربع|متر\s*مربع|متر|م)\b',
                r'(\d+)\s*زیربنا', r'مساحت\s*(\d+)', r'متراژ\s*مفید\s*(\d+)',
                r'متراژ\s*کل\s*(\d+)', r'(\d+)\s*متر\s*مربع',
            ]:
                m = re.search(pattern, nt)
                if m:
                    details['area'] = int(m.group(1))
                    break

        # Fallback: infer rooms from title
        if not details.get('rooms') and title:
            r = extract_rooms_from_text(title)
            if r is not None:
                details['rooms'] = r

        # Fallback: JSON-LD
        if not details.get('rooms'):
            try:
                for script in soup.find_all('script', type='application/ld+json'):
                    try:
                        payload = json.loads(script.string or '{}')
                        if not isinstance(payload, dict):
                            continue
                        if 'numberOfRooms' in payload:
                            details['rooms'] = int(payload['numberOfRooms'])
                            break
                        desc_text = payload.get('description')
                        if desc_text and isinstance(desc_text, str):
                            r = extract_rooms_from_text(desc_text)
                            if r is not None:
                                details['rooms'] = r
                                break
                    except Exception:
                        continue
            except Exception:
                pass

        # Fallback: full page text
        if not details.get('rooms'):
            try:
                r = extract_rooms_from_text(soup.get_text(separator=' '))
                if r is not None:
                    details['rooms'] = r
            except Exception:
                pass

        # Fallback: window.__PRELOADED_STATE__ LIST_DATA
        if not details.get('rooms') or not details.get('area'):
            try:
                for script in soup.find_all('script'):
                    try:
                        txt = script.string or ''
                        if '__PRELOADED_STATE__' not in txt:
                            continue
                        list_data = _extract_list_data(txt)
                        if isinstance(list_data, list):
                            for item in list_data:
                                try:
                                    t = item.get('title') or item.get('Title') or ''
                                    v = item.get('value') or item.get('Value') or ''
                                    if t and v:
                                        if 'متراژ' in t and not details.get('area'):
                                            details['area'] = parse_persian_number(v)
                                        if 'اتاق' in t and not details.get('rooms'):
                                            val = parse_persian_number(v)
                                            if not _is_year_value(val):
                                                details['rooms'] = val
                                except Exception:
                                    continue
                            break
                        # Regex fallback
                        m = re.search(
                            r'"title"\s*:\s*"([^"\\]*اتاق[^"\\]*)"\S{0,200}?"value"\s*:\s*"([^"\\]+)"',
                            txt, re.S,
                        )
                        if m and not details.get('rooms'):
                            val = parse_persian_number(m.group(2))
                            if not _is_year_value(val):
                                details['rooms'] = val
                            break
                    except Exception:
                        continue
            except Exception:
                pass

        # Guard: a room count >= 1300 is a year (سال ساخت), not اتاق.
        # This happens when a table cell is skipped by CSS selectors and the
        # positional index shifts, assigning the year value to the rooms slot.
        rooms_val = details.get('rooms')
        if rooms_val is not None and rooms_val >= 1300:
            if not details.get('year_built'):
                details['year_built'] = rooms_val
            del details['rooms']
            # Re-try: infer from title or full text
            if title:
                r = extract_rooms_from_text(title)
                if r is not None:
                    details['rooms'] = r
            if not details.get('rooms'):
                try:
                    r = extract_rooms_from_text(soup.get_text(separator=' '))
                    if r is not None:
                        details['rooms'] = r
                except Exception:
                    pass

    except Exception as e:
        logger.warning(f"Failed to extract property details: {e}")

    return details


def _extract_list_data(txt: str):
    """Parse LIST_DATA array from __PRELOADED_STATE__ script text."""
    def _parse_bracket(source: str, start: int):
        depth = 0
        in_str = False
        escape = False
        for i in range(start, len(source)):
            ch = source[i]
            if ch == '\\' and not escape:
                escape = True
                continue
            if ch == '"' and not escape:
                in_str = not in_str
            escape = False
            if in_str:
                continue
            if ch == '[':
                depth += 1
            elif ch == ']':
                depth -= 1
                if depth == 0:
                    return i
        return None

    m = re.search(r'"LIST_DATA"\s*:\s*\[', txt)
    if not m:
        return None
    start = m.end() - 1
    end = _parse_bracket(txt, start)
    if end is None:
        return None
    array_text = txt[start: end + 1]
    for candidate in (array_text, re.sub(r',\s*(\]|\})', r'\1', array_text)):
        try:
            return json.loads(candidate)
        except Exception:
            pass
    return None


# ---------------------------------------------------------------------------
# Location / features / amenities / images
# ---------------------------------------------------------------------------

def extract_location(soup) -> Dict[str, Any]:
    location: Dict[str, Any] = {}
    try:
        breadcrumb = soup.select('.kt-page-title__subtitle a, .kt-breadcrumb a')
        if breadcrumb:
            locs = [b.get_text(strip=True) for b in breadcrumb]
            if locs:
                location['city_name'] = locs[0]
            if len(locs) >= 2:
                location['district'] = locs[1]
            if len(locs) >= 3:
                location['neighborhood'] = locs[2]
        map_elem = soup.select_one('[data-lat][data-lng]')
        if map_elem:
            location['latitude']  = float(map_elem.get('data-lat', 0))
            location['longitude'] = float(map_elem.get('data-lng', 0))
        addr_elem = soup.select_one('.kt-unexpandable-row__value a[href^="geo:"]')
        if addr_elem:
            location['address'] = addr_elem.get_text(strip=True)
    except Exception as e:
        logger.warning(f"Failed to extract location: {e}")
    return location


def extract_features(soup) -> List[str]:
    features: List[str] = []
    try:
        for elem in soup.select('.kt-group-row-item__value, .kt-feature-row__title'):
            text = elem.get_text(strip=True)
            if text and text not in features:
                features.append(text)
        unwanted = [
            'خودرو', 'موبایل', 'تلویزیون', 'کالای دیجیتال', 'وسایل شخصی',
            'خدمات', 'استخدام', 'حیوانات', 'صندلی', 'نیمکت', 'اسباب', 'گوشی',
            'لامپ', 'پرنده', 'عروس', 'یخچال', 'میز', 'رایانه', 'آموزش',
            'نظافت', 'باغبانی', 'تعمیر', 'حمل', 'فروشگاه', 'مغازه', 'کافه', 'رستوران',
        ]
        for elem in soup.select('.kt-group-row-item .kt-body--stable'):
            text = elem.get_text(strip=True)
            if text and text not in features and not any(kw in text for kw in unwanted):
                features.append(text)
    except Exception as e:
        logger.warning(f"Failed to extract features: {e}")
    return features


def extract_amenities(soup) -> List[str]:
    amenities: List[str] = []
    try:
        for section_title_text in ['امکانات', 'ویژگی', 'مشخصات', 'توضیحات بیشتر']:
            section = soup.find(
                'span', class_='kt-section-title__title',
                string=lambda x, t=section_title_text: x and t in x,
            )
            if section:
                parent = section.find_parent('div', class_='kt-section-title')
                if parent:
                    nxt = parent.find_next_sibling()
                    if nxt:
                        for item in nxt.select(
                            '.kt-group-row-item__value, .kt-feature-row__title, .kt-unexpandable-row__value'
                        ):
                            text = item.get_text(strip=True)
                            if text and text not in amenities and len(text) > 1:
                                amenities.append(text)

        amenity_keywords = [
            'پارکینگ', 'انباری', 'آسانسور', 'بالکن', 'لابی', 'سرایدار',
            'استخر', 'سونا', 'جکوزی', 'سالن ورزش', 'روف گاردن',
            'کولر', 'شوفاژ', 'پکیج', 'رادیاتور', 'اسپلیت', 'چیلر',
            'کف', 'پارکت', 'سرامیک', 'موزاییک', 'سنگ', 'کاشی', 'کمد', 'دیواری', 'شومینه',
            'سرویس', 'آشپزخانه', 'هود', 'کابینت', 'گاز',
            'اسکلت', 'فلزی', 'بتنی', 'نورگیر', 'حیاط', 'مشجر',
            'برق', 'آب', 'تلفن', 'فاضلاب',
            'شمالی', 'جنوبی', 'شرقی', 'غربی',
            'نوساز', 'بازسازی', 'نقاشی', 'کناف',
        ]
        for elem in soup.select(
            '.kt-group-row-item__value, .kt-unexpandable-row__value, .kt-unexpandable-row__title'
        ):
            text = elem.get_text(strip=True)
            if any(kw in text for kw in amenity_keywords) and text not in amenities and len(text) > 1:
                amenities.append(text)

        desc_elem = soup.select_one('.kt-description-row__text')
        if desc_elem:
            for line in desc_elem.get_text().split('\n'):
                line = line.strip()
                if line and any(kw in line for kw in amenity_keywords) and len(line) < 50:
                    if line not in amenities:
                        amenities.append(line)
    except Exception as e:
        logger.warning(f"Failed to extract amenities: {e}")
    return amenities


def extract_images(soup) -> List[str]:
    images: List[str] = []
    try:
        for img in soup.select('.kt-image-block__image, .post-image img, picture img'):
            src = img.get('src') or img.get('data-src')
            if src and 'divarcdn.com' in src and src not in images:
                src = src.replace('thumbnail', 'main').replace('webp_thumbnail', 'webp')
                images.append(src)
    except Exception as e:
        logger.warning(f"Failed to extract images: {e}")
    return images


# ---------------------------------------------------------------------------
# Price enrichment from features list
# ---------------------------------------------------------------------------

def enrich_price_from_features(property_data: Dict[str, Any]) -> None:
    """Infer deposit/rent_price from the features list when labeled rows are missing."""
    features = property_data.get("features") or []
    if not features:
        return
    current_deposit = property_data.get("deposit")
    current_rent    = property_data.get("rent_price")
    if current_deposit is not None and current_rent is not None:
        return

    marker_index = None
    for idx, text in enumerate(features):
        if any(kw in text for kw in ["ودیعه", "رهن"]) and "قابل" in text:
            marker_index = idx
            break
    if marker_index is None:
        for idx, text in enumerate(features):
            if any(kw in text for kw in ["ودیعه", "رهن", "اجاره"]):
                marker_index = idx
                break
    if marker_index is None:
        return

    price_candidates: List[int] = []
    context_slice = features[marker_index: marker_index + 4]
    for text in features[marker_index + 1: marker_index + 5]:
        v = parse_price_with_unit(text)
        if v is not None:
            price_candidates.append(v)
        if len(price_candidates) >= 2:
            break
    if not price_candidates:
        return

    deposit    = current_deposit
    rent_price = current_rent

    def assign_single(val: int) -> None:
        nonlocal deposit, rent_price
        if deposit is None and rent_price is None:
            deposit = val
        elif deposit is None:
            deposit = val
        elif rent_price is None:
            rent_price = val

    if len(price_candidates) == 1:
        assign_single(price_candidates[0])
    else:
        v1, v2 = price_candidates[0], price_candidates[1]
        if 0 in (v1, v2):
            non_zero = v1 if v2 == 0 else v2
            ctx_text = " ".join(str(x) for x in context_slice)
            no_deposit_phrases = ["بدون ودیعه", "بدون رهن", "بدون پیش پرداخت", "ودیعه صفر"]
            if any(p in ctx_text for p in no_deposit_phrases):
                if deposit is None:
                    deposit = 0
                if rent_price is None:
                    rent_price = non_zero
            else:
                if deposit is None:
                    deposit = non_zero
                if rent_price is None:
                    rent_price = 0
        else:
            big, small = max(v1, v2), min(v1, v2)
            if deposit is None:
                deposit = big
            if rent_price is None:
                rent_price = small

    property_data["deposit"]    = deposit
    property_data["rent_price"] = rent_price
