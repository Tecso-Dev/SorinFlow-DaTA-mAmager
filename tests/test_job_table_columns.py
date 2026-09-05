"""
«جای جدید / بروز برعکس میوفته عددها.»

The header is Persian, so it reads right-to-left: «جدید» is the RIGHT column
and «بروز» the left one. The cell under it carried dir="ltr", which lays its
contents out left-to-right — putting the new count on the left, under «بروز»,
and the already-seen count on the right, under «جدید». Every row in the table
reported its two numbers the wrong way round.

The cell cannot simply drop dir="ltr" and stop there: two numbers joined by a
slash form a single left-to-right run under the bidi algorithm, so they would
be reordered together anyway. Each number has to be isolated.
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_JS = open(os.path.join(ROOT, "frontend/js/app.js"), encoding="utf-8").read()
INDEX = open(os.path.join(ROOT, "frontend/index.html"), encoding="utf-8").read()

# The cell, from the new_items span back to the enclosing <td>.
CELL = re.search(
    r"<td[^>]*>\s*(?:<[^>]+>\s*)*\$\{job\.new_items\}.*?</td>",
    APP_JS, re.S).group(0)


class TestTheTwoNumbersFollowTheirHeader:
    def test_the_header_is_still_new_then_updated(self):
        """If the header is ever reordered, this test should be the one that
        notices — the fix below depends on which word is on the right."""
        assert "جدید / بروز" in INDEX

    def test_the_cell_is_not_forced_left_to_right(self):
        assert 'dir="ltr"' not in CELL, (
            "forcing LTR puts the new count under «بروز»"
        )

    def test_new_comes_first_so_it_lands_on_the_right(self):
        assert CELL.index("job.new_items") < CELL.index("job.updated_items")

    def test_each_number_is_bidi_isolated(self):
        """Without isolation the pair is one numeric run and flips as a unit,
        which is the same bug with an extra step."""
        assert CELL.count("<bdi") == 2 and CELL.count("</bdi>") == 2

    def test_the_digits_themselves_are_still_intact(self):
        """<bdi> isolates; it does not reverse what is inside it."""
        assert "${job.new_items}</bdi>" in CELL
        assert "${job.updated_items}</bdi>" in CELL


class TestThePanelWillBeReloaded:
    def test_the_cache_buster_moved(self):
        """A fix nobody's browser fetches is not a fix."""
        m = re.search(r"js/app\.js\?v=([0-9a-z]+)", INDEX)
        assert m, "app.js is no longer cache-busted"
        assert m.group(1) != "20260903c", "still the version that has the bug"
