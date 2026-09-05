"""
A listing was being dropped for having no name.

The DOM harvester collects tokens three ways, in order:

  1. a regex over __NEXT_DATA__ — token only, never a title
  2. rendered <a href="/v/…"> links — token AND the link's text
  3. [data-token] elements — token AND the element's text

`seen` made the first sighting final, and method 1 runs first. So every
listing Divar had pre-loaded into __NEXT_DATA__ — the whole first page —
was registered with an empty title, and method 2 could never fill it in.

That is not a cosmetic loss. scrape_property_detail decides whether a
listing belongs to the requested category from `decoded_url + source_title`,
and Divar serves most listings as a bare /v/<token> with no descriptive
slug. No title and no slug means no signal, and the listing was dropped as
off-category — for having no name, not for being the wrong kind of ad.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_titles.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRAPER = open(os.path.join(ROOT, "app/scraper/divar_scraper.py"),
               encoding="utf-8-sig").read()

# The harvester block, as it is shipped to the browser. Scoped, because the
# file holds other page scripts with their own `seen` sets.
_i = SCRAPER.index("const addToken = (tok, title) => {")
HARVEST = SCRAPER[SCRAPER.rindex("await self.page.evaluate", 0, _i):
                  SCRAPER.index("return results;", _i)]
ADD_TOKEN = SCRAPER[_i:][:SCRAPER[_i:].index("};") + 2]


class TestALaterSightingCanStillSupplyTheTitle:
    def test_the_index_is_kept_not_just_the_token(self):
        """A Set cannot point back at the row it recorded."""
        assert "new Map()" in HARVEST
        assert "new Set()" not in HARVEST

    def test_a_seen_token_can_be_filled_in(self):
        assert "if (!results[at].title && title) results[at].title = title;" in ADD_TOKEN

    def test_a_title_already_there_is_not_overwritten(self):
        """Method 2's link text beats method 3's container text, which can be
        a whole card of surrounding chrome."""
        assert "!results[at].title &&" in ADD_TOKEN

    def test_a_repeat_sighting_does_not_duplicate_the_listing(self):
        """The dedupe this helper existed for has to survive the change."""
        assert "return;" in ADD_TOKEN
        assert ADD_TOKEN.index("seen.get(tok)") < ADD_TOKEN.index("results.push")

    def test_the_token_is_still_validated_before_anything_is_stored(self):
        assert ADD_TOKEN.index("TOKEN_RE.test(tok)") < ADD_TOKEN.index("seen.get(tok)")

    def test_the_title_is_still_bounded(self):
        assert "substring(0, 120)" in ADD_TOKEN


class TestTheHarvestOrderThatCausedIt:
    """Pinned so the fix cannot be silently undone by a reordering."""

    def test_the_next_data_scan_still_supplies_no_title(self):
        assert "addToken(m[1], '')" in SCRAPER

    def test_it_still_runs_before_the_rendered_links(self):
        assert SCRAPER.index("addToken(m[1], '')") < \
            SCRAPER.index("addToken(tokFromUrl(a.href)")

    def test_the_rendered_links_do_carry_one(self):
        i = SCRAPER.index("addToken(tokFromUrl(a.href)")
        assert "a.innerText" in SCRAPER[i:i + 120]


class TestWhyItMattered:
    def test_the_category_check_reads_the_search_title(self):
        assert "haystack = f\"{decoded_url} {source_title or ''}\"" in SCRAPER

    def test_the_title_reaches_it_from_the_listing(self):
        assert "source_title=listing.get('title')" in SCRAPER

    def test_a_titleless_listing_stores_none_rather_than_empty_string(self):
        """Which is what makes `source_title or ''` collapse to nothing."""
        assert "'title': item.get('title') or None," in SCRAPER
