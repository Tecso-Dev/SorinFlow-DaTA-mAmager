"""
The aggregations behind the CRM insights page.

Every number on that page is a claim about the business, so the tests here
are mostly about the claims that are easy to get subtly wrong: a rate with
nothing in the denominator, a funnel drawn in the wrong order, a "stalled"
count that includes deals which finished months ago, a line chart that skips
empty days and turns a quiet fortnight into a climb.

The rule under test throughout: where the data cannot answer, the answer is
None — never a zero dressed up as a measurement.
"""
import os
import sys
from datetime import date, datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_ins.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.services import crm_insights as ins  # noqa: E402


class Row:
    """A stand-in for an ORM row — the functions read attributes, not models."""
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


# ── funnel ──────────────────────────────────────────────────────────────────

class TestFunnel:
    def test_stages_come_out_in_pipeline_order(self):
        f = ins.funnel({"won": 1, "new": 9, "contacted": 4})
        assert [s["key"] for s in f[:6]] == [
            "new", "contacted", "qualified", "negotiating", "won", "lost"]

    def test_a_missing_stage_is_zero_not_absent(self):
        """A funnel with a stage silently dropped is not a funnel."""
        f = ins.funnel({"new": 3})
        assert {s["key"] for s in f} >= {"contacted", "qualified", "won", "lost"}
        assert next(s for s in f if s["key"] == "won")["count"] == 0

    def test_an_unknown_status_is_surfaced_not_swallowed(self):
        """Lead.status is a free VARCHAR. A value nobody remembers creating is
        exactly what this page is for."""
        f = ins.funnel({"new": 2, "به‌زودی": 5})
        stray = [s for s in f if s.get("unexpected")]
        assert len(stray) == 1
        assert stray[0]["label"] == "به‌زودی"
        assert stray[0]["count"] == 5

    def test_unknown_statuses_are_ordered_by_size(self):
        f = ins.funnel({"a": 1, "b": 7, "c": 3})
        strays = [s["count"] for s in f if s.get("unexpected")]
        assert strays == [7, 3, 1]

    def test_a_zero_count_stray_is_not_listed(self):
        f = ins.funnel({"new": 1, "ghost": 0})
        assert not any(s.get("unexpected") for s in f)

    def test_counts_are_integers(self):
        f = ins.funnel({"new": None, "contacted": "4"})
        assert all(isinstance(s["count"], int) for s in f)


# ── conversion ──────────────────────────────────────────────────────────────

class TestConversionRate:
    def test_normal_case(self):
        assert ins.conversion_rate(25, 100) == 25.0

    def test_empty_crm_gives_none_not_zero(self):
        """«0٪ conversion» reads as a failing business; «—» reads as a question
        that cannot be answered yet, which is the truth on an empty CRM."""
        assert ins.conversion_rate(0, 0) is None

    def test_negative_total_is_refused(self):
        assert ins.conversion_rate(1, -5) is None

    def test_rounded_to_one_decimal(self):
        assert ins.conversion_rate(1, 3) == 33.3


# ── stalled leads ───────────────────────────────────────────────────────────

