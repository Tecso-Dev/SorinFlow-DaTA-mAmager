"""
A daily schedule's publish date moves with the day (#33).

`config_for_run` replayed the `posted_date` saved in the schedule exactly as
it was saved. A schedule made with «yesterday» therefore searched that one
old day every morning and found nothing new. The date is now stored
relative — «امروز» (0), «دیروز» (1), «N روز پیش» — and each firing works out
the actual Tehran day when it fires. A schedule saved before that, with a
fixed date, is read as the same distance from the day it was made.

Time is held still by swapping the `datetime` the scheduler module uses, so
these run the real `fire()`, not a copy of its logic. The same behaviour
through the routes is in test_schedule_relative_date_api.py.
"""
import asyncio
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_sched_rel.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.services import scrape_scheduler as sch   # noqa: E402


def _freeze(monkeypatch, moment: datetime):
    """Make `datetime.now()` inside the scheduler module answer `moment`."""
    assert moment.tzinfo is not None

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment.astimezone(tz) if tz else moment.replace(tzinfo=None)

    monkeypatch.setattr(sch, "datetime", Frozen)


def _tehran(y, mo, d, h=8, mi=0) -> datetime:
    """A Tehran wall-clock moment, as the aware datetime it is."""
    return datetime(y, mo, d, h, mi, tzinfo=sch.TEHRAN)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


class FakeDb:
    def __init__(self, owner):
        self.owner = owner
        self.commits = 0

    async def execute(self, q):
        return _Result([self.owner] if self.owner else [])

    async def commit(self):
        self.commits += 1


def _schedule(**kw):
    base = dict(id=7, name="ارومیه صبح", owner_user_id=21, hour=8, minute=0, enabled=True,
                config={"city": "urmia", "category": "rent-apartment", "max_items": 30},
                created_at=_tehran(2026, 9, 28, 9, 0),
                last_job_id=None, last_run_at=None, last_result=None, next_run_at=None)
    base.update(kw)
    return SimpleNamespace(**base)


def _owner():
    # root is exempt from the phone gate, so these need no SMS settings
    return SimpleNamespace(id=21, is_active=True, username="sched_owner", role="root")


def _fire_seeing_jobs(monkeypatch, schedule):
    """Fire once through the real fire(); return the ScrapingJobCreate it launched."""
    seen = []

    async def fake_launch(job_config, db, current_user, resumed_from=None, interactive=True):
        seen.append(job_config)
        return SimpleNamespace(job_id="1a5e5004-f416-4aaf-8ae7-45f8edc68804")
    import app.api.routes.scraper as routes
    monkeypatch.setattr(routes, "_launch_job", fake_launch)
    asyncio.run(sch.fire(schedule, FakeDb(_owner())))
    assert len(seen) == 1
    return seen[0]


class TestAFixedDateNoLongerRepeatsItself:
    """The bug as reported: yesterday's date, saved once, was searched again
    every morning."""

    def test_a_schedule_saved_with_a_fixed_date_fires_tomorrow_on_tomorrows_day(self, monkeypatch):
        # made on the morning of 28 Sep with 27 Sep typed in: «yesterday»
        s = _schedule(config={"city": "urmia", "category": "rent-apartment", "posted_date": "2026-09-27"},
                      created_at=_tehran(2026, 9, 28, 9, 0))
        days = []
        for d in (28, 29, 30):
            _freeze(monkeypatch, _tehran(2026, 9, d, 8, 0))
            days.append(_fire_seeing_jobs(monkeypatch, s).posted_date)
        assert days == ["2026-09-27", "2026-09-28", "2026-09-29"], \
            "the date must move with the day, not stay on the one that was typed"


