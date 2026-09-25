"""
One image, four roles (SORINFLOW_ROLE): what each process starts, and how
its boot meets the schema.

all is the single process the app always was. api is HTTP alone — no loop,
no browser; worker is the scrape queue; scheduler is every periodic loop,
each under a supervisor. A pod that does not migrate (DB_MIGRATE_ON_BOOT=
false) only checks that the database is at its Alembic head
(test_migrate.py).
"""
import asyncio
import inspect
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_process_roles.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

import app.main as main  # noqa: E402
from app.services import scrape_queue, supervisor  # noqa: E402

PERIODIC = {"reminders", "backup", "lease_expiry", "audit_retention", "divar_session",
            "proxy_pool", "forwarder_watch", "apk_mirror", "scrape_scheduler", "match_engine",
            "price_watch", "digest", "listing_reader", "embeddings", "photo_tagger",
            "assistant", "gcp_exporter"}


@pytest.fixture
def started(monkeypatch):
    """What _start_background asked for, with nothing actually running."""
    seen = SimpleNamespace(loops=[], worker=[], heartbeat=[])

    async def fake_supervise(name, fn, stall_after, role):
        seen.loops.append(name)
        await asyncio.sleep(3600)

    async def fake_heartbeat(role):
        seen.heartbeat.append(role)
        await asyncio.sleep(3600)
    monkeypatch.setattr(supervisor, "supervise", fake_supervise)
    monkeypatch.setattr(supervisor, "heartbeat_loop", fake_heartbeat)
    monkeypatch.setattr(scrape_queue, "start", lambda role: seen.worker.append(role) or [])
    return seen


async def _start(role):
    tasks, _ = main._start_background(role)
    await asyncio.sleep(0.01)
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


class TestEachRoleStartsWhatItShould:

    @pytest.mark.parametrize("role,loops,worker", [
        ("api", {"gcp_exporter"}, False),
        ("worker", {"gcp_exporter"}, True),
        ("scheduler", PERIODIC, False),
        ("all", PERIODIC, True),
    ])
    async def test_the_role_decides(self, started, role, loops, worker):
        await _start(role)
        assert set(started.loops) == loops and len(started.loops) == len(loops)
        assert started.heartbeat == [role], "every role beats"
        assert started.worker == ([role] if worker else [])

    async def test_a_process_told_not_to_scrape_does_not(self, started, monkeypatch):
        monkeypatch.setattr(main.settings, "scrape_worker_enabled", False)
        await _start("all")
        assert started.worker == [] and set(started.loops) == PERIODIC

    def test_every_loop_beats_under_its_registry_name(self):
        """A mismatch reads as silence: the supervisor would cancel a healthy
        loop every stall_after, forever."""
        for name, fn, stall_after, roles in main._loops():
            assert f'beat("{name}")' in inspect.getsource(fn), name
            assert stall_after > 60 and set(roles) <= set(main._EVERY_ROLE)
        assert 'beat("scrape_consumer")' in inspect.getsource(scrape_queue.consume)
        assert 'beat("scrape_sweep")' in inspect.getsource(scrape_queue.sweep_loop)

    def test_a_stall_is_never_shorter_than_the_loop_s_own_sleep(self, monkeypatch):
        """backup sleeps up to a day, proxies a configurable number of hours:
        a stall_after below that would restart them mid-sleep, forever."""
        monkeypatch.setattr(main.settings, "proxy_refresh_hours", 48)
        monkeypatch.setattr(main.settings, "divar_session_check_minutes", 90)
        loops = {name: stall for name, _fn, stall, _roles in main._loops()}
        assert loops["backup"] > 24 * 3600 and loops["audit_retention"] > 24 * 3600
        assert loops["proxy_pool"] > 48 * 3600 and loops["divar_session"] > 90 * 60


class TestTheSettings:

    def test_the_defaults_are_the_single_process_of_today(self, monkeypatch):
        from app.config import Settings
        for var in ("SORINFLOW_ROLE", "DB_MIGRATE_ON_BOOT", "SCRAPE_WORKER_ENABLED",
                    "SCRAPE_WORKER_CONCURRENCY", "HEARTBEAT_FILE"):
            monkeypatch.delenv(var, raising=False)
        s = Settings(_env_file=None)
        assert (s.sorinflow_role, s.db_migrate_on_boot, s.scrape_worker_enabled,
                s.scrape_worker_concurrency, s.heartbeat_file) == \
            ("all", True, True, 3, "/tmp/sorinflow-heartbeat")

    def test_they_come_from_the_environment(self, monkeypatch):
        from app.config import Settings
        monkeypatch.setenv("SORINFLOW_ROLE", "worker")
        monkeypatch.setenv("DB_MIGRATE_ON_BOOT", "false")
        monkeypatch.setenv("SCRAPE_WORKER_CONCURRENCY", "2")
        s = Settings(_env_file=None)
        assert (s.sorinflow_role, s.db_migrate_on_boot, s.scrape_worker_concurrency) == ("worker", False, 2)

    @pytest.mark.parametrize("var,value", [("SORINFLOW_ROLE", "workers"),
                                           ("SCRAPE_WORKER_CONCURRENCY", "0")])
    def test_a_typo_refuses_to_start_rather_than_run_nothing(self, monkeypatch, var, value):
        from pydantic import ValidationError
        from app.config import Settings
        monkeypatch.setenv(var, value)
        with pytest.raises(ValidationError):
            Settings(_env_file=None)


