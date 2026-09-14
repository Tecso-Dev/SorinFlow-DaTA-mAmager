"""
Issue #11 — the password reset leaked whether an account exists, via the
429. The endpoint was built to answer identically either way, and did — on
the 200. But the per-identifier throttle inside issue_code was only reachable
on the branch that had found a user, so the sixth request for a real account
answered 429 where the sixth for a stranger answered 200. Panel usernames are
phone numbers; that enumerated which numbers have accounts.

The fix is sobhan's «3 combined with 1»: the per-IP budget (#12) refuses a
noisy host out loud and says nothing about any account, and the
per-identifier throttle is swallowed into same_answer.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_enum.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.api.routes import users as u  # noqa: E402

SRC = inspect.getsource(u.password_reset_request)


class TestTheOracleIsClosed:
    def test_the_identifier_throttle_no_longer_raises(self):
        i = SRC.index("await issue_code(")
        after = SRC[i:]
        j = after.index("except VerificationError as e:")
        k = after.index("except Exception as e:")
        assert "raise HTTPException" not in after[j:k], \
            "a 429 reachable only for a real account is the oracle"

    def test_it_returns_the_same_answer_instead(self):
        i = SRC.index("await issue_code(")
        assert SRC[i:].rstrip().endswith("return same_answer")

    def test_it_is_still_logged_so_a_real_throttle_is_not_invisible(self):
        assert "[reset] throttled" in SRC

    def test_the_only_429_left_is_the_per_ip_one(self):
        """Which is reachable before the lookup and says nothing about any
        account."""
        assert SRC.count("status_code=429") == 1
        assert SRC.index("status_code=429") < SRC.index("select(User)")


class TestTheSameAnswerStillCoversEveryOtherBranch:
    def test_unknown_user(self):
        assert "if not user or not (user.email" in SRC
        i = SRC.index("if not user or not (user.email")
        assert "return same_answer" in SRC[i:i + 150]

    def test_a_send_failure(self):
        i = SRC.index("except Exception as e:")
        assert "return same_answer" in SRC[i:i + 200]

    def test_the_reason_is_written_beside_the_swallow(self):
        """The next person will be tempted to put the 429 back — the comment
        there says why they must not."""
        assert "oracle" in SRC and "enumerated" in SRC