class TestStalledLeads:
    NOW = datetime(2026, 9, 3, 12, 0, 0)

    def test_a_finished_lead_is_not_stalled(self):
        """A lead that closed a year ago is finished, not neglected, and
        counting it would bury the ones that need a call."""
        rows = [Row(id=1, status="won", updated_at=self.NOW - timedelta(days=400))]
        assert ins.stalled_leads(rows, now=self.NOW) == []

    def test_an_open_lead_past_the_threshold_is_stalled(self):
        rows = [Row(id=1, status="new", updated_at=self.NOW - timedelta(days=9))]
        out = ins.stalled_leads(rows, now=self.NOW)
        assert len(out) == 1 and out[0]["idle_days"] == 9

    def test_a_recently_touched_lead_is_not(self):
        rows = [Row(id=1, status="new", updated_at=self.NOW - timedelta(days=2))]
        assert ins.stalled_leads(rows, now=self.NOW) == []

    def test_a_never_updated_lead_falls_back_to_created_at(self):
        """No update to be old is the most neglected state of all."""
        rows = [Row(id=1, status="contacted", updated_at=None,
                    created_at=self.NOW - timedelta(days=30))]
        out = ins.stalled_leads(rows, now=self.NOW)
        assert out and out[0]["idle_days"] == 30

    def test_oldest_first(self):
        rows = [
            Row(id=1, status="new", updated_at=self.NOW - timedelta(days=8)),
            Row(id=2, status="new", updated_at=self.NOW - timedelta(days=40)),
            Row(id=3, status="new", updated_at=self.NOW - timedelta(days=15)),
        ]
        assert [r["id"] for r in ins.stalled_leads(rows, now=self.NOW)] == [2, 3, 1]

    def test_a_timezone_aware_row_does_not_raise(self):
        """Postgres returns aware datetimes, SQLite naive ones. Comparing across
        the two raises, and that would take down the whole page."""
        rows = [Row(id=1, status="new",
                    updated_at=datetime(2026, 8, 1, tzinfo=timezone.utc))]
        out = ins.stalled_leads(rows, now=self.NOW)
        assert len(out) == 1

    def test_a_row_with_no_dates_at_all_is_skipped(self):
        rows = [Row(id=1, status="new", updated_at=None, created_at=None)]
        assert ins.stalled_leads(rows, now=self.NOW) == []

    def test_the_status_label_is_translated(self):
        rows = [Row(id=1, status="contacted",
                    updated_at=self.NOW - timedelta(days=10))]
        assert ins.stalled_leads(rows, now=self.NOW)[0]["status_label"] == "تماس گرفته‌شده"


# ── daily series ────────────────────────────────────────────────────────────

class TestDailySeries:
    TODAY = date(2026, 9, 3)

    def test_every_day_in_the_window_is_present(self):
        s = ins.daily_series([], days=30, today=self.TODAY)
        assert len(s) == 30

    def test_quiet_days_are_zeros_not_gaps(self):
        """Drawing only the days that have data spaces points a week apart
        evenly, which turns a quiet fortnight into a smooth climb."""
        rows = [datetime(2026, 9, 3, 10), datetime(2026, 8, 25, 10)]
        s = ins.daily_series(rows, days=30, today=self.TODAY)
        assert sum(p["count"] for p in s) == 2
        assert s[-1]["count"] == 1
        assert any(p["count"] == 0 for p in s)

    def test_it_runs_oldest_to_newest(self):
        s = ins.daily_series([], days=5, today=self.TODAY)
        assert s[0]["date"] < s[-1]["date"]
        assert s[-1]["date"] == "2026-09-03"

    def test_rows_outside_the_window_are_ignored(self):
        rows = [datetime(2020, 1, 1, 10)]
        s = ins.daily_series(rows, days=30, today=self.TODAY)
        assert sum(p["count"] for p in s) == 0

    def test_it_accepts_orm_rows_and_bare_datetimes(self):
        rows = [Row(created_at=datetime(2026, 9, 2, 8)), datetime(2026, 9, 2, 9)]
        s = ins.daily_series(rows, days=30, today=self.TODAY)
        assert sum(p["count"] for p in s) == 2

    def test_a_none_timestamp_does_not_raise(self):
        assert ins.daily_series([Row(created_at=None)], days=7, today=self.TODAY)


# ── buckets ─────────────────────────────────────────────────────────────────

class TestTopBuckets:
    def test_the_remainder_is_folded_so_the_total_still_adds_up(self):
        """A chart whose slices do not sum to the headline number is worse
        than no chart."""
        pairs = [(f"c{i}", 10) for i in range(10)]
        out = ins.top_buckets(pairs, limit=6)
        assert sum(b["count"] for b in out) == 100
        assert out[-1]["is_other"] is True
        assert out[-1]["count"] == 40

    def test_no_other_slice_when_everything_fits(self):
        out = ins.top_buckets([("a", 3), ("b", 1)], limit=6)
        assert not any(b.get("is_other") for b in out)

    def test_biggest_first(self):
        out = ins.top_buckets([("a", 1), ("b", 9), ("c", 5)])
        assert [b["label"] for b in out] == ["b", "c", "a"]

    def test_empty_buckets_are_dropped(self):
        out = ins.top_buckets([("a", 0), ("b", 2)])
        assert [b["label"] for b in out] == ["b"]

    def test_a_null_name_becomes_a_dash_not_the_word_none(self):
        out = ins.top_buckets([(None, 4)])
        assert out[0]["label"] == "—"


# ── agents ──────────────────────────────────────────────────────────────────

