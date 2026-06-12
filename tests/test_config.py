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

    def test_cors_origins_default_is_wildcard(self):
        from app.config import Settings
        s = Settings()
        assert s.cors_origins == "*"

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
