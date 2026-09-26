"""
The listing's publish date, as divar.ir writes it since 1405/07/04.

The page used to show «۳ ساعت پیش» where the scraper looked; it now shows
«انتشار آگهی: ۴ مهر ۱۴۰۵، ۰۸:۴۶». Nothing matched, posted_at came back
None on every listing, and a run with the publish-date filter dropped
everything it had already paid a reveal for (job 24: 26 reveals, 0 saved).
"""
import os
import sys
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_pubdate.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.scraper.divar_scraper import DivarScraper  # noqa: E402
from app.scraper.parsers import (_JALALI_MONTHS, TEHRAN_OFFSET,  # noqa: E402
                                 parse_divar_published)
from app.services.dpa_service import to_jalali  # noqa: E402

FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def divar_line(tehran: datetime) -> str:
    """The line Divar prints for a listing published at this Tehran time."""
    jy, jm, jd = (int(p) for p in to_jalali(tehran).split("/"))
    when = f"{jd} {_JALALI_MONTHS[jm - 1]} {jy}، {tehran:%H:%M}".translate(FA)
    return f"انتشار آگهی: {when}\nآخرین به‌روز‌رسانی: {when}"


def tehran_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) + TEHRAN_OFFSET


class TestReadingTheLine:
    def test_the_calendar_anchor_seen_live(self):
        # divar.ir, 2026-09-26: «فروش خانه ... - ۴ مهر ۱۴۰۵»
        assert to_jalali(datetime(2026, 9, 26)) == "1405/07/04"

    def test_it_is_read_as_utc(self):
        t = (tehran_now() - timedelta(days=2)).replace(hour=8, minute=46, second=0, microsecond=0)
        assert parse_divar_published(divar_line(t)) == t - TEHRAN_OFFSET

    def test_the_publish_date_wins_over_the_update_date(self):
        pub = (tehran_now() - timedelta(days=5)).replace(hour=10, minute=0, second=0, microsecond=0)
        upd = pub + timedelta(days=3)
        text = divar_line(pub).split("\n")[0] + "\n" + divar_line(upd).split("\n")[1]
        assert parse_divar_published(text) == pub - TEHRAN_OFFSET

    def test_other_text_is_not_a_date(self):
        assert parse_divar_published("۳ ساعت پیش در ارومیه") is None
        assert parse_divar_published("") is None
        assert parse_divar_published(None) is None


class TestTheExactDayFilter:
    def test_an_ad_posted_after_midnight_tehran_is_that_tehran_day(self):
        # 01:10 Tehran on day D is 21:40 UTC on D-1
        t = (tehran_now() - timedelta(days=1)).replace(hour=1, minute=10, second=0, microsecond=0)
        posted = parse_divar_published(divar_line(t))
        assert posted.date() == (t - timedelta(days=1)).date()        # UTC says yesterday
        assert DivarScraper._date_skip(posted, t.date(), None) is None
        assert "is after" in DivarScraper._date_skip(posted, t.date() - timedelta(days=1), None)

    def test_an_unknown_date_is_dropped_only_under_a_date_filter(self):
        assert DivarScraper._date_skip(None, date(2026, 9, 26), None) == \
            "posted_at unknown; date filter active"
        assert DivarScraper._date_skip(None, None, None) is None
        assert DivarScraper._date_skip(None, None, 24) is None

    def test_the_age_filter_still_works(self):
        old = datetime.now() - timedelta(hours=30)
        assert "older than 24h" in DivarScraper._date_skip(old, None, 24)
        assert DivarScraper._date_skip(datetime.now() - timedelta(hours=2), None, 24) is None


class TestItIsDecidedBeforeTheReveal:
    """pre_contact_skip is what stops «اطلاعات تماس» being clicked."""

    def scraper(self):
        return DivarScraper.__new__(DivarScraper)

    def test_a_wrong_day_costs_no_reveal(self):
        posted = datetime(2026, 9, 20, 9, 0)
        why = self.scraper().pre_contact_skip({"posted_at": posted}, "sell",
                                              {"target_day": date(2026, 9, 26)})
        assert why and "is before" in why

    def test_an_unreadable_date_under_a_date_filter_costs_no_reveal(self):
        why = self.scraper().pre_contact_skip({}, "sell", {"target_day": date(2026, 9, 26)})
        assert why == "posted_at unknown; date filter active"

    def test_the_right_day_is_still_revealed(self):
        posted = datetime(2026, 9, 26, 5, 16)      # 08:46 Tehran
        assert self.scraper().pre_contact_skip({"posted_at": posted}, "sell",
                                               {"target_day": date(2026, 9, 26)}) is None


def test_just_posted_is_now_not_unknown():
    got = DivarScraper._parse_relative_time(None, "دقایقی پیش در ارومیه")
    assert got is not None and abs((datetime.now() - got).total_seconds()) < 5
