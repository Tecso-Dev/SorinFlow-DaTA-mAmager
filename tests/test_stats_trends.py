"""
The trend chart used to run one or two COUNT queries per day in a Python
loop — up to 730 round trips for a year of /property-trends, plus seven more
for the dashboard's own daily_scraping. Both are now one GROUP BY, bucketed
by the Tehran calendar day rather than the server's.

The gotcha that makes this worth a real (not just source-grep) test:
`AT TIME ZONE '+03:30'` on Postgres uses POSIX sign rules and means
UTC-03:30 — the wrong direction — so a row scraped right after Tehran
midnight would be filed under yesterday. Two rows two minutes apart in UTC,
straddling Tehran midnight, must land in two different day buckets.
"""
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_stats_trends.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

import asyncio  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api.routes.stats import TEHRAN, _tehran_day_col, _tehran_today_and_utc_cutoff  # noqa: E402

_RUN = uuid.uuid4().hex[:8]
# Far enough in the past that no other test's "now"-based seed can land here
# and throw off an exact count — this suite shares one database in CI.
_DAY_OFFSET = 45


def _tehran_midnight(day, hour, minute):
    """A tz-aware UTC instant — these tests only ever seed Postgres (see
    _needs_postgres), and asyncpg only interprets a NAIVE datetime correctly
    as UTC when the connection's own session TimeZone happens to be UTC,
    which is not guaranteed (a local Homebrew Postgres can default to the
    machine's zone instead). Aware is unambiguous regardless."""
    local = datetime.combine(day, datetime.min.time(), tzinfo=TEHRAN).replace(hour=hour, minute=minute)
    return local.astimezone(timezone.utc)


def _needs_postgres():
    """A full lifespan boot needs Postgres — _guard()'s SET lock_timeout /
    SET statement_timeout is Postgres-only syntax, unconditionally, the same
    reason test_digest.py's own `client` fixture skips here."""
    import app.database as db
    if not str(db.engine.url).startswith("postgresql"):
        pytest.skip("needs Postgres — see test_digest.py's client fixture")


class TestTehranDayHelper:
    """Pure date math and SQL text — no DB, runs on every dialect."""

    def test_the_cutoff_moves_back_a_day_per_extra_day_requested(self):
        """Timing-independent: whatever 'now' is, ten Tehran days of history
        starts exactly one day earlier than nine."""
        _, cutoff_9 = _tehran_today_and_utc_cutoff(9)
        _, cutoff_10 = _tehran_today_and_utc_cutoff(10)
        assert cutoff_9 - cutoff_10 == timedelta(days=1)

    def test_postgres_uses_the_zone_name_not_a_literal_offset(self):
        """'+03:30' is read under POSIX sign rules and means UTC-03:30 — the
        wrong direction. The zone name is what the gotcha demands."""
        from sqlalchemy import column
        from sqlalchemy.dialects import postgresql

        expr = _tehran_day_col("postgresql", column("scraped_at"))
        compiled = str(expr.compile(dialect=postgresql.dialect(),
                                     compile_kwargs={"literal_binds": True}))
        assert "Asia/Tehran" in compiled
        assert "+03:30" not in compiled and "-03:30" not in compiled

    def test_sqlite_shifts_the_stored_utc_string_instead(self):
        from sqlalchemy import column

        expr = _tehran_day_col("sqlite", column("scraped_at"))
        compiled = str(expr.compile(compile_kwargs={"literal_binds": True}))
        assert "+3 hours" in compiled and "+30 minutes" in compiled


@pytest.fixture(scope="module")
def boss():
    """A super_admin token and a TestClient sharing the real app — needed by
    every endpoint under /api/stats (router-level `stats` permission)."""
    _needs_postgres()
    import app.main as m

    # The app (and its `users` table) only exists once the lifespan has run,
    # so the client comes up first and the account is seeded through it.
    with TestClient(m.app) as c:
        from app.database import async_session_maker
        from app.models.user import User
        from app.auth.jwt import get_password_hash

        async def _go():
            async with async_session_maker() as db:
                db.add(User(username=f"stats_{_RUN}", full_name="Stats Tester", role="super_admin",
                            hashed_password=get_password_hash("pw123456"), is_active=True))
                await db.commit()
        asyncio.run(_go())

        r = c.post("/api/users/token", data={"username": f"stats_{_RUN}", "password": "pw123456"})
        assert r.status_code == 200, r.text
        headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
        yield headers, c


