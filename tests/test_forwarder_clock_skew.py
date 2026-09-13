"""
«after it send like 5-6 time the code to site, after that it not send code.»

The forwarder kept working. What stopped was the server accepting what it
sent.

An arriving code was judged stale by comparing sentStamp — off the PHONE's
clock — against the request's timestamp, off the server's. Those are two
clocks. A handset without time sync drifts, the drift only grows, and the
moment it passes the ten seconds of slack EVERY code reads as stale. Nothing
looks broken from either end: the phone reports success, the panel shows a
prompt nobody answers.

The fix measures the difference from the same message rather than trusting
either clock. receivedStamp is when the phone saw the SMS; now_ms is when we
saw the POST; the gap is transit plus offset.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_skew.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.api.routes import scraper as sr  # noqa: E402

SRC = inspect.getsource(sr.otp_inbound)


class TestTheSkewIsMeasured:
    def test_it_is_computed_from_the_same_message(self):
        assert "clock_skew_ms = (now_ms - int(body.receivedStamp))" in SRC

    def test_a_message_without_a_received_stamp_yields_no_measurement(self):
        assert "if body.receivedStamp else None" in SRC

    def test_hours_of_it_is_not_treated_as_drift(self):
        """A clock set to the wrong year would otherwise 'correct' every
        stamp by months."""
        assert "abs(clock_skew_ms) > 6 * 3600 * 1000" in SRC


class TestTheStalenessTestUsesIt:
    def test_the_stamp_is_corrected_before_comparison(self):
        assert "int(body.sentStamp) + (clock_skew_ms or 0)" in SRC

    def test_an_uncorrectable_stamp_is_not_judged_on(self):
        """Better to accept a code we have a live request for than to reject
        every one because a clock is nonsense."""
        i = SRC.index("int(body.sentStamp) + (clock_skew_ms or 0)")
        assert "sent_ms = None" in SRC[i:i + 500]

    def test_the_guard_itself_survives(self):
        """Its purpose is real: a late first code must not overwrite the
        resend that replaced it."""
        assert 'reason = "stale_code"' in SRC
        assert 'entry["ts"] - 10' in SRC


class TestItIsVisibleAfterwards:
    def test_the_skew_is_recorded_on_the_event(self):
        """A number nobody can see is a fault nobody can find — and this one
        hid for a week."""
        assert "clock_skew_ms=clock_skew_ms" in SRC

    def test_it_is_in_the_container_log_too(self):
        assert "clock_skew_ms={clock_skew_ms}" in SRC

    def test_the_reason_is_still_recorded_for_every_arrival(self):
        assert "reason=reason" in SRC


class TestTheArithmeticOfTheBug:
    """Stated as numbers, with the real thresholds."""

    SLACK_S = 10

    def test_a_phone_ten_seconds_behind_still_worked(self):
        drift = 9
        assert not (-drift < -self.SLACK_S)

    def test_a_phone_a_minute_behind_rejected_everything(self):
        drift = 60
        assert -drift < -self.SLACK_S

    def test_correcting_by_the_measured_offset_restores_it(self):
        drift_ms = 60_000
        sent_ms = 1_000_000          # phone's clock
        received_ms = sent_ms + 500  # phone saw it half a second later
        now_ms = received_ms + drift_ms
        skew = now_ms - received_ms
        assert (sent_ms + skew) / 1000.0 >= (now_ms / 1000.0) - self.SLACK_S
