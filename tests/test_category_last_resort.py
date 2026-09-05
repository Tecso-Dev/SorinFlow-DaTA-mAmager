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
        between = SRC[i:j]
        assert "if not any(p in haystack for p in patterns):" in between

    def test_it_joins_the_haystack_rather_than_replacing_it(self):
        i = SRC.index("await self.page.title()")
        assert 'haystack = f"{haystack} {page_title}"' in SRC[i:i + 400]

    def test_the_listing_is_still_dropped_when_nothing_says_otherwise(self):
        assert "return False  # sentinel: category skip" in SRC

    def test_an_unreadable_title_does_not_take_the_run_with_it(self):
        i = SRC.index("await self.page.title()")
        assert "except Exception" in SRC[i - 200:i + 300]

    def test_the_page_title_is_named_in_the_drop_log(self):
        """Otherwise the log says a listing was dropped and shows only the two
        signals that were empty."""
        i = SRC.index("Skipping off-category listing")
        assert "page title" in SRC[i:i + 400]


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
