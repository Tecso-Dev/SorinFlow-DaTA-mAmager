"""
Divar session state — expiry derivation and what a stored `is_valid` means.

Two separate bugs, both of which showed up on the same panel screenshot: the
header said «کوکی فعال» for a session Divar had been rejecting since the
previous morning, and every account's expiry column read «—».

The first is a belief with no date on it. The second is this:

    expires_at = None
    for cookie in cookies:
        if cookie.get("name") == "token":
            if "expires" in cookie:
                expires_at = datetime.fromtimestamp(cookie["expires"])

Three copies of that existed. Two read only `expires`, so a jar pasted from a
browser extension — which writes `expirationDate`, and is the documented import
path — produced no expiry at all. All three built a naive *local* datetime and
handed it to code that compares against naive *UTC*, so any countdown was out
by the host's offset: three and a half hours here.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_dsession.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

FUTURE = datetime(2027, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
TS = FUTURE.timestamp()


def _jar(**kw):
    """A jar with a token cookie carrying whatever expiry fields are given."""
    return [{"name": "did", "value": "x", "expires": 1},
            {"name": "token", "value": "abc", **kw}]


class TestExpiryDerivation:

    def test_playwright_style_expires_is_read(self):
        from app.services.divar_session import derive_expiry
        assert derive_expiry(_jar(expires=TS)) == FUTURE

    def test_extension_style_expirationDate_is_read(self):
        """The bug that made every row show «—»: the documented import path is
        a jar pasted out of a browser extension, and it does not use `expires`."""
        from app.services.divar_session import derive_expiry
        assert derive_expiry(_jar(expirationDate=TS)) == FUTURE

    def test_the_result_is_utc_not_naive_local(self):
        """_cookie_state compares against naive UTC. A naive local value there
        is silently wrong by the host's offset, which is how a countdown ends
        up three and a half hours out in Tehran."""
        from app.services.divar_session import derive_expiry
        got = derive_expiry(_jar(expires=TS))
        assert got.tzinfo is not None
        assert got.utcoffset() == timedelta(0)

    def test_milliseconds_are_recognised(self):
        from app.services.divar_session import derive_expiry
        assert derive_expiry(_jar(expirationDate=TS * 1000)) == FUTURE

    def test_a_string_timestamp_is_accepted(self):
        """JSON pasted by hand routinely quotes numbers."""
        from app.services.divar_session import derive_expiry
        assert derive_expiry(_jar(expirationDate=str(TS))) == FUTURE

    @pytest.mark.parametrize("fields", [
        {},                      # session cookie: no expiry field at all
        {"expires": -1},         # Playwright's "session cookie"
        {"expirationDate": 0},
        {"expires": None},
        {"expires": "not-a-number"},
    ])
    def test_no_usable_expiry_is_none_not_a_crash_and_not_expired(self, fields):
        """A session cookie has no wall-clock expiry. That is not the same as
        having passed one, and must not be rendered as «منقضی شده»."""
        from app.services.divar_session import derive_expiry
        assert derive_expiry(_jar(**fields)) is None

    def test_an_empty_or_missing_jar_is_none(self):
        from app.services.divar_session import derive_expiry
        assert derive_expiry([]) is None
        assert derive_expiry(None) is None

    def test_only_the_auth_cookie_counts(self):
        """Analytics cookies expire on their own schedule and say nothing about
        whether we can still scrape. Taking the earliest in the jar would
        understate the session's life."""
        from app.services.divar_session import derive_expiry
        jar = [{"name": "_ga", "value": "x", "expirationDate": 1},
               {"name": "token", "value": "abc", "expirationDate": TS}]
        assert derive_expiry(jar) == FUTURE

    def test_there_is_exactly_one_implementation(self):
        """Three copies drifted apart and two of them were wrong. The callers
        must all route through this one."""
        import inspect
        from app.scraper import auth as scraper_auth
        from app.api.routes import auth as route_auth

        for mod in (scraper_auth, route_auth):
            src = inspect.getsource(mod)
            assert "derive_expiry" in src, f"{mod.__name__} does not use the helper"
            assert 'datetime.fromtimestamp(token_cookie' not in src
            assert 'datetime.fromtimestamp(cookie["expires"])' not in src


