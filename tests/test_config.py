"""Tests for src.config — environment-based configuration loading."""

from __future__ import annotations

import pytest

from src.config import ConfigError, Settings


class TestSettings:
    """Verify that Settings loads correctly from environment variables."""

    def test_load_succeeds_with_required_vars(self) -> None:
        """Settings.load() should work when DATABASE_URL and OPENAI_API_KEY are set."""
        settings = Settings.load()

        assert settings.database_url == "postgresql://test:test@localhost:5432/jobagent_test"
        assert settings.openai_api_key == "sk-test-not-a-real-key"
        assert settings.playwright_service_url == "http://localhost:8000"

    def test_load_fails_without_database_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Settings.load() should raise ConfigError if DATABASE_URL is missing."""
        monkeypatch.delenv("DATABASE_URL", raising=False)

        with pytest.raises(ConfigError, match="DATABASE_URL"):
            Settings.load()

    def test_load_fails_without_openai_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Settings.load() should raise ConfigError if OPENAI_API_KEY is missing."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        with pytest.raises(ConfigError, match="OPENAI_API_KEY"):
            Settings.load()

    def test_playwright_url_has_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """PLAYWRIGHT_SERVICE_URL should fall back to localhost:8000."""
        monkeypatch.delenv("PLAYWRIGHT_SERVICE_URL", raising=False)

        settings = Settings.load()
        assert settings.playwright_service_url == "http://localhost:8000"
