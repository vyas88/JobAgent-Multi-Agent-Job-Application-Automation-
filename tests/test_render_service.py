"""Unit and integration tests for the standalone Playwright Render Service.

Unit tests mock Playwright/browser to ensure NO real browser processes are launched in default pytest.
Integration tests marked @pytest.mark.integration test real Playwright rendering.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from src.services.render_app import app, validate_render_url


@pytest.fixture
def render_client():
    """FastAPI TestClient with mocked browser on app.state."""
    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.content = AsyncMock(return_value="<html><body><h1>Software Engineer</h1></body></html>")

    mock_context = AsyncMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)
    mock_context.close = AsyncMock()

    mock_browser = AsyncMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_browser.close = AsyncMock()

    mock_playwright = AsyncMock()
    mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)
    mock_playwright.stop = AsyncMock()

    with patch("src.services.render_app.asynccontextmanager_playwright", new=AsyncMock(return_value=mock_playwright)):
        with TestClient(app) as client:
            yield client, mock_browser, mock_context, mock_page

    app.state.browser = None
    app.state.playwright = None


class TestRenderServiceUnit:
    """Unit tests for Render Service API without launching real browser."""

    def test_health_check_returns_200(self, render_client) -> None:
        """GET /health returns 200 OK {"status": "ok"}."""
        client, _, _, _ = render_client
        res = client.get("/health")
        assert res.status_code == 200
        assert res.json() == {"status": "ok"}

    def test_render_reused_browser_success(self, render_client) -> None:
        """POST /render reuses browser, opens isolated context/page, and returns HTML."""
        client, mock_browser, mock_context, _ = render_client

        res = client.post(
            "/render",
            json={"url": "https://boards.greenhouse.io/testco/jobs/12345"},
        )

        assert res.status_code == 200
        assert "Software Engineer" in res.json()["html"]
        mock_browser.new_context.assert_called_once()
        mock_context.close.assert_called_once()

    def test_render_disallowed_domain_returns_400(self, render_client) -> None:
        """POST /render returns 400 Bad Request for non-Greenhouse domains (SSRF guard)."""
        client, _, _, _ = render_client

        res = client.post(
            "/render",
            json={"url": "https://malicious-site.com/steal-data"},
        )

        assert res.status_code == 400
        assert "Disallowed target host" in res.json()["detail"]

    def test_render_disallowed_scheme_returns_400(self, render_client) -> None:
        """POST /render returns 400 Bad Request for ftp or local schemes."""
        client, _, _, _ = render_client

        res = client.post(
            "/render",
            json={"url": "ftp://boards.greenhouse.io/job"},
        )

        assert res.status_code == 400
        assert "Disallowed URL scheme" in res.json()["detail"]

    def test_render_localhost_returns_400(self, render_client) -> None:
        """POST /render returns 400 Bad Request for localhost targets (SSRF guard)."""
        client, _, _, _ = render_client

        res = client.post(
            "/render",
            json={"url": "http://localhost:8000/internal-secret"},
        )

        assert res.status_code == 400
        assert "localhost" in res.json()["detail"]

    def test_render_timeout_returns_504(self, render_client) -> None:
        """POST /render returns 504 Gateway Timeout when Playwright navigation times out."""
        client, _, _, mock_page = render_client
        mock_page.goto.side_effect = PlaywrightTimeoutError("Navigation timeout 30000ms exceeded")

        res = client.post(
            "/render",
            json={"url": "https://job-boards.greenhouse.io/slowco/jobs/999"},
        )

        assert res.status_code == 504
        assert "timed out" in res.json()["detail"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_render_app_integration_real_browser() -> None:
    """Integration test: verifies real Playwright rendering against local HTML fixture."""
    import httpx
    from playwright.async_api import async_playwright

    fixture_path = Path("fixtures/greenhouse_job_page.html").resolve()
    file_url = f"file://{fixture_path}"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        app.state.playwright = p
        app.state.browser = browser

        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                res = await client.post(
                    "/render",
                    json={"url": file_url, "allow_file_urls": True},
                )
                assert res.status_code == 200
                html = res.json()["html"]
                assert "Software Engineer" in html or "Greenhouse" in html
        finally:
            await browser.close()
            app.state.browser = None
            app.state.playwright = None
