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


class TestParseDBRow:
    """Verify parse_db_row converts JSONB strings and non-JSON-native objects."""

    def test_parse_db_row_serializes_uuid_datetime_decimal(self) -> None:
        from datetime import datetime, timezone
        from decimal import Decimal
        from uuid import uuid4
        from src.db import parse_db_row

        test_uuid = uuid4()
        test_dt = datetime(2026, 8, 7, 12, 0, 0, tzinfo=timezone.utc)
        test_dec = Decimal("123.45")

        row = {
            "id": test_uuid,
            "created_at": test_dt,
            "fit_score": test_dec,
            "nested": {"sub_id": test_uuid, "sub_dt": test_dt},
        }

        parsed = parse_db_row(row)
        assert parsed["id"] == str(test_uuid)
        assert parsed["created_at"] == "2026-08-07T12:00:00+00:00"
        assert parsed["fit_score"] == 123.45
        assert parsed["nested"]["sub_id"] == str(test_uuid)
        assert parsed["nested"]["sub_dt"] == "2026-08-07T12:00:00+00:00"
