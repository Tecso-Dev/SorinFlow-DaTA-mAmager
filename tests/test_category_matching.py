"""
The hyphen was doing the rejecting, not the words.

The accept lists were written for URL slugs — «اجاره-آپارتمان», «اجاره-مسکن» —
and then matched against titles, where Divar writes the same phrase with
spaces. So a real ad titled «اجاره ی مسکن مهر کوثر تمام رهن» was tested
against «اجاره-مسکن», found nothing, and was dropped as off-category.

Every one of these titles is a real Urmia apartment rental that a run threw
away, read off the panel's own list of what it skipped.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_cm.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.scraper.divar_scraper import DivarScraper  # noqa: E402

match = DivarScraper._category_matches
RENT_APT = DivarScraper.CATEGORY_URL_PATTERNS["rent-apartment"]


class TestTheHyphenSpaceMismatch:
    def test_a_slug_pattern_matches_a_spaced_title(self):
        assert match("اجاره مسکن مهر کوثر تمام رهن", ["اجاره-مسکن"])

    def test_a_slug_pattern_still_matches_a_slug(self):
        assert match("https://divar.ir/v/اجاره-مسکن-۸۰-متری/abc", ["اجاره-مسکن"])

    def test_underscores_count_as_spaces_too(self):
        assert match("اجاره_مسکن مهر", ["اجاره-مسکن"])

    def test_repeated_spaces_do_not_break_a_match(self):
        assert match("اجاره    آپارتمان   ۸۵ متری", ["اجاره-آپارتمان"])

    def test_the_words_still_have_to_be_adjacent(self):
        """Collapsing separators must not turn the pattern into two loose
        words that match anywhere in the text."""
        assert not match("اجاره ویلا در شمال و مسکن مهر", ["اجاره-مسکن"])


class TestWhatThisChangeDoesAndDoesNotFix:
    """Honest about its reach: it repairs patterns that could never fire, and
    on the run that prompted it that is all it does."""

    def test_a_title_that_names_the_property_type_was_never_the_problem(self):
        assert match("اجاره آپارتمان تکواحده ۱۷۰ متر رودکی", RENT_APT)

    def test_the_ezafe_still_separates_the_two_words(self):
        """«اجاره ی مسکن مهر کوثر» — a real ad this run dropped. The pattern
        «اجاره-مسکن» wants them adjacent and the «ی» is between them, so the
        separator fix alone does not reach it."""
        assert not match("اجاره ی مسکن مهر کوثر تمام رهن", RENT_APT)

    def test_nor_does_it_reach_a_title_with_words_in_between(self):
        """«اجاره رهن کامل مسکن مهرگلشهر» — also real, also still dropped."""
        assert not match("اجاره رهن کامل مسکن مهرگلشهر در ارومیه", RENT_APT)


class TestNothingWasLoosened:
    def test_an_empty_text_matches_nothing(self):
        assert not match("", RENT_APT)

    def test_a_job_ad_is_still_rejected(self):
        assert not match("استخدام حسابدار در ارومیه", RENT_APT)

    def test_a_car_is_still_rejected(self):
        assert not match("پراید ۱۳۹۰ سفید دوگانه", RENT_APT)

    def test_a_shop_is_still_rejected(self):
        assert not match("مغازه ۲۵ متری بر خیابان", RENT_APT)


class TestTheGuardUsesIt:
    def test_both_checks_go_through_the_matcher(self):
        import inspect
        src = inspect.getsource(DivarScraper.scrape_property_detail)
        assert src.count("self._category_matches(haystack, patterns)") == 2
        assert "any(p in haystack for p in patterns)" not in src
