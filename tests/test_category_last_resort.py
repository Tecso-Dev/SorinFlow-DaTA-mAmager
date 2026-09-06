"""
Before dropping a listing, ask the page what it is.

The category check reads the redirected URL and the title captured from the
search results. Divar serves most listings as a bare /v/<token> with no
descriptive slug, so when the search title is missing too there is no signal
at all — and «no signal» was being treated as «wrong category».

The page itself is open at that moment and its <title> says plainly what the
ad is: «اجاره آپارتمان ۸۵ متری در ارومیه | دیوار». Asking it costs nothing
when the cheap signals already matched, which is the common case.
"""
import inspect
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_lastr.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.scraper.divar_scraper import DivarScraper  # noqa: E402

SRC = inspect.getsource(DivarScraper.scrape_property_detail)
PATTERNS = DivarScraper.CATEGORY_URL_PATTERNS


class TestTheFallbackExists:
    def test_the_page_title_is_consulted(self):
        assert "await self.page.title()" in SRC

    def test_only_after_the_cheap_signals_fail(self):
        """Reading it for every listing would be a round trip per ad, paid on
        the listings that were never in doubt."""
        i = SRC.index("haystack = f\"{decoded_url}")
        j = SRC.index("await self.page.title()")
        assert "if not self._category_matches(haystack, patterns):" in SRC[i:j]

    def test_it_joins_the_haystack_rather_than_replacing_it(self):
        i = SRC.index("await self.page.title()")
        assert 'haystack = f"{haystack} {page_title}"' in SRC[i:i + 400]

    def test_a_page_title_that_matches_settles_it_there_and_then(self):
        """When it does say what the ad is, nothing further is needed."""
        assert SRC.count("self._category_matches(haystack, patterns)") == 2

    def test_a_page_title_that_says_nothing_no_longer_drops_the_listing(self):
        """It is «سایت دیوار» more often than not — React has not replaced it
        at domcontentloaded — and that is not a category."""
        i = SRC.index("await self.page.title()")
        assert "category_unconfirmed = True" in SRC[i:SRC.index("else:", i)]

    def test_an_unreadable_title_does_not_take_the_run_with_it(self):
        i = SRC.index("await self.page.title()")
        assert "except Exception" in SRC[i - 200:i + 300]

    def test_the_page_title_is_named_where_the_doubt_is_raised(self):
        """Otherwise the log records doubt and shows none of what caused it."""
        i = SRC.index("Category unconfirmed for")
        assert "page title" in SRC[i:SRC.index("category_unconfirmed = True", i)]


class TestWhatThePageTitleRecovers:
    """Divar's own <title> for a real ad, against the accept list."""

    def matches(self, category, *signals):
        return any(p in " ".join(signals) for p in PATTERNS[category])

    def test_a_bare_token_url_with_no_search_title(self):
        """The case that was being dropped: nothing but the token."""
        assert not self.matches("rent-apartment", "https://divar.ir/v/wZm3kQ8t", "")

    def test_the_page_title_settles_it(self):
        assert self.matches(
            "rent-apartment", "https://divar.ir/v/wZm3kQ8t", "",
            "اجاره آپارتمان ۸۵ متری در ارومیه | دیوار")

    def test_it_does_not_rescue_a_genuinely_wrong_ad(self):
        assert not self.matches(
            "rent-apartment", "https://divar.ir/v/wZm3kQ8t", "",
            "استخدام حسابدار در ارومیه | دیوار")

    def test_nor_a_plot_of_land(self):
        assert not self.matches(
            "rent-apartment", "https://divar.ir/v/wZm3kQ8t", "",
            "فروش زمین ۲۰۰ متری | دیوار")
