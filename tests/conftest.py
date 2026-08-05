"""Shared pytest fixtures.

These fixtures provide common test data and environment patching so that
no test ever requires real credentials or live services.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# Path to the fixtures/ directory at repo root.
FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"


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
def _patch_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set dummy environment variables for every test.

    This ensures no test accidentally uses real credentials and that
    config.Settings.load() always succeeds in tests.
    """
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost:5432/jobagent_test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")
    monkeypatch.setenv("PLAYWRIGHT_SERVICE_URL", "http://localhost:8000")