class TestTheDayIsWorkedOutWhenItFires:

    @pytest.mark.parametrize("ago, expected", [(0, "2026-09-28"), (1, "2026-09-27"),
                                               (2, "2026-09-26"), (30, "2026-08-29")])
    def test_today_yesterday_and_n_days_ago(self, ago, expected):
        cfg = sch.config_for_run({"city": "urmia", "posted_days_ago": ago},
                                 now=_tehran(2026, 9, 28, 8, 0))
        assert cfg["posted_date"] == expected
        assert "posted_days_ago" not in cfg, "the scraper's form has no such field"

    def test_the_same_saved_schedule_names_a_new_day_each_morning(self, monkeypatch):
        s = _schedule(config={"city": "urmia", "category": "rent-apartment", "posted_days_ago": 1})
        days = []
        for month, day in ((9, 28), (9, 30), (10, 1), (10, 2)):     # across a month's end
            _freeze(monkeypatch, _tehran(2026, month, day, 8, 0))
            days.append(_fire_seeing_jobs(monkeypatch, s).posted_date)
        assert days == ["2026-09-27", "2026-09-29", "2026-09-30", "2026-10-01"]

    def test_the_day_is_the_tehran_day_not_the_utc_one(self):
        cfg = {"posted_days_ago": 0}
        # 21:00 UTC on the 27th is 00:30 on the 28th in Tehran
        assert sch.config_for_run(cfg, now=datetime(2026, 9, 27, 21, 0, tzinfo=timezone.utc))["posted_date"] == "2026-09-28"
        # 20:29 UTC is 23:59 Tehran, still the 27th; a minute later it is the 28th
        assert sch.config_for_run(cfg, now=datetime(2026, 9, 27, 20, 29, tzinfo=timezone.utc))["posted_date"] == "2026-09-27"
        assert sch.config_for_run(cfg, now=datetime(2026, 9, 27, 20, 30, tzinfo=timezone.utc))["posted_date"] == "2026-09-28"

    def test_a_missed_firing_uses_the_day_it_actually_runs(self, monkeypatch):
        """The pod was down at 08:00 and the loop fires just after midnight:
        «yesterday» is still the day before the one it really ran on."""
        s = _schedule(config={"city": "urmia", "category": "rent-apartment", "posted_days_ago": 1})
        _freeze(monkeypatch, _tehran(2026, 9, 29, 0, 20))
        assert _fire_seeing_jobs(monkeypatch, s).posted_date == "2026-09-28"

    def test_a_relative_date_is_the_same_job_as_a_manual_run_for_that_day(self):
        """What «شروع» sends for a picked day: the filters, the date, and no
        count when the box is empty (a whole day)."""
        from app.schemas import ScrapingJobCreate
        manual = {"city": "urmia", "category": "rent-apartment", "download_images": True,
                  "min_rent": 5000000, "has_parking": True, "rotate_every": 40,
                  "posted_date": "2026-09-27"}
        saved = sch.stored_config({"city": "urmia", "category": "rent-apartment", "download_images": True,
                                   "min_rent": 5000000, "has_parking": True, "rotate_every": 40,
                                   "posted_days_ago": 1}, now=_tehran(2026, 9, 28, 9, 0))
        ran = sch.config_for_run(saved, now=_tehran(2026, 9, 28, 8, 0))
        assert ScrapingJobCreate(**ran).model_dump() == ScrapingJobCreate(**manual).model_dump()

    def test_a_cap_typed_in_the_form_is_kept_beside_the_date(self):
        cfg = sch.config_for_run({"city": "urmia", "max_items": 40, "posted_days_ago": 1},
                                 now=_tehran(2026, 9, 28))
        assert cfg["max_items"] == 40 and cfg["posted_date"] == "2026-09-27"


class TestTheDailyDefaultStillHolds:

    def test_neither_age_nor_date_is_the_last_day(self):
        assert sch.config_for_run({"city": "urmia"})["max_age_hours"] == 24

    def test_a_date_switches_the_default_off_and_takes_any_age_with_it(self):
        cfg = sch.config_for_run({"posted_days_ago": 1, "max_age_hours": 6}, now=_tehran(2026, 9, 28))
        assert "max_age_hours" not in cfg, "the scraper ignores the age once a day is named"

    def test_an_explicit_age_without_a_date_is_kept(self):
        assert sch.config_for_run({"max_age_hours": 6})["max_age_hours"] == 6