class TestProbeIsHonestAboutUncertainty:
    """alive=None is load-bearing. Divar being unreachable is not the account
    being dead, and writing the row off for it would empty the rotation pool
    during an outage — when the scraper can least afford it."""

    class _Row:
        def __init__(self, jar=None):
            self.phone_number = "09120000000"
            self.cookies = jar if jar is not None else [{"name": "token", "value": "v"}]
            self.is_valid = True
            self.expires_at = None
            self.last_checked_at = None

    @pytest.mark.asyncio
    async def test_an_empty_jar_is_dead_without_asking_divar(self):
        from app.services import divar_session
        res = await divar_session.probe(self._Row(jar=[]))
        assert res["alive"] is False and res["needs_login"] is True

    @staticmethod
    def _client_returning(*statuses):
        """A client whose successive GETs return the given statuses.

        The probe makes a second, cookie-less request to the same endpoint
        before it will call a session dead, so a refusal needs two entries:
        the answer for the token, then the control.
        """
        seq = list(statuses)

        class _Resp:
            def __init__(self, s): self.status_code = s

        class _Client:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, *a, **kw):
                return _Resp(seq.pop(0) if len(seq) > 1 else seq[0])

        # One class, one shared `seq`. The probe opens a SEPARATE AsyncClient
        # for its control request, so building the class inside the factory
        # would hand the second call a fresh sequence starting at the first
        # status — and the control would answer with the refusal it is meant
        # to be checking.
        return _Client

    @pytest.mark.asyncio
    @pytest.mark.parametrize("statuses,alive", [
        ((200,), True),
        ((403, 200), False),   # refused us, but serves everyone else -> dead
        ((401, 200), False),
        ((500,), None),
        ((429,), None),
        ((302,), None),
    ])
    async def test_status_codes_map_to_the_right_certainty(self, monkeypatch, statuses, alive):
        from app.services import divar_session
        import httpx
        Client = self._client_returning(*statuses)
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: Client())
        res = await divar_session.probe(self._Row())
        assert res["alive"] is alive

    @pytest.mark.asyncio
    @pytest.mark.parametrize("control", [403, 500, 429])
    async def test_a_refusal_is_not_trusted_when_divar_refuses_everyone(
            self, monkeypatch, control):
        """If the same endpoint says no without any token, the refusal is about
        Divar, not about this account. Calling it "expired" is how a working
        session gets written off during somebody else's outage."""
        from app.services import divar_session
        import httpx
        Client = self._client_returning(403, control)
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: Client())
        res = await divar_session.probe(self._Row())
        assert res["alive"] is None
        assert res["needs_login"] is False

    def test_the_probe_endpoint_needs_no_login(self):
        """/v8/user/profile is role-gated: it answers 403 to a valid ordinary
        account as readily as to a junk token, so every session it was asked
        about came back dead. The probe must isolate token validity, nothing
        else."""
        from app.services import divar_session
        assert "user/profile" not in divar_session.PROBE_URL
        assert divar_session.PROBE_URL.endswith("/places/cities")

    @pytest.mark.asyncio
    async def test_a_network_error_is_unknown_not_dead(self, monkeypatch):
        from app.services import divar_session

        class _Client:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, *a, **kw): raise OSError("connection reset")

        import httpx
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _Client())
        res = await divar_session.probe(self._Row())
        assert res["alive"] is None
        assert res["needs_login"] is False


