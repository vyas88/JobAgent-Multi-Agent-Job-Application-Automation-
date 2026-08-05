"""Tests for src.db — Postgres connection helper."""

from __future__ import annotations

from src.config import Settings


class TestDatabaseConfig:
    """Verify the database helper uses config correctly."""

    def test_settings_contain_database_url(self) -> None:
        """Settings.load() should provide a usable database_url."""
        settings = Settings.load()

        assert settings.database_url.startswith("postgresql://")
        assert "test" in settings.database_url  # Using test fixtures, not real creds.

    def test_database_url_is_not_hardcoded(self) -> None:
        """The database URL must come from env, never be hardcoded in code."""
        import inspect
        from src import db

        source = inspect.getsource(db)
        # Should not contain any hardcoded connection strings.
        assert "postgresql://" not in source
        assert "postgres://" not in source
