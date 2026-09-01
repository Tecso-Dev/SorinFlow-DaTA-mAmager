"""
What a verification code actually proves.

A code is delivered over one channel and checked in a later, separate request.
Nothing used to carry the channel across that gap, so verify credited the phone
no matter where the code had really gone. With no SMS provider configured every
code goes to the address (see verification._deliver), which made that the normal
case rather than an edge one: people were being marked phone_verified on the
strength of an email they read.

That is not a cosmetic error. The panel shows the tick to whoever is about to
ring the number, and the SMS marketing audience reads it as permission to text.

These tests run without Postgres on purpose — the logic lives in the service,
and tests/test_auth_roles.py skips entirely on SQLite, which is where this
would have gone unnoticed.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_vchannel.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

PURPOSE = "signup"
PHONE = "09121112233"


@pytest.fixture
def redis(monkeypatch):
    """A real Redis implementation, in memory — TTLs and counters included."""
    import fakeredis.aioredis
    import app.services.verification as v

    # An explicit server per test. Without one, FakeRedis instances share
    # state, and issue_code's resend cooldown from the previous test refuses
    # the next test's first code — a failure that looks like a bug in the code
    # under test rather than in the fixture.
    fake = fakeredis.aioredis.FakeRedis(
        server=fakeredis.FakeServer(), decode_responses=True)

    async def _get():
        return fake
    monkeypatch.setattr(v, "get_redis", _get)
    return fake


def _deliver_over(monkeypatch, channel):
    """Pin the delivery leg to one channel, as if only that one were configured.

    Returns a dict that fills in with the code actually handed to delivery.
    Reading it from here rather than from IssuedCode.debug_code matters: that
    field is suppressed outside development, and the settings object is an
    lru_cached singleton that an earlier test module mutates — so a test built
    on debug_code passes alone and fails in a full run, which is the least
    useful way for a test to fail.
    """
    import app.services.verification as v
    box = {}

    async def fake_deliver(code, **kw):
        box["code"] = code
        return channel
    monkeypatch.setattr(v, "_deliver", fake_deliver)
    return box


async def _issue(**kw):
    from app.services.verification import issue_code
    return await issue_code(PURPOSE, PHONE, PHONE, **kw)


class TestTheChannelSurvivesTheRoundTrip:

    @pytest.mark.asyncio
    @pytest.mark.parametrize("channel", ["email", "sms"])
    async def test_verify_reports_the_channel_the_code_travelled_over(
            self, redis, monkeypatch, channel):
        from app.services.verification import verify_code

        sent = _deliver_over(monkeypatch, channel)
        issued = await _issue()
        assert issued.channel == channel

        assert await verify_code(PURPOSE, PHONE, sent["code"]) == channel

    @pytest.mark.asyncio
    async def test_an_email_code_never_reports_itself_as_sms(
            self, redis, monkeypatch):
        """The whole bug in one assertion."""
        from app.services.verification import verify_code

        sent = _deliver_over(monkeypatch, "email")
        await _issue()
        assert await verify_code(PURPOSE, PHONE, sent["code"]) != "sms"

    @pytest.mark.asyncio
    async def test_the_channel_is_consumed_with_the_code(self, redis, monkeypatch):
        """Left behind, it would answer for the *next* code — which may well
        have gone somewhere else."""
        from app.services.verification import verify_code, _keys

        sent = _deliver_over(monkeypatch, "sms")
        await _issue()
        await verify_code(PURPOSE, PHONE, sent["code"])
        assert await redis.get(_keys(PURPOSE, PHONE)["channel"]) is None

    @pytest.mark.asyncio
    async def test_a_code_with_no_recorded_channel_does_not_claim_the_phone(
            self, redis, monkeypatch):
        """A code issued by the previous image mid-rollout has no channel key.

        Defaulting to "sms" there would invent exactly the claim this change
        exists to stop inventing.
        """
        from app.services.verification import verify_code, _keys

        sent = _deliver_over(monkeypatch, "sms")
        await _issue()
        await redis.delete(_keys(PURPOSE, PHONE)["channel"])

        assert await verify_code(PURPOSE, PHONE, sent["code"]) != "sms"

    @pytest.mark.asyncio
    async def test_a_wrong_code_still_fails(self, redis, monkeypatch):
        """Returning a string instead of True must not make failure truthy."""
        from app.services.verification import verify_code, VerificationError

        _deliver_over(monkeypatch, "email")
        await _issue()
        with pytest.raises(VerificationError):
            await verify_code(PURPOSE, PHONE, "00000")


class TestTheRouteCreditsOnlyWhatWasProven:
    """The branch is four lines in public_auth; these pin its shape, because
    the end-to-end version of this test skips on SQLite."""

    def test_verify_credits_the_phone_only_on_the_sms_path(self):
        import inspect
        from app.api.routes import public_auth

        src = inspect.getsource(public_auth.portal_verify)
        assert "channel = await verify_code" in src
        assert 'if channel == "sms":' in src
        i = src.index('if channel == "sms":')
        assert "user.phone_verified = True" in src[i:i + 200]
        # and the unconditional version is gone
        assert src.count("user.phone_verified = True") == 1

    def test_email_verification_is_recorded_separately(self):
        import inspect
        from app.api.routes import public_auth

        src = inspect.getsource(public_auth.portal_verify)
        assert "user.email_verified = True" in src

    @pytest.mark.parametrize("fn_name", ["portal_login"])
    def test_either_proof_opens_the_account(self, fn_name):
        """Gating on the phone alone would loop every account created while
        email is the only channel: verify by email, still fail the gate."""
        import inspect
        from app.api.routes import public_auth

        src = inspect.getsource(getattr(public_auth, fn_name))
        assert "user.phone_verified or user.email_verified" in src

    def test_the_portal_gate_accepts_either_proof(self):
        import inspect
        from app.api.routes import portal

        src = inspect.getsource(portal._visitor)
        assert "phone_verified or current_user.email_verified" in src


class TestTheModelAndTheMigrationAgree:

    def test_the_column_exists_on_the_model(self):
        from app.models.user import User
        assert hasattr(User, "email_verified")

    def test_boot_refuses_to_start_without_it(self):
        """The model selects it on every query, so a skipped ALTER breaks every
        request rather than degrading one screen."""
        import inspect
        from app import database

        src = inspect.getsource(database._verify_auth_v2)
        assert "email_verified" in src

    def test_the_migration_adds_it(self):
        import inspect
        from app import database

        src = inspect.getsource(database._migrate_auth_v2)
        assert "ADD COLUMN IF NOT EXISTS email_verified" in src
        # the catalog check must know about it too, or the ALTER is skipped
        # on every already-migrated database and the column never appears
        i = src.index("<= present")
        assert "email_verified" in src[max(0, i - 300):i]