class TestCheckRecordsThatWeAsked:

    class _Db:
        def __init__(self): self.commits = 0
        async def commit(self): self.commits += 1

    def _row(self, valid=True, expires=None, jar=None):
        r = TestProbeIsHonestAboutUncertainty._Row(jar=jar)
        r.is_valid = valid
        r.expires_at = expires
        return r

    async def _check(self, monkeypatch, row, result):
        from app.services import divar_session

        async def fake_probe(_row):
            return dict(result)
        monkeypatch.setattr(divar_session, "probe", fake_probe)
        return await divar_session.check_and_record(self._Db(), row)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("alive", [True, False, None])
    async def test_every_answer_stamps_the_time(self, monkeypatch, alive):
        """Including a healthy one. Recording only failures would leave a
        working session looking exactly as unverified as an untested one."""
        row = self._row()
        await self._check(monkeypatch, row, {"alive": alive, "state": "x", "message": ""})
        assert row.last_checked_at is not None

    @pytest.mark.asyncio
    async def test_an_unknown_answer_does_not_write_the_row_off(self, monkeypatch):
        row = self._row(valid=True)
        await self._check(monkeypatch, row, {"alive": None, "state": "unknown", "message": ""})
        assert row.is_valid is True

    @pytest.mark.asyncio
    async def test_a_refusal_removes_it_from_rotation(self, monkeypatch):
        row = self._row(valid=True)
        await self._check(monkeypatch, row, {"alive": False, "state": "expired", "message": ""})
        assert row.is_valid is False

    @pytest.mark.asyncio
    async def test_a_revived_session_goes_back_into_rotation(self, monkeypatch):
        row = self._row(valid=False)
        await self._check(monkeypatch, row, {"alive": True, "state": "active", "message": ""})
        assert row.is_valid is True

    @pytest.mark.asyncio
    async def test_a_missing_expiry_is_backfilled_on_the_way_past(self, monkeypatch):
        """Sessions stored before derive_expiry existed have expires_at NULL.
        Re-deriving here fixes them without anyone logging in again."""
        row = self._row(expires=None,
                        jar=[{"name": "token", "value": "v", "expirationDate": TS}])
        await self._check(monkeypatch, row, {"alive": True, "state": "active", "message": ""})
        assert row.expires_at == FUTURE

    @pytest.mark.asyncio
    async def test_an_existing_expiry_is_left_alone(self, monkeypatch):
        keep = FUTURE - timedelta(days=5)
        row = self._row(expires=keep,
                        jar=[{"name": "token", "value": "v", "expirationDate": TS}])
        await self._check(monkeypatch, row, {"alive": True, "state": "active", "message": ""})
        assert row.expires_at == keep


class TestTheStateIsPresentedHonestly:

    def test_the_endpoint_reports_verification_age_not_just_write_age(self):
        """age_hours measures when we last wrote the row. Presenting that as
        though it meant "checked" is what let the header contradict the table."""
        import inspect
        from app.api.routes import monitoring

        src = inspect.getsource(monitoring.cookie_health)
        assert "last_checked_at" in src
        assert "checked_age_minutes" in src
        assert '"verified"' in src
        assert "seconds_left" in src

    def test_the_check_endpoint_and_the_loop_share_one_probe(self):
        import inspect
        from app.api.routes import monitoring
        from app.services import divar_session

        src = inspect.getsource(monitoring.cookie_check)
        assert "divar_session.check_and_record" in src
        # and the router no longer carries its own copy
        assert "httpx" not in src
        assert hasattr(divar_session, "verifier_loop")

    def test_the_verifier_can_be_switched_off(self):
        from app.config import get_settings
        assert hasattr(get_settings(), "divar_session_check_minutes")

    def test_the_model_and_migration_carry_the_column(self):
        import inspect
        from app.models.cookie import Cookie
        from app import database

        assert hasattr(Cookie, "last_checked_at")
        src = inspect.getsource(database._migrate_cookie_usage)
        assert "ADD COLUMN IF NOT EXISTS last_checked_at" in src


class TestBulkProxyDeleteIsGuarded:
    """Removing every proxy is one click, so it needs the same confirm-count
    guard the broadcast endpoints use: a stale tab must not be able to wipe a
    list somebody else is still adding to."""

    def test_the_endpoint_refuses_a_stale_count(self):
        import inspect
        from app.api.routes import proxies

        src = inspect.getsource(proxies.delete_all_proxies)
        assert "confirm_count" in src
        assert "409" in src

    def test_it_is_registered_on_the_collection_not_an_id(self):
        from app.api.routes import proxies
        paths = {(r.path, tuple(sorted(r.methods))) for r in proxies.router.routes}
        assert ("", ("DELETE",)) in paths or ("/", ("DELETE",)) in paths
        # and the per-item delete still exists
        assert any(p == "/{proxy_id}" and "DELETE" in m for p, m in paths)

    def test_the_module_can_log(self):
        """This module had no logger; referencing one would have raised
        NameError inside the request, which is exactly how a migration failed
        earlier in this project."""
        from app.api.routes import proxies
        assert hasattr(proxies, "logger")


