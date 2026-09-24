"""Unit tests for app configuration."""
import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestSettings:
    def test_default_values_are_set(self):
        from app.config import Settings
        s = Settings()
        assert s.app_name == "SorinFlow Divar Scraper"
        assert s.environment == "production"
        assert s.debug is False

    def test_cors_origins_default_is_same_origin_only(self):
        from app.config import Settings
        s = Settings()
        assert s.cors_origins == ""

    def test_api_key_is_a_string(self):
        from app.config import Settings
        s = Settings()
        assert isinstance(s.api_key, str)

    def test_secret_key_is_a_string(self):
        from app.config import Settings
        s = Settings()
        assert isinstance(s.secret_key, str)
        assert len(s.secret_key) > 0

    def test_env_var_overrides_api_key(self, monkeypatch):
        monkeypatch.setenv("API_KEY", "my-test-key-123")
        from importlib import reload
        import app.config as cfg_module
        # Force a fresh Settings instance (bypass lru_cache)
        s = cfg_module.Settings()
        assert s.api_key == "my-test-key-123"

    def test_env_var_overrides_cors_origins(self, monkeypatch):
        monkeypatch.setenv("CORS_ORIGINS", "https://example.com,https://other.com")
        from app.config import Settings
        s = Settings()
        assert s.cors_origins == "https://example.com,https://other.com"

    def test_env_var_overrides_debug(self, monkeypatch):
        monkeypatch.setenv("DEBUG", "true")
        from app.config import Settings
        s = Settings()
        assert s.debug is True

    def test_get_settings_returns_settings_instance(self):
        from app.config import get_settings, Settings
        s = get_settings()
        assert isinstance(s, Settings)

    def test_scraper_headless_default_true(self):
        from app.config import Settings
        s = Settings()
        assert s.scraper_headless is True

    def test_proxy_enabled_default_false(self):
        from app.config import Settings
        s = Settings()
        assert s.proxy_enabled is False


class TestEnvAliasesMatchDocumentedNames:
    """Field(..., env="X") is pydantic v1 syntax. pydantic-settings 2.1.0
    silently ignores it (it becomes json_schema_extra), so a field whose
    documented env name differed from its own name — SCRAPER_REST_AFTER_REVEALS
    for rest_after_reveals, for one — was never actually readable from the
    environment it documented. Fixed with
    validation_alias=AliasChoices(env_name, field_name), which keeps both the
    documented name and the field name working."""

    def test_a_mismatched_scraper_pacing_var_now_works(self, monkeypatch):
        monkeypatch.setenv("SCRAPER_REST_AFTER_REVEALS", "77")
        from app.config import Settings
        assert Settings().rest_after_reveals == 77

    def test_a_second_mismatched_var(self, monkeypatch):
        monkeypatch.setenv("SCRAPER_REVEAL_DWELL_MIN", "9.5")
        from app.config import Settings
        assert Settings().reveal_dwell_min_seconds == 9.5

    def test_a_third_mismatched_var(self, monkeypatch):
        monkeypatch.setenv("SCRAPER_CHALLENGE_GOAL_REVEALS", "42")
        from app.config import Settings
        assert Settings().challenge_goal_reveals == 42

    def test_the_field_name_itself_still_works_as_a_fallback(self, monkeypatch):
        # AliasChoices keeps both names alive, so anywhere already relying on
        # the (undocumented, but previously the only one that worked) field
        # name does not go dark either.
        monkeypatch.setenv("REST_AFTER_REVEALS", "13")
        from app.config import Settings
        assert Settings().rest_after_reveals == 13

    def test_a_field_whose_name_already_matched_its_env_still_works(self, monkeypatch):
        monkeypatch.setenv("AUTH_SMS_DAILY_CAP", "50")
        from app.config import Settings
        assert Settings().auth_sms_daily_cap == 50

    def test_git_sha_from_env(self, monkeypatch):
        monkeypatch.setenv("GIT_SHA", "deadbeef")
        from app.config import Settings
        assert Settings().git_sha == "deadbeef"

    def test_git_sha_defaults_to_empty(self, monkeypatch):
        monkeypatch.delenv("GIT_SHA", raising=False)
        from app.config import Settings
        assert Settings().git_sha == ""

    def test_dotenv_file_also_reaches_a_previously_mismatched_field(self, tmp_path, monkeypatch):
        (tmp_path / ".env").write_text("SCRAPER_REST_HOURS=3\n")
        monkeypatch.chdir(tmp_path)
        from app.config import Settings
        assert Settings().rest_hours == 3.0


class TestCitiesAndCategories:
    def test_cities_is_non_empty_dict(self):
        from app.config import CITIES
        assert isinstance(CITIES, dict)
        assert len(CITIES) > 0

    def test_categories_is_non_empty_dict(self):
        from app.config import CATEGORIES
        assert isinstance(CATEGORIES, dict)
        assert len(CATEGORIES) > 0

    def test_tehran_is_in_cities(self):
        from app.config import CITIES
        assert "tehran" in CITIES

    def test_city_values_have_name_field(self):
        from app.config import CITIES
        for key, value in CITIES.items():
            assert isinstance(key, str), f"City key {key!r} is not a string"
            assert isinstance(value, dict), f"City value for {key!r} is not a dict"
            assert "name" in value, f"City {key!r} missing 'name' field"

    def test_all_category_keys_are_strings(self):
        from app.config import CATEGORIES
        for key in CATEGORIES:
            assert isinstance(key, str)