class TestASavedFixedDateIsReadAsRelative:
    """Measured from the schedule's own creation day in Tehran."""

    def _days(self, fixed, created):
        return sch.relative_posted_days({"posted_date": fixed}, created)

    def test_made_on_the_fifth_with_the_fourth_is_yesterday(self):
        assert self._days("2026-09-27", _tehran(2026, 9, 28, 9)) == 1

    def test_the_creation_day_itself_is_today(self):
        assert self._days("2026-09-28", _tehran(2026, 9, 28, 9)) == 0

    def test_three_days_before_is_three_days_ago(self):
        assert self._days("2026-09-25", _tehran(2026, 9, 28, 9)) == 3

    def test_the_creation_day_is_the_tehran_day(self):
        # 00:30 on the 28th in Tehran is still the 27th in UTC
        created = datetime(2026, 9, 27, 21, 0, tzinfo=timezone.utc)
        assert self._days("2026-09-27", created) == 1

    def test_a_date_after_the_creation_day_reads_as_today(self):
        assert self._days("2026-09-30", _tehran(2026, 9, 28, 9)) == 0

    def test_a_datetime_from_sqlite_has_no_zone_and_is_read_as_utc(self):
        assert self._days("2026-09-27", datetime(2026, 9, 28, 5, 0)) == 1

    def test_without_a_creation_day_the_fixed_date_is_left_alone(self):
        cfg = sch.config_for_run({"city": "urmia", "posted_date": "2026-09-18"}, now=_tehran(2026, 10, 5))
        assert cfg["posted_date"] == "2026-09-18" and "max_age_hours" not in cfg

    def test_a_date_that_is_not_a_date_is_left_alone(self):
        cfg = sch.config_for_run({"city": "urmia", "posted_date": "دیروز"},
                                 _tehran(2026, 9, 28), now=_tehran(2026, 9, 29))
        assert cfg["posted_date"] == "دیروز"

    def test_it_fires_on_the_converted_day_through_config_for_run(self):
        cfg = sch.config_for_run({"city": "urmia", "posted_date": "2026-09-27"},
                                 _tehran(2026, 9, 28, 9), now=_tehran(2026, 10, 3, 8))
        assert cfg["posted_date"] == "2026-10-02"

    def test_the_relative_form_wins_when_a_config_has_both(self):
        cfg = sch.config_for_run({"city": "urmia", "posted_date": "2026-09-01", "posted_days_ago": 0},
                                 _tehran(2026, 9, 28), now=_tehran(2026, 9, 29))
        assert cfg["posted_date"] == "2026-09-29"


class TestWhatIsSaved:

    def test_only_the_relative_form_is_stored(self):
        saved = sch.stored_config({"city": "urmia", "category": "rent-apartment", "posted_days_ago": 1},
                                  now=_tehran(2026, 9, 28))
        assert saved["posted_days_ago"] == 1
        assert "posted_date" not in saved and "max_age_hours" not in saved

    def test_a_form_without_a_date_still_stores_the_daily_default(self):
        saved = sch.stored_config({"city": "urmia", "category": "rent-apartment", "max_items": 30})
        assert saved["max_age_hours"] == 24 and "posted_days_ago" not in saved

    def test_an_older_client_that_sends_a_fixed_date_gets_it_stored_relative(self):
        saved = sch.stored_config({"city": "urmia", "category": "rent-apartment", "posted_date": "2026-09-27"},
                                  now=_tehran(2026, 9, 28, 9))
        assert saved["posted_days_ago"] == 1 and "posted_date" not in saved

    @pytest.mark.parametrize("bad", [-1, 31, "1", 1.5, True])
    def test_a_relative_date_out_of_range_or_not_a_whole_number_is_refused(self, bad):
        with pytest.raises(sch.ScheduleDateError):
            sch.stored_config({"city": "urmia", "category": "rent-apartment", "posted_days_ago": bad})

    @pytest.mark.parametrize("fixed", ["2026-09-30", "2026-01-01", "not a date"])
    def test_a_fixed_date_in_the_future_or_far_back_or_garbage_is_refused(self, fixed):
        with pytest.raises(sch.ScheduleDateError):
            sch.stored_config({"city": "urmia", "category": "rent-apartment", "posted_date": fixed},
                              now=_tehran(2026, 9, 28))

    def test_another_bad_field_is_still_refused_by_the_form_schema(self):
        with pytest.raises(ValueError):
            sch.stored_config({"city": "urmia", "category": "rent-apartment", "min_rent": "cheap"})


