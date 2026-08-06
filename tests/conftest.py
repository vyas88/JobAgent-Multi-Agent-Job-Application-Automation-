"""Shared pytest fixtures.

These fixtures provide common test data and environment patching so that
unit tests never require real credentials or live services, while integration
tests can access the real DATABASE_URL from .env when explicitly invoked.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

# Path to the fixtures/ directory at repo root.
FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"

# Load .env once at fixture loading time
load_dotenv()


@pytest.fixture()
def master_profile() -> dict:
    """Load the sample master profile fixture as a dict."""
    path = FIXTURES_DIR / "master_profile.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture()
def greenhouse_page_html() -> str:
    """Load the saved Greenhouse job page fixture as a string."""
    path = FIXTURES_DIR / "greenhouse_job_page.html"
    return path.read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _patch_env(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Set environment variables for tests.

    For unit tests: patches dummy variables so no live calls/credentials are made.
    For integration tests (@pytest.mark.integration): preserves the real DATABASE_URL.
    """
    if request.node.get_closest_marker("integration"):
        real_db = os.environ.get("DATABASE_URL")
        if real_db:
            monkeypatch.setenv("DATABASE_URL", real_db)
        monkeypatch.setenv("OPENAI_API_KEY", os.environ.get("OPENAI_API_KEY", "sk-test-not-a-real-key"))
        monkeypatch.setenv("PLAYWRIGHT_SERVICE_URL", "http://localhost:8000")
    else:
        monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost:5432/jobagent_test")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")
        monkeypatch.setenv("PLAYWRIGHT_SERVICE_URL", "http://localhost:8000")