@pytest.fixture
def boot(monkeypatch):
    """The lifespan with its heavy parts recorded instead of run."""
    calls = []

    async def _refuse():
        calls.append("refuse")

    async def _init_db(strict=False):
        calls.append("init_db")

    async def _assert_schema():
        calls.append("assert_schema_current")

    async def _backfill():
        calls.append("backfill_owner_ids")

    async def _noop():
        pass

    def _start_background(role):
        calls.append(f"start:{role}")
        return [asyncio.create_task(asyncio.sleep(3600))], []
    monkeypatch.setattr(main, "_refuse_default_secrets", _refuse)
    monkeypatch.setattr(main, "init_db", _init_db)
    monkeypatch.setattr(main, "assert_schema_current", _assert_schema)
    monkeypatch.setattr(main, "_backfill_owner_ids_once", _backfill)
    monkeypatch.setattr(main, "_start_background", _start_background)
    monkeypatch.setattr(main, "close_db", _noop)
    monkeypatch.setattr(main, "close_redis", _noop)
    return calls


class TestTheBoot:

    async def test_by_default_it_migrates_as_it_always_has(self, boot, monkeypatch):
        monkeypatch.setattr(main.settings, "db_migrate_on_boot", True)
        monkeypatch.setattr(main.settings, "sorinflow_role", "all")
        async with main.lifespan(main.app):
            pass
        assert boot == ["refuse", "init_db", "backfill_owner_ids", "start:all"]

    async def test_a_pod_that_does_not_migrate_only_checks(self, boot, monkeypatch):
        monkeypatch.setattr(main.settings, "db_migrate_on_boot", False)
        monkeypatch.setattr(main.settings, "sorinflow_role", "api")
        async with main.lifespan(main.app):
            pass
        assert boot == ["refuse", "assert_schema_current", "start:api"], \
            "api never touches ownership — only scheduler and all backfill it"

    async def test_a_non_migrating_scheduler_still_backfills_ownership(self, boot, monkeypatch):
        """The one role this actually matters for: DB_MIGRATE_ON_BOOT=false
        in k8s means init_db()'s own backfill step never runs in this pod —
        only the separate migrate Job does, before the rollout."""
        monkeypatch.setattr(main.settings, "db_migrate_on_boot", False)
        monkeypatch.setattr(main.settings, "sorinflow_role", "scheduler")
        async with main.lifespan(main.app):
            pass
        assert boot == ["refuse", "assert_schema_current", "backfill_owner_ids", "start:scheduler"]

    async def test_a_worker_does_not_backfill_ownership(self, boot, monkeypatch):
        monkeypatch.setattr(main.settings, "db_migrate_on_boot", False)
        monkeypatch.setattr(main.settings, "sorinflow_role", "worker")
        async with main.lifespan(main.app):
            pass
        assert boot == ["refuse", "assert_schema_current", "start:worker"]

    async def test_a_failed_backfill_does_not_raise(self, monkeypatch):
        """Same rule as every other boot step: a lock timeout or a transient
        database blip must not stop a pod that is otherwise ready to serve.
        Exercises the real _backfill_owner_ids_once, not the boot fixture's
        recording stub — _guard is Postgres-only SQL this suite's sqlite
        would fail on for an unrelated reason, so it is a no-op here and the
        actual backfill call is what is made to fail."""
        import app.database as db
        from app.auth import visibility

        async def _noop_guard(conn):
            pass

        def _broken(conn):
            raise RuntimeError("database is down")
        monkeypatch.setattr(db, "_guard", _noop_guard)
        monkeypatch.setattr(visibility, "backfill_owner_ids", _broken)
        await main._backfill_owner_ids_once()   # must not raise

    async def test_a_schema_behind_the_image_stops_the_boot(self, boot, monkeypatch):
        async def _behind():
            raise RuntimeError("database schema is at 0014, this image needs 0015")
        monkeypatch.setattr(main, "assert_schema_current", _behind)
        monkeypatch.setattr(main.settings, "db_migrate_on_boot", False)
        with pytest.raises(RuntimeError, match="0014"):
            async with main.lifespan(main.app):
                pass
        assert not [c for c in boot if c.startswith("start:")], "nothing runs on a schema it cannot use"

    async def test_a_worker_drains_while_everything_else_still_runs(self, boot, monkeypatch):
        heartbeat = asyncio.create_task(asyncio.sleep(3600))
        consumer = asyncio.create_task(asyncio.sleep(3600))
        monkeypatch.setattr(main, "_start_background", lambda role: ([heartbeat], [consumer]))
        seen = []

        async def fake_drain(tasks):
            seen.append((tasks, heartbeat.done()))
        monkeypatch.setattr(scrape_queue, "drain", fake_drain)
        async with main.lifespan(main.app):
            pass
        assert seen == [([consumer], False)], "drained first, with the heartbeat still beating"
        assert heartbeat.cancelled()
        consumer.cancel()