class TestPropertyTrendsEndpoint:

    @pytest.fixture(scope="class")
    @classmethod
    def seeded(cls, boss):
        """One row at 23:59 Tehran, one two minutes later at 00:01 the next
        Tehran day — the same UTC calendar day, but two different Tehran
        ones."""
        from app.database import async_session_maker
        from app.models.property import Property

        today_tehran = datetime.now(timezone.utc).astimezone(TEHRAN).date()
        day_a = today_tehran - timedelta(days=_DAY_OFFSET)
        day_b = day_a + timedelta(days=1)

        async def _go():
            async with async_session_maker() as db:
                db.add(Property(
                    tag_number=f"tzt-{_RUN}-a", divar_id=f"tzt-{_RUN}-a", title="x",
                    url="https://divar.ir/v/tzt-a", is_active=True,
                    scraped_at=_tehran_midnight(day_a, 23, 59), phone_number="09140000001"))
                db.add(Property(
                    tag_number=f"tzt-{_RUN}-b", divar_id=f"tzt-{_RUN}-b", title="x",
                    url="https://divar.ir/v/tzt-b", is_active=True,
                    scraped_at=_tehran_midnight(day_b, 0, 1)))
                await db.commit()
        asyncio.run(_go())
        return day_a, day_b

    def _trends(self, boss):
        headers, client = boss
        r = client.get(f"/api/stats/property-trends?days={_DAY_OFFSET + 5}", headers=headers)
        assert r.status_code == 200, r.text
        return r.json()

    def test_response_shape_is_unchanged(self, boss, seeded):
        body = self._trends(boss)
        assert list(body.keys()) == ["trends"]
        trends = body["trends"]
        assert len(trends) == _DAY_OFFSET + 5
        assert all(set(t.keys()) == {"date", "total", "with_phone"} for t in trends)
        dates = [t["date"] for t in trends]
        assert dates == sorted(dates), "oldest day first, same order as before"

    def test_rows_on_both_sides_of_tehran_midnight_land_in_different_days(self, boss, seeded):
        day_a, day_b = seeded
        by_date = {t["date"]: t for t in self._trends(boss)["trends"]}

        row_a = by_date[day_a.strftime("%Y-%m-%d")]
        row_b = by_date[day_b.strftime("%Y-%m-%d")]
        assert row_a["total"] == 1, "23:59 Tehran must count on day_a, not spill into day_b"
        assert row_b["total"] == 1, "00:01 Tehran must count on day_b, not stay on day_a"
        # with_phone: only day_a's row has a phone_number
        assert row_a["with_phone"] == 1
        assert row_b["with_phone"] == 0

    def test_a_day_with_nothing_scraped_is_zero_not_missing(self, boss, seeded):
        day_a, _ = seeded
        empty_day = (day_a - timedelta(days=1)).strftime("%Y-%m-%d")
        by_date = {t["date"]: t for t in self._trends(boss)["trends"]}
        assert by_date[empty_day] == {"date": empty_day, "total": 0, "with_phone": 0}


class TestDashboardDailyScraping:
    """The same grouped-query technique, applied to /dashboard's own 7-day
    chart (app/api/routes/stats.py's daily_scraping block)."""

    def test_seven_tehran_days_ascending_and_todays_row_sees_a_fresh_scrape(self, boss):
        headers, client = boss
        from app.database import async_session_maker
        from app.models.property import Property

        async def _seed_today():
            async with async_session_maker() as db:
                db.add(Property(
                    tag_number=f"dash-{_RUN}", divar_id=f"dash-{_RUN}", title="x",
                    url="https://divar.ir/v/dash", is_active=True))  # scraped_at: server default, now
                await db.commit()
        asyncio.run(_seed_today())

        # A sync client for this one call: app.database.get_redis() caches a
        # single client for the process, first bound to whichever loop asked
        # first (here, the TestClient's own portal loop) — calling it again
        # from this asyncio.run()'s fresh loop is the same cross-loop hazard
        # a pooled DB connection has. Sync redis has no loop affinity at all.
        import redis as sync_redis
        sync_redis.from_url(os.environ["REDIS_URL"]).delete("stats:dashboard:all")

        r = client.get("/api/stats/dashboard", headers=headers)
        assert r.status_code == 200, r.text
        daily = r.json()["daily_scraping"]
        assert len(daily) == 7
        dates = [d["date"] for d in daily]
        assert dates == sorted(dates)
        today_tehran = datetime.now(timezone.utc).astimezone(TEHRAN).date().strftime("%Y-%m-%d")
        assert daily[-1]["date"] == today_tehran
        assert daily[-1]["count"] >= 1
