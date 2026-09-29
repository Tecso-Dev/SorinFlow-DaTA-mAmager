"""
The «خارج از دسته‌بندی» check outside the residential categories.

The count of listings thrown out as off-category was only ever looked at on
residential runs (where a hyphen-versus-space mismatch had dropped seventeen real
rentals). The other categories judge Divar's breadcrumb leaf with the same matcher
and lists that had never been run against a leaf name written the way Divar writes
it — with a zero-width non-joiner inside «کوتاه‌مدت», with Arabic ي and ك — nor
against the sub-categories those lists forgot: a warehouse («سوله و انبار»), an
orchard («باغ و باغچه»), a short-term suite («سوئیت»).

What Divar actually prints for each leaf could not be read from where this was
written; the leaves below are the panel's own category names plus the natural
names of each family's sub-categories.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_cma.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.config import CATEGORIES  # noqa: E402
from app.scraper.divar_scraper import DivarScraper  # noqa: E402

match = DivarScraper._category_matches
PATTERNS = DivarScraper.CATEGORY_URL_PATTERNS


class TestTheSeparatorsDivarTypes:
    def test_a_zero_width_non_joiner_is_a_space(self):
        assert match("اجاره کوتاه‌مدت", ["کوتاه-مدت"])
        assert match("خانه‌کلنگی", ["خانه-کلنگی"])

    def test_arabic_letters_are_persian_letters(self):
        assert match("اجاره كوتاه مدت", ["کوتاه-مدت"])
        assert match("فروش كلنگي", ["کلنگی"])
        assert match("اجاره ويلا", ["ویلا"])

    def test_the_pattern_is_normalised_too(self):
        assert match("اجاره کوتاه مدت", ["کوتاه‌مدت"])

    def test_the_words_still_have_to_be_adjacent(self):
        assert not match("اجاره ویلا و مدت طولانی و کوتاه", ["کوتاه-مدت"])


@pytest.mark.parametrize("slug", sorted(PATTERNS))
def test_a_category_accepts_its_own_name(slug):
    """The panel's name for the category is Divar's, and must pass its own list."""
    assert match(CATEGORIES[slug]["name"], PATTERNS[slug])


OWN_LEAVES = [
    ("buy-store", "فروش مغازه و غرفه"),
    ("rent-store", "اجاره مغازه و غرفه"),
    ("buy-office", "فروش دفتر کار، اتاق اداری و مطب"),
    ("rent-office", "اجاره دفتر کار، اتاق اداری و مطب"),
    ("buy-commercial-property", "فروش دفتر کار، اتاق اداری و مطب"),
    ("buy-commercial-property", "فروش مغازه و غرفه"),
    ("rent-commercial-property", "اجاره مغازه و غرفه"),
    ("buy-old-house", "فروش زمین و کلنگی"),
    ("buy-villa", "فروش خانه و ویلا"),
    ("rent-villa", "اجاره خانه و ویلا"),
    ("buy-industrial-agricultural-property", "فروش کارگاه، کارخانه و سوله"),
    ("buy-industrial-agricultural-property", "فروش باغ، مزرعه و زمین کشاورزی"),
    ("buy-industrial-agricultural-property", "فروش سوله و انبار"),
    ("buy-industrial-agricultural-property", "فروش باغ و باغچه"),
    ("rent-industrial-agricultural-property", "اجاره سوله و انبار"),
    ("rent-industrial-agricultural-property", "اجاره باغ و مزرعه"),
    ("rent-temporary", "اجاره کوتاه‌مدت"),
    ("rent-temporary", "اجاره کوتاه مدت"),
    ("rent-temporary", "اجاره سوئیت و اقامتگاه"),
    ("rent-temporary", "اقامتگاه بوم‌گردی"),
    ("rent-temporary", "اجاره روزانه"),
]


@pytest.mark.parametrize("slug,leaf", OWN_LEAVES)
def test_a_breadcrumb_of_its_own_family_is_kept(slug, leaf):
    assert match(leaf, PATTERNS[slug]), f"{slug} would count «{leaf}» as off-category"


WRONG_LEAVES = ["استخدام و کاریابی", "سواری", "موبایل و تبلت", "لوازم خانگی", "خدمات پیراهن"]


@pytest.mark.parametrize("slug", sorted(PATTERNS))
@pytest.mark.parametrize("leaf", WRONG_LEAVES)
def test_something_that_is_not_real_estate_is_off_category_everywhere(slug, leaf):
    assert not match(leaf, PATTERNS[slug])


DIFFERENT_TRADES = [
    ("rent-store", "اجاره دفتر کار، اتاق اداری و مطب"),
    ("buy-store", "فروش دفتر کار، اتاق اداری و مطب"),
    ("rent-office", "اجاره مغازه و غرفه"),
    ("buy-office", "فروش مغازه و غرفه"),
    ("rent-store", "اجاره سوله و انبار"),
    ("buy-old-house", "فروش آپارتمان"),
    ("buy-villa", "فروش زمین و کلنگی"),
    ("rent-villa", "اجاره آپارتمان"),
    ("rent-temporary", "اجاره مغازه و غرفه"),
    ("rent-temporary", "فروش آپارتمان"),
    ("buy-industrial-agricultural-property", "فروش مغازه و غرفه"),
    ("rent-industrial-agricultural-property", "اجاره دفتر کار، اتاق اداری و مطب"),
]


@pytest.mark.parametrize("slug,leaf", DIFFERENT_TRADES)
def test_another_trade_s_breadcrumb_is_still_off_category(slug, leaf):
    """Widening a list for the sub-categories it forgot must not make it accept
    the neighbouring family."""
    assert not match(leaf, PATTERNS[slug]), f"{slug} would keep «{leaf}»"
