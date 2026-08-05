"""Configuration loader.

All secrets and connection strings come from environment variables.
Never hardcode secrets. In local dev, values are loaded from .env via
python-dotenv (the .env file is gitignored).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# Load .env in local dev; no-op in production if the file doesn't exist.
load_dotenv()


class ConfigError(Exception):
    """Raised when a required environment variable is missing."""


def _require_env(name: str) -> str:
    """Return the value of an env var or raise with a clear message."""
    value = os.environ.get(name)
    if not value:
        raise ConfigError(
            f"Required environment variable '{name}' is not set. "
            "See .env.example for the full list."
        )
    return value


@dataclass(frozen=True)
class Settings:
    """Application settings loaded from the environment."""

    database_url: str
    openai_api_key: str
    playwright_service_url: str = "http://localhost:8000"

    @classmethod
    def load(cls) -> Settings:
        """Build settings from environment variables."""
        return cls(
            database_url=_require_env("DATABASE_URL"),
            openai_api_key=_require_env("OPENAI_API_KEY"),
            playwright_service_url=os.environ.get(
                "PLAYWRIGHT_SERVICE_URL", "http://localhost:8000"
            ),
        )