class TestTheWordsOnTheCard:

    def test_labels(self):
        assert [sch.days_ago_label(n) for n in (0, 1, 2, 10)] == ["امروز", "دیروز", "۲ روز پیش", "۱۰ روز پیش"]

    def test_a_fixed_date_says_it_was_converted_and_to_what(self):
        note = sch.fixed_date_note({"posted_date": "2026-09-27"}, _tehran(2026, 9, 28, 9))
        assert note == "تاریخ ثابت ۱۴۰۵/۰۷/۰۵ به «دیروز» تبدیل شد (نسبت به روز ساخت، ۱۴۰۵/۰۷/۰۶)"

    def test_a_date_after_the_creation_day_says_so(self):
        note = sch.fixed_date_note({"posted_date": "2026-09-30"}, _tehran(2026, 9, 28, 9))
        assert "بعد از روز ساخت" in note and "«امروز»" in note

    def test_a_relative_schedule_has_no_note(self):
        assert sch.fixed_date_note({"posted_days_ago": 1}, _tehran(2026, 9, 28)) is None
        assert sch.fixed_date_note({"max_age_hours": 24}, _tehran(2026, 9, 28)) is None

    def test_describe_gives_the_panel_the_relative_form_only(self):
        d = sch.describe_date({"city": "urmia", "posted_date": "2026-09-25"}, _tehran(2026, 9, 28, 9))
        assert d["days_ago"] == 3 and d["label"] == "۳ روز پیش"
        assert d["config"]["posted_days_ago"] == 3 and "posted_date" not in d["config"]
        assert d["note"]

    def test_describing_never_changes_the_stored_config(self):
        stored = {"city": "urmia", "posted_date": "2026-09-25"}
        sch.describe_date(stored, _tehran(2026, 9, 28, 9))
        assert stored == {"city": "urmia", "posted_date": "2026-09-25"}


def test_the_panel_knows_how_far_back_the_server_lets_a_date_reach():
    """The panel validates «N روز پیش» before it asks; the server is what
    enforces it. Two numbers that must not drift apart."""
    js = (Path(__file__).resolve().parent.parent / "frontend" / "js" / "app.js").read_text(encoding="utf-8")
    m = re.search(r"const SCHEDULE_MAX_DAYS_AGO = (\d+);", js)
    assert m and int(m.group(1)) == sch.MAX_POSTED_DAYS_AGO


class TestWhatTheRowSays:

    def test_the_result_names_the_day_it_went_for(self, monkeypatch):
        s = _schedule(config={"city": "urmia", "category": "rent-apartment", "posted_days_ago": 1})
        _freeze(monkeypatch, _tehran(2026, 9, 28, 8, 0))
        _fire_seeing_jobs(monkeypatch, s)
        assert s.last_result["status"] == "started" and s.last_result["posted_date"] == "2026-09-27"
        assert "۱۴۰۵/۰۷/۰۵" in s.last_result["detail"]

    def test_a_schedule_with_no_date_names_no_day(self, monkeypatch):
        s = _schedule()
        _fire_seeing_jobs(monkeypatch, s)
        assert "posted_date" not in s.last_result and s.last_result["detail"].endswith("شروع شد")