class TestAgentScoreboard:
    def test_totals_are_summed_across_days(self):
        rows = [Row(agent_name="سارا", new_files=2, showings_count=3,
                    offers_count=1, closed_count=1),
                Row(agent_name="سارا", new_files=1, showings_count=4,
                    offers_count=2, closed_count=0)]
        out = ins.agent_scoreboard(rows)
        assert out[0]["days"] == 2
        assert out[0]["showings"] == 7
        assert out[0]["closed"] == 1

    def test_best_closer_first(self):
        rows = [Row(agent_name="a", closed_count=1, showings_count=0,
                    offers_count=0, new_files=0),
                Row(agent_name="b", closed_count=5, showings_count=0,
                    offers_count=0, new_files=0)]
        assert [a["agent"] for a in ins.agent_scoreboard(rows)] == ["b", "a"]

    def test_showings_per_close_is_none_when_nothing_closed(self):
        """Dividing by zero would crash, or worse be clamped to something
        flattering."""
        rows = [Row(agent_name="a", closed_count=0, showings_count=12,
                    offers_count=0, new_files=0)]
        assert ins.agent_scoreboard(rows)[0]["showings_per_close"] is None

    def test_showings_per_close_is_computed_when_it_can_be(self):
        rows = [Row(agent_name="a", closed_count=2, showings_count=9,
                    offers_count=0, new_files=0)]
        assert ins.agent_scoreboard(rows)[0]["showings_per_close"] == 4.5

    def test_an_unnamed_agent_is_skipped(self):
        rows = [Row(agent_name="  ", closed_count=3, showings_count=0,
                    offers_count=0, new_files=0)]
        assert ins.agent_scoreboard(rows) == []

    def test_null_counters_do_not_raise(self):
        rows = [Row(agent_name="a", closed_count=None, showings_count=None,
                    offers_count=None, new_files=None)]
        assert ins.agent_scoreboard(rows)[0]["closed"] == 0


# ── money ───────────────────────────────────────────────────────────────────

class TestDealTotals:
    def test_open_and_closed_are_counted_apart(self):
        rows = [Row(status="open", amount=100, commission=5, commission_paid=False),
                Row(status="closed", amount=200, commission=10, commission_paid=True)]
        t = ins.deal_totals(rows)
        assert t["open_count"] == 1 and t["closed_count"] == 1
        assert t["total_amount"] == 300
        assert t["closed_amount"] == 200

    def test_commission_splits_by_whether_it_was_paid(self):
        rows = [Row(status="closed", amount=1, commission=7, commission_paid=True),
                Row(status="closed", amount=1, commission=3, commission_paid=False)]
        t = ins.deal_totals(rows)
        assert t["commission_paid"] == 7
        assert t["commission_due"] == 3

    def test_an_open_deals_commission_is_not_counted_as_due(self):
        """It is not owed until the deal closes."""
        rows = [Row(status="open", amount=1, commission=99, commission_paid=False)]
        t = ins.deal_totals(rows)
        assert t["commission_due"] == 0

    def test_empty_is_all_zeros_not_an_error(self):
        t = ins.deal_totals([])
        assert t["deal_count"] == 0 and t["total_amount"] == 0

    def test_status_matching_is_case_insensitive(self):
        rows = [Row(status="CLOSED", amount=5, commission=1, commission_paid=True)]
        assert ins.deal_totals(rows)["closed_count"] == 1


# ── coverage ────────────────────────────────────────────────────────────────

class TestCoverage:
    def test_it_reports_a_percentage(self):
        assert ins.coverage(200, 50) == 25.0

    def test_no_records_gives_none(self):
        assert ins.coverage(0, 0) is None

    def test_full_coverage_is_a_hundred(self):
        assert ins.coverage(10, 10) == 100.0


class TestTheEndpointIsWiredUp:
    def test_the_route_exists(self):
        import inspect
        from app.api.routes import crm
        assert "/insights" in inspect.getsource(crm)

    def test_it_aggregates_in_the_database_not_in_python(self):
        """Pulling every lead back to count them works on a demo and falls
        over on a real CRM."""
        import inspect
        from app.api.routes import crm
        src = inspect.getsource(crm.crm_insights)
        assert "func.count" in src
        assert "group_by" in src

    def test_the_stalled_query_is_bounded(self):
        import inspect
        from app.api.routes import crm
        src = inspect.getsource(crm.crm_insights)
        assert ".limit(" in src
