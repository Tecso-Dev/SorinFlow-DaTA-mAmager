"""
Issue #12 — the three endpoints email 2FA added were unauthenticated in
exactly the way public_auth.py's are, and called neither check_ip_budget
nor spend_ip_budget. One host could walk a list of phone numbers at five
requests each with nothing to stop it, and every request that hit a real
account sent mail — through the Gmail account whose quota the email second
factor shares. Exhausting it would block logins for everyone using it.

Done when the three behave like public_auth.py's routes under a burst from
one IP, and a single source cannot probe N identifiers without being cut
off.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_ipb.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

import pytest  # noqa: E402
from app.api.routes import users as u  # noqa: E402
from app.services import verification as v  # noqa: E402

THREE = (u.verify_email_login, u.password_reset_request, u.password_reset_confirm)


class TestEachTakesTheRequest:
    @pytest.mark.parametrize("fn", THREE, ids=lambda f: f.__name__)
    def test_the_request_is_in_the_signature(self, fn):
        """client_ip needs it; the endpoints did not take it before."""
        assert "request" in inspect.signature(fn).parameters


class TestEachChecksBeforeAnyWork:
    @pytest.mark.parametrize("fn", THREE, ids=lambda f: f.__name__)
    def test_the_budget_is_checked(self, fn):
        assert "await check_ip_budget(request," in inspect.getsource(fn)

    @pytest.mark.parametrize("fn", THREE, ids=lambda f: f.__name__)
    def test_a_spent_budget_is_a_429(self, fn):
        src = inspect.getsource(fn)
        i = src.index("await check_ip_budget(request,")
        assert "status_code=429" in src[i:i + 300]

    def test_reset_request_checks_before_the_lookup(self):
        """So the refusal says nothing about any account — this is the half of
        issue #11 that a per-IP budget solves for free."""
        src = inspect.getsource(u.password_reset_request)
        assert src.index("await check_ip_budget(request,") < src.index("select(User)")


class TestWhatIsCounted:
    def test_a_reset_request_is_counted_whether_or_not_the_name_is_real(self):
        """Probing an unknown name is still probing. Counting only real hits
        would let the walk continue free between them, and make the moment
        the budget runs out depend on how many real accounts the list held."""
        src = inspect.getsource(u.password_reset_request)
        assert src.index('await spend_ip_budget(request, "code")') < \
            src.index("if not user or not (user.email")

    def test_a_wrong_code_is_counted(self):
        for fn in (u.verify_email_login, u.password_reset_confirm):
            src = inspect.getsource(fn)
            # the budget check has its own except above; want the verify's
            i = src.index("except VerificationError as e:", src.index("await verify_code("))
            assert 'await spend_ip_budget(request, "verify")' in src[i:i + 200], fn.__name__

    def test_a_confirm_against_an_unknown_name_is_counted_too(self):
        src = inspect.getsource(u.password_reset_confirm)
        i = src.index("if not user:")
        assert 'await spend_ip_budget(request, "verify")' in src[i:i + 200]


class TestTheBuckets:
    def test_sending_mail_shares_the_bucket_public_auth_uses_for_sending(self):
        """One quota is being protected — the Gmail account's — so one budget."""
        assert '"code", IP_CODE_LIMIT' in inspect.getsource(u.password_reset_request)
        assert '"code", IP_CODE_LIMIT' in open(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "app/api/routes/public_auth.py"), encoding="utf-8").read()

    def test_guessing_has_its_own_looser_bucket(self):
        """A guess sends nothing; the per-identifier attempt cap is the real
        lock. This only stops one host walking many identifiers at the cap."""
        assert v.IP_VERIFY_LIMIT > v.IP_CODE_LIMIT
        for fn in (u.verify_email_login, u.password_reset_confirm):
            assert '"verify", IP_VERIFY_LIMIT' in inspect.getsource(fn), fn.__name__


class TestASingleSourceIsCutOff:
    """The done-when, driven through the real budget code against a fake
    Redis: N identifiers from one IP, and the (N+1)th is refused."""

    class FakeRedis:
        def __init__(self):
            self.kv = {}
        async def get(self, k):
            return self.kv.get(k)
        async def ttl(self, k):
            return 1800
        def pipeline(self):
            outer = self
            class P:
                def __init__(s):
                    s.ops = []
                def incr(s, k):
                    s.ops.append(k)
                def expire(s, k, t):
                    pass
                async def execute(s):
                    for k in s.ops:
                        outer.kv[k] = int(outer.kv.get(k) or 0) + 1
            return P()

    class Req:
        headers = {}
        class client:
            host = "203.0.113.9"

    @pytest.mark.asyncio
    async def test_the_walk_is_stopped_at_the_limit(self, monkeypatch):
        r = self.FakeRedis()
        async def _redis():
            return r
        monkeypatch.setattr(v, "get_redis", _redis)
        req = self.Req()
        for _ in range(v.IP_CODE_LIMIT):
            await v.check_ip_budget(req, "code", v.IP_CODE_LIMIT)
            await v.spend_ip_budget(req, "code")
        with pytest.raises(v.VerificationError):
            await v.check_ip_budget(req, "code", v.IP_CODE_LIMIT)