class TestImportTellsTheTruth:
    """Import used to save the row with is_valid=True and report success without
    asking Divar anything. The panel then said the session was fine, and the
    person found out otherwise on a different screen minutes later — sent back
    to the login form with no explanation."""

    def test_a_jar_without_a_token_cookie_is_refused_with_a_reason(self):
        import inspect
        from app.api.routes import auth

        src = inspect.getsource(auth.import_cookies)
        assert "token_value" in src
        assert "400" in src or "status_code=400" in src
        # and it names what it did receive, so the mistake is diagnosable
        assert "names" in src

    def test_the_import_verifies_before_it_reports(self):
        import inspect
        from app.api.routes import auth

        src = inspect.getsource(auth.import_cookies)
        assert "check_and_record" in src
        assert '"alive"' in src or "alive" in src

    def test_a_failed_probe_does_not_lose_the_import(self):
        """The row is already committed; a probe that raises must not turn a
        successful import into an error."""
        import inspect
        from app.api.routes import auth

        src = inspect.getsource(auth.import_cookies)
        i = src.index("check_and_record")
        assert "except Exception" in src[i:i + 400]


class TestOnlyDivarsOwnCookiesAreSent:
    """The 403 that kept killing freshly-issued sessions.

    `context.cookies()` returns every cookie the browser holds for every domain
    it touched during a login. The whole jar was being joined into one Cookie
    header and sent to api.divar.ir. Two consequences, and the second is fatal:

      * third-party cookies (analytics, captcha providers) went to Divar
      * `token` appears twice — Divar sets it for `.divar.ir` and the page picks
        it up again for `divar.ir` — so the header read `token=old; token=new`

    Divar answers 403 to any request whose token cookie it dislikes, on ANY
    endpoint: `/v8/places/cities` needs no login at all and still returns 403
    with a bad token where it returns 200 with none. So a perfectly good new
    session came back "rejected by Divar", and the verifier then wrote it out
    of rotation automatically.
    """

    FUTURE_TS = FUTURE.timestamp()

    def test_a_duplicate_token_collapses_to_the_fresher_one(self):
        from app.services.divar_session import _header_from
        jar = [{"name": "token", "value": "OLD", "domain": "divar.ir",
                "expirationDate": 1},
               {"name": "token", "value": "NEW", "domain": ".divar.ir",
                "expirationDate": self.FUTURE_TS}]
        header = _header_from(jar)
        assert header.count("token=") == 1
        assert "NEW" in header and "OLD" not in header

    def test_foreign_cookies_are_not_sent_to_divar(self):
        from app.services.divar_session import _header_from
        jar = [{"name": "token", "value": "t", "domain": ".divar.ir"},
               {"name": "_ga", "value": "x", "domain": ".google-analytics.com"},
               {"name": "cf_clearance", "value": "y", "domain": ".cloudflare.com"}]
        header = _header_from(jar)
        assert "_ga" not in header and "cf_clearance" not in header
        assert "token=t" in header

    def test_divar_subdomains_are_kept(self):
        from app.services.divar_session import divar_cookies
        jar = [{"name": "a", "value": "1", "domain": "api.divar.ir"},
               {"name": "b", "value": "2", "domain": ".divar.ir"},
               {"name": "c", "value": "3", "domain": "divar.ir"}]
        assert {c["name"] for c in divar_cookies(jar)} == {"a", "b", "c"}

    def test_a_hand_pasted_jar_without_domains_still_works(self):
        """The documented import path is a paste. Requiring a `domain` field
        would break it for the sake of a key it need not carry."""
        from app.services.divar_session import _header_from
        jar = [{"name": "token", "value": "t"}, {"name": "did", "value": "d"}]
        header = _header_from(jar)
        assert "token=t" in header and "did=d" in header

    def test_valueless_and_nameless_entries_are_dropped(self):
        from app.services.divar_session import _header_from
        jar = [{"name": "token", "value": "t"},
               {"name": "", "value": "x"}, {"name": "y", "value": ""}]
        assert _header_from(jar) == "token=t"


