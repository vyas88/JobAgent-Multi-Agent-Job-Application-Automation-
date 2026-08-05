"""Playwright service HTTP client.

The Playwright browser automation runs in a standalone containerized
service (e.g., FastAPI + Playwright). This module provides an HTTP
client for calling that service from agent code.

The service URL is configured via the PLAYWRIGHT_SERVICE_URL env var.

Implements: used starting in Phase 1 (fetch/render) and Phase 3 (form fill).
"""

from __future__ import annotations

import httpx

from src.config import Settings

# Default timeout for Playwright service calls (seconds).
_DEFAULT_TIMEOUT = 30.0


async def fetch_page(
    url: str,
    *,
    settings: Settings | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> str:
    """Fetch a rendered page via the Playwright service.

    Parameters
    ----------
    url:
        The career-page URL to fetch and render.
    settings:
        Application settings; loaded from env if not provided.
    timeout:
        HTTP request timeout in seconds.

    Returns
    -------
    The rendered HTML content of the page.
    """
    if settings is None:
        settings = Settings.load()

    # TODO: Implement actual endpoint contract in Phase 1.
    async with httpx.AsyncClient(
        base_url=settings.playwright_service_url,
        timeout=timeout,
    ) as client:
        response = await client.post(
            "/render",
            json={"url": url},
        )
        response.raise_for_status()
        return response.json()["html"]
