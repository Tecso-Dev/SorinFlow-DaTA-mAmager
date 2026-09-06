"""
An absence of evidence was being treated as evidence of the wrong category.

The panel's own list of what one run skipped, read back:

    گلشهر ۲ تمام رهن                          خارج از دسته‌بندی — سایت دیوار
    اجاره رهن ۱۴۵متر                          خارج از دسته‌بندی — سایت دیوار
    ۲۰۰ متر بر دانشکده ودیعه: ۴۵۰,۰۰۰,۰۰۰    خارج از دسته‌بندی — سایت دیوار
    اجاره مسکونی راه جدا …                    خارج از دسته‌بندی — سایت دیوار
    اجاره ی مسکن مهر کوثر تمام رهن …          خارج از دسته‌بندی

Seventeen of them, and every one a real Urmia apartment rental. Two things
were happening at once: ads written by people often name no property type at
all, and the page title read at domcontentloaded was still Divar's stock
«سایت دیوار» because React had not replaced it yet. Neither is a statement
that the ad is the wrong kind.

Divar's breadcrumb — «املاک › اجاره مسکونی › اجاره آپارتمان» — is a
statement, it is authoritative, and the parse already reads it. So the doubt
is carried there instead of being settled on a missing keyword.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_cdef.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.scraper.divar_scraper import DivarScraper  # noqa: E402

SRC = inspect.getsource(DivarScraper.scrape_property_detail)
match = DivarScraper._category_matches
RENT_APT = DivarScraper.CATEGORY_URL_PATTERNS["rent-apartment"]


def between(start, end):
    i = SRC.index(start)
    return SRC[i:SRC.index(end, i + len(start))]


class TestTheCheapCheckNoLongerDrops:
    def test_it_raises_doubt_instead_of_a_verdict(self):
        assert "category_unconfirmed = True" in SRC

    def test_it_does_not_return_from_the_cheap_check(self):
        block = between("if not self._category_matches(haystack, patterns):",
                        "else:")
        assert "return False" not in block, \
            "a missing keyword is deciding the listing's fate again"

    def test_the_doubt_starts_false(self):
        assert "category_unconfirmed = False" in SRC

    def test_the_log_says_unconfirmed_not_skipped(self):
        assert "Category unconfirmed for" in SRC


class TestTheBreadcrumbDecides:
    def test_it_is_consulted_when_there_is_doubt(self):
        assert "if category_unconfirmed:" in SRC

    def test_it_reads_divars_own_category_name(self):
        block = between("if category_unconfirmed:", "# Infer listing_type")
        assert 'property_data.get("category_name")' in block

    def test_a_contradicting_breadcrumb_drops_the_listing(self):
        block = between("if category_unconfirmed:", "# Infer listing_type")
        assert "return False" in block
        assert "not self._category_matches(leaf, patterns)" in block

    def test_no_breadcrumb_keeps_the_listing(self):
        """The search Divar filtered by category is better evidence than a
        keyword we could not find."""
        block = between("if category_unconfirmed:", "# Infer listing_type")
        assert "if leaf and not" in block, \
            "an empty breadcrumb must not reach the drop"

    def test_the_drop_names_what_divar_called_it(self):
        block = between("if category_unconfirmed:", "# Infer listing_type")
        assert "_last_category_drop" in block
        assert "leaf" in block


class TestNoRevealIsSpentOnAListingAboutToBeDropped:
    def test_the_verdict_comes_before_the_contact_extractor(self):
        """A reveal costs the account an SMS and pushes it toward a challenge."""
        assert SRC.index("if category_unconfirmed:") < SRC.index("ContactExtractor(")

    def test_and_before_the_reveal_is_charged(self):
        assert SRC.index("if category_unconfirmed:") < SRC.index("_charge_reveal()")


class TestTheBreadcrumbsThemselves:
    """Divar's leaf crumb, against the accept list it is judged by."""

    def test_an_apartment_rental_passes(self):
        assert match("اجاره آپارتمان", RENT_APT)

    def test_a_residential_rental_passes(self):
        """«اجاره مسکونی» is what Divar calls the parent, and «مسکونی» ads
        show up under it — «اجاره مسکونی راه جدا» was dropped for this."""
        assert match("اجاره مسکونی", DivarScraper.CATEGORY_URL_PATTERNS["rent-residential"])

    def test_a_job_ad_breadcrumb_is_rejected(self):
        assert not match("استخدام و کاریابی", RENT_APT)

    def test_a_vehicle_breadcrumb_is_rejected(self):
        assert not match("سواری", RENT_APT)


class TestThePatternsAreStillAvailableLater:
    def test_they_are_looked_up_before_the_branch(self):
        """The later check needs them, and they used to be local to the if."""
        assert "patterns = self.CATEGORY_URL_PATTERNS.get(target_category or \"\", ())" in SRC

    def test_an_unknown_category_still_falls_to_the_broad_check(self):
        assert "if patterns:" in SRC