class TestAnUnattendedCheckAsksTwice:
    """A 403 is ambiguous, so the loop must not act on one alone. A false
    negative there costs a working session and a round of SMS codes."""

    class _Db:
        async def commit(self): pass

    def _row(self):
        r = TestProbeIsHonestAboutUncertainty._Row()
        r.is_valid = True
        return r

    @pytest.mark.asyncio
    async def test_a_single_failure_does_not_demote_when_confirming(self, monkeypatch):
        from app.services import divar_session
        calls = {"n": 0}

        async def flaky(_row):
            calls["n"] += 1
            return ({"alive": False, "state": "expired", "message": ""} if calls["n"] == 1
                    else {"alive": True, "state": "active", "message": ""})
        monkeypatch.setattr(divar_session, "probe", flaky)
        monkeypatch.setattr(divar_session.asyncio, "sleep", lambda *_: _noop())

        row = self._row()
        await divar_session.check_and_record(self._Db(), row, confirm=True)
        assert calls["n"] == 2
        assert row.is_valid is True, "a one-off refusal wrote off a live session"

    @pytest.mark.asyncio
    async def test_two_failures_do_demote(self, monkeypatch):
        from app.services import divar_session

        async def dead(_row):
            return {"alive": False, "state": "expired", "message": ""}
        monkeypatch.setattr(divar_session, "probe", dead)
        monkeypatch.setattr(divar_session.asyncio, "sleep", lambda *_: _noop())

        row = self._row()
        await divar_session.check_and_record(self._Db(), row, confirm=True)
        assert row.is_valid is False

    @pytest.mark.asyncio
    async def test_the_manual_button_does_not_second_guess(self, monkeypatch):
        """Somebody is watching and they asked."""
        from app.services import divar_session
        calls = {"n": 0}

        async def dead(_row):
            calls["n"] += 1
            return {"alive": False, "state": "expired", "message": ""}
        monkeypatch.setattr(divar_session, "probe", dead)

        row = self._row()
        await divar_session.check_and_record(self._Db(), row)
        assert calls["n"] == 1 and row.is_valid is False

    def test_the_background_loop_confirms_but_the_route_does_not(self):
        import inspect
        from app.services import divar_session
        from app.api.routes import monitoring

        assert "confirm=True" in inspect.getsource(divar_session.verifier_loop)
        assert "confirm=True" not in inspect.getsource(monitoring.cookie_check)


async def _noop():
    return None


class TestLoginBrowsersDoNotLeak:
    """Each in-flight Divar login holds a live Chromium. It was only ever
    closed on a SUCCESSFUL OTP, so every abandoned login left one running for
    the life of the pod — and a second attempt for the same number overwrote
    the dict entry, dropping the only handle to the previous browser. On a
    single-replica box that is most of the CPU."""

    @pytest.mark.asyncio
    async def test_starting_a_second_login_closes_the_first(self):
        import app.api.routes.auth as r

        closed = []

        class _Auth:
            def __init__(self, tag): self.tag = tag
            async def close_browser(self): closed.append(self.tag)

        r.auth_instances["0912"] = _Auth("first")
        r._auth_started["0912"] = 0.0
        await r._discard_auth_instance("0912", "superseded")

        assert closed == ["first"]
        assert "0912" not in r.auth_instances
        assert "0912" not in r._auth_started

    @pytest.mark.asyncio
    async def test_an_abandoned_login_is_swept(self):
        import time
        import app.api.routes.auth as r

        closed = []

        class _Auth:
            async def close_browser(self): closed.append(True)

        r.auth_instances["0913"] = _Auth()
        r._auth_started["0913"] = time.monotonic() - (r.AUTH_INSTANCE_TTL + 5)
        await r._sweep_auth_instances()
        assert closed == [True] and "0913" not in r.auth_instances

    @pytest.mark.asyncio
    async def test_a_login_still_in_progress_is_left_alone(self):
        import time
        import app.api.routes.auth as r

        class _Auth:
            async def close_browser(self): raise AssertionError("closed too early")

        r.auth_instances["0914"] = _Auth()
        r._auth_started["0914"] = time.monotonic()
        await r._sweep_auth_instances()
        assert "0914" in r.auth_instances
        r.auth_instances.pop("0914"); r._auth_started.pop("0914")

    @pytest.mark.asyncio
    async def test_a_browser_that_will_not_close_does_not_raise(self):
        """A dead browser must not turn into a 500 on somebody else's login."""
        import app.api.routes.auth as r

        class _Auth:
            async def close_browser(self): raise RuntimeError("already gone")

        r.auth_instances["0915"] = _Auth()
        r._auth_started["0915"] = 0.0
        await r._discard_auth_instance("0915", "test")
        assert "0915" not in r.auth_instances

    def test_the_login_route_discards_before_it_replaces(self):
        import inspect
        import app.api.routes.auth as r

        src = inspect.getsource(r.initiate_login)
        i = src.index("auth_instances[phone_number] = auth")
        assert "_discard_auth_instance" in src[:i], \
            "a new browser is stored before the previous one is closed"
