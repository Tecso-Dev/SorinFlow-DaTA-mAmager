"""
Reading the expiry of the token, not of the cookie carrying it.

Divar moved to SuperTokens, which splits a session in two: sAccessToken lasts
about an hour, sRefreshToken 364 days. Nothing here read the first one, so the
panel reported «۳۶۴ روز و ۱۶ ساعت» beside five accounts whose access tokens had
been dead since shortly after login — and every scrape was met with an SMS
prompt on its first contact reveal, seconds after each rotation.

Both numbers were real. They answer different questions, and the one nobody
asked is the one Divar decides on.
"""
import base64
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_at.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.services import divar_session as ds  # noqa: E402

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


def jwt(exp=None, *, payload=None, parts=3, pad=True):
    """A JWT-shaped string. Only the payload matters — nothing verifies it."""
    body = payload if payload is not None else ({"exp": exp} if exp else {})
    raw = base64.urlsafe_b64encode(json.dumps(body).encode()).decode()
    if not pad:
        raw = raw.rstrip("=")
    pieces = ["header", raw, "signature"][:parts]
    return ".".join(pieces)


def jar(value=None, name="sAccessToken", extra=None):
    out = []
    if value is not None:
        out.append({"name": name, "value": value})
    out.extend(extra or [])
    return out


class TestAccessTokenExpiry:
    def test_it_reads_the_exp_claim(self):
        exp = (NOW + timedelta(hours=1)).timestamp()
        assert ds.access_token_expiry(jar(jwt(exp))) == \
            datetime.fromtimestamp(exp, tz=timezone.utc)

    def test_the_result_is_timezone_aware(self):
        """Comparing an aware time against a naive one raises, and that
        exception would land inside a rotation."""
        got = ds.access_token_expiry(jar(jwt(NOW.timestamp())))
        assert got.tzinfo is not None

    def test_unpadded_base64url_still_decodes(self):
        """JWTs drop the padding; a decoder that demands it reads nothing."""
        exp = (NOW + timedelta(hours=1)).timestamp()
        assert ds.access_token_expiry(jar(jwt(exp, pad=False))) is not None

    def test_milliseconds_are_recognised(self):
        exp = (NOW + timedelta(hours=1)).timestamp()
        got = ds.access_token_expiry(jar(jwt(exp * 1000)))
        assert abs((got - datetime.fromtimestamp(exp, tz=timezone.utc)).total_seconds()) < 1

    def test_no_access_token_is_none(self):
        assert ds.access_token_expiry([]) is None
        assert ds.access_token_expiry(None) is None

    def test_a_refresh_token_alone_is_none(self):
        """It is a different cookie and carries a different lifetime."""
        assert ds.access_token_expiry(jar(jwt(1), name="sRefreshToken")) is None

    def test_a_non_jwt_value_is_none_not_a_crash(self):
        assert ds.access_token_expiry(jar("not-a-jwt")) is None

    def test_a_two_part_token_is_none(self):
        assert ds.access_token_expiry(jar(jwt(1, parts=2))) is None

    def test_a_payload_without_exp_is_none(self):
        assert ds.access_token_expiry(jar(jwt(payload={"sub": "x"}))) is None

    def test_an_empty_value_is_ignored(self):
        assert ds.access_token_expiry([{"name": "sAccessToken", "value": ""}]) is None

    def test_undecodable_payload_does_not_raise(self):
        assert ds.access_token_expiry(jar("a.!!!not-base64!!!.c")) is None


class TestAccessTokenState:
    def test_a_future_token_is_live(self):
        exp = (NOW + timedelta(hours=1)).timestamp()
        assert ds.access_token_state(jar(jwt(exp)), now=NOW) == "live"

    def test_an_expired_token_is_stale(self):
        exp = (NOW - timedelta(hours=2)).timestamp()
        assert ds.access_token_state(jar(jwt(exp)), now=NOW) == "stale"

    def test_a_token_expiring_within_the_skew_is_stale(self):
        """One that dies in ten seconds is already useless by the time a
        request lands, and acting on «live» there walks into the OTP prompt
        this check exists to avoid."""
        exp = (NOW + timedelta(seconds=10)).timestamp()
        assert ds.access_token_state(jar(jwt(exp)), now=NOW) == "stale"

    def test_no_token_is_unknown_not_stale(self):
        """Absent is not expired, and treating it as expired throws away
        sessions over a format change."""
        assert ds.access_token_state([], now=NOW) == "unknown"

    def test_an_undecodable_token_is_unknown_not_stale(self):
        assert ds.access_token_state(jar("garbage"), now=NOW) == "unknown"

    def test_the_skew_is_configurable(self):
        exp = (NOW + timedelta(seconds=120)).timestamp()
        assert ds.access_token_state(jar(jwt(exp)), now=NOW, skew_seconds=10) == "live"
        assert ds.access_token_state(jar(jwt(exp)), now=NOW, skew_seconds=300) == "stale"


class TestItIsADifferentQuestionFromTheCookieExpiry:
    """The bug in one test: both numbers real, only one of them decisive."""

    def test_a_year_long_cookie_can_carry_a_dead_token(self):
        dead = (NOW - timedelta(hours=3)).timestamp()
        year_away = (NOW + timedelta(days=364)).timestamp()
        j = [
            {"name": "sAccessToken", "value": jwt(dead), "expires": year_away},
            {"name": "sRefreshToken", "value": "r", "expires": year_away},
        ]
        assert ds.access_token_state(j, now=NOW) == "stale"
        # …while the cookie-level expiry, which is what the panel showed, is
        # nearly a year out.
        assert ds.derive_expiry(j) > NOW + timedelta(days=300)
