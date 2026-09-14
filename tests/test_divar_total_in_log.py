"""
«۱۱۳ آگهی با این فیلترها در دیوار هست — نوار پیشرفت نباید بر همین باشد؟»

Reasonable question, and the answer is no — but the reason it looked wrong is
worth fixing.

113 is an estimate Divar computed when the button was pressed. 119 is what
the listing page gave up when the run started. They are answers to different
questions and they drift apart: ads are posted and removed in between, and
Divar's result page injects promoted listings the count never included.

As a denominator it would break the bar in both directions. When the pool is
larger — 119 against 113, exactly what happened — the bar fills with six
candidates still to go, which is the bug just fixed wearing a new hat. When
the pool is smaller, a finished run stops at 53% and reads as abandoned.

So the pool stays the denominator and Divar's number goes in the log beside
it, where the two can be compared instead of guessed at.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_dtot.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRAPER = open(os.path.join(ROOT, "app/scraper/divar_scraper.py"),
               encoding="utf-8-sig").read()

BLOCK = SCRAPER[SCRAPER.index("دیوار می‌گوید") - 2500:
                SCRAPER.index("دیوار می‌گوید") + 900]


class TestItAsksDivarAtRunTime:
    def test_it_uses_the_same_service_the_panel_button_uses(self):
        """Two different answers to «how many are there?» would be worse than
        one."""
        assert "from app.services import divar_count as dc" in BLOCK
        assert "dc.fetch_post_count(city, _form)" in BLOCK

    def test_it_builds_the_form_from_the_run_s_own_filters(self):
        for f in ("min_price=min_price", "max_deposit=max_deposit",
                  "min_rent=min_rent", "max_area=max_area",
                  "advertiser_type=advertiser_type", "has_images=has_images"):
            assert f in BLOCK, f

    def test_it_records_both_numbers_together(self):
        assert "divar_count=_divar_total" in BLOCK
        assert "collected=len(all_listings)" in BLOCK

    def test_it_names_the_gap_when_there_is_one(self):
        assert "بیشتر" in BLOCK and "کمتر" in BLOCK


class TestItCannotCostARun:
    def test_the_whole_thing_is_guarded(self):
        i = BLOCK.index("from app.services import divar_count as dc")
        assert "try:" in BLOCK[i - 300:i]
        assert "except Exception" in BLOCK[i:]

    def test_a_failure_is_logged_not_raised(self):
        i = BLOCK.index("could not ask Divar for its total")
        assert "logger.warning" in BLOCK[i - 120:i]

    def test_divar_refusing_to_answer_is_not_an_error_either(self):
        """fetch_post_count reports its own failures in-band."""
        assert "_count_err" in BLOCK
        assert "Divar's own total unavailable" in BLOCK


class TestTheDenominatorDecisionWasReversedDeliberately:
    """This file argued that Divar's count must not be the denominator. The
    operator then asked for exactly that — «درصد پیشرفت بر اساس تعداد دقیق
    آگهی‌های دیوار» — and the concern was answered differently: not by
    refusing the number, but by clamping progress at 100 and filling the bar
    on completion. The pool stays as the fallback when Divar does not answer.
    Pinned here so the reversal reads as a decision, not a regression."""

    def test_divars_count_is_the_denominator_when_it_answered(self):
        i = SCRAPER.index("job.total_items = (job.divar_count")
        assert "else len(all_listings)" in SCRAPER[i:i + 200]

    def test_the_two_failure_modes_are_both_handled(self):
        from app.models.scraping_job import ScrapingJob
        j = ScrapingJob(); j.total_items, j.scraped_items = 113, 119
        assert j.progress == 100.0, "a pool larger than the count must clamp"
        i = SCRAPER.index('job.status = "completed"')
        assert "job.scraped_items = job.total_items" in SCRAPER[i:i + 700], \
            "a pool smaller than the count must be filled on completion"


class TestTheArithmeticOfTheTwoFailureModes:
    """Why the count cannot be the denominator, stated as numbers."""

    def test_a_pool_larger_than_the_count_would_overfill(self):
        collected, divar_says = 119, 113
        assert collected / divar_says > 1.0

    def test_a_pool_smaller_than_the_count_would_never_finish(self):
        collected, divar_says = 60, 113
        assert round(collected / divar_says * 100) == 53
