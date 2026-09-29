"""A blank environment value must not stop the app from starting.

Setting a key to "" is how an operator says a knob is not configured, and the
k8s manifests mark several of them `optional: true`. But app/config.py has 49
int/float/bool settings, and pydantic reads "" as a malformed number: one
blank value and Settings() raises at import, so every pod crash-loops — the
api, the worker, the scheduler and the migrate Job — on a traceback that names
the type and not the knob.

Found in the phase-4 k3d rehearsal: a secret with SMTP_PORT="" took the whole
cluster down before a single migration ran.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import Settings  # noqa: E402

BASE = {
    "DATABASE_URL": "sqlite+aiosqlite:///./_test_blank.db",
    "SECRET_KEY": "0123456789abcdef0123456789abcdef",
    "LOGS_PATH": "/tmp",
    "IMAGES_PATH": "/tmp",
}


def _settings(monkeypatch, **env):
    for k, v in {**BASE, **env}.items():
        monkeypatch.setenv(k, v)
    return Settings()


@pytest.mark.parametrize("key, attr, default", [
    ("SMTP_PORT", "smtp_port", 587),
    ("SCRAPER_DELAY_MIN", "scraper_delay_min", 2.0),
    ("MAX_IMAGES_PER_PROPERTY", "max_images_per_property", None),
    ("DIVAR_SESSION_CHECK_MINUTES", "divar_session_check_minutes", None),
])
def test_a_blank_number_falls_back_to_its_default(monkeypatch, key, attr, default):
    expected = default if default is not None else Settings.model_fields[attr].default
    assert getattr(_settings(monkeypatch, **{key: ""}), attr) == expected


def test_a_blank_boolean_falls_back_too(monkeypatch):
    assert _settings(monkeypatch, DEBUG="").debug is Settings.model_fields["debug"].default
    assert _settings(monkeypatch, PROXY_ENABLED="").proxy_enabled is Settings.model_fields["proxy_enabled"].default


def test_whitespace_counts_as_blank(monkeypatch):
    """A secret written with a trailing newline is the same mistake."""
    assert _settings(monkeypatch, SMTP_PORT="  \n ").smtp_port == 587


def test_an_empty_string_setting_keeps_its_empty_value(monkeypatch):
    """Only the fields that cannot hold one are dropped. For a string, "" is a
    real setting — a blank bot token means no Telegram, and turning that into
    the default would switch a feature back on."""
    s = _settings(monkeypatch, TELEGRAM_BOT_TOKEN="", LLM_API_KEY="")
    assert s.telegram_bot_token == ""
    assert s.llm_api_key == ""


def test_a_real_value_is_still_read(monkeypatch):
    """The fallback must not swallow a value that was actually given."""
    s = _settings(monkeypatch, SMTP_PORT="2525", DEBUG="true")
    assert s.smtp_port == 2525
    assert s.debug is True


def test_every_numeric_setting_survives_being_blanked(monkeypatch):
    """Not one at a time by name: blank every int/float/bool at once, the way
    a half-filled secret would, and the app still has to start."""
    numeric = {n: f for n, f in Settings.model_fields.items() if f.annotation in (int, float, bool)}
    assert len(numeric) > 30, "expected dozens of numeric settings; the sweep below is the point"
    env = {}
    for name, f in numeric.items():
        alias = f.validation_alias
        names = [a for a in getattr(alias, "choices", []) or [] if isinstance(a, str)] or [name.upper()]
        env[names[0]] = ""
    s = _settings(monkeypatch, **env)
    for name, f in numeric.items():
        assert getattr(s, name) == f.default, name
