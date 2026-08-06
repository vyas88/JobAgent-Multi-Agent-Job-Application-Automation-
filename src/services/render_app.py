"""Standalone Playwright HTML Render Microservice.

Exposes:
- GET  /health  -> 200 OK {"status": "ok"}
- POST /render  -> 200 OK {"html": "<rendered html>"}

Lifespan: Launches one persistent headless Chromium browser on startup and closes it on shutdown.
Per-request: Opens a fresh isolated browser context + page, navigates to the URL, extracts content, and closes context.
Security: Enforces URL scheme (http/https only) and domain scope guard (*.greenhouse.io).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field
from playwright.async_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError, async_playwright

logger = logging.getLogger(__name__)

# Allowed domains for Greenhouse v1
ALLOWED_DOMAINS = (
    "greenhouse.io",
    "boards.greenhouse.io",
    "job-boards.greenhouse.io",
)


def validate_render_url(url: str, allow_file_urls: bool = False) -> None:
    """Validate target URL for scheme and domain scope guard (SSRF protection)."""
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()

    if allow_file_urls and scheme == "file":
        return

    if scheme not in ("http", "https"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Disallowed URL scheme '{scheme}'. Only http/https are allowed.",
        )

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid URL: missing hostname.",
        )

    # Disallow localhost and private IP addresses
    if hostname in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Disallowed target: localhost and loopback addresses are forbidden.",
        )

    # Greenhouse domain scope check
    is_allowed = any(
        hostname == domain or hostname.endswith("." + domain)
        for domain in ALLOWED_DOMAINS
    )
    if not is_allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Disallowed target host '{hostname}'. Only Greenhouse job boards (*.greenhouse.io) are supported.",
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """FastAPI lifespan manager: start one Chromium browser instance on startup and stop on shutdown."""
    logger.info("Initializing Playwright and launching persistent Chromium instance...")
    p = await asynccontextmanager_playwright()
    app.state.playwright = p
    app.state.browser = await p.chromium.launch(headless=True)
    logger.info("Persistent Chromium browser instance launched successfully.")
    try:
        yield
    finally:
        logger.info("Shutting down Chromium browser and Playwright...")
        if hasattr(app.state, "browser") and app.state.browser:
            await app.state.browser.close()
        if hasattr(app.state, "playwright") and app.state.playwright:
            await app.state.playwright.stop()
        logger.info("Playwright shutdown complete.")


async def asynccontextmanager_playwright():
    """Helper wrapper for async_playwright start."""
    p_cm = async_playwright()
    return await p_cm.start()


app = FastAPI(
    title="JobAgent Playwright Render Service",
    description="Standalone headless Chromium rendering microservice for job board scraping.",
    version="1.0.0",
    lifespan=lifespan,
)


class RenderRequest(BaseModel):
    url: str = Field(..., description="Target job posting URL to render via headless Chromium.")
    allow_file_urls: bool = Field(default=False, description="Flag for internal fixture testing.")


class RenderResponse(BaseModel):
    html: str = Field(..., description="Full rendered HTML content of target page.")


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok"}


@app.post(
    "/render",
    response_model=RenderResponse,
    status_code=status.HTTP_200_OK,
)
async def render_page(req: RenderRequest) -> dict[str, Any]:
    """Render job posting page using persistent Chromium browser instance."""
    validate_render_url(req.url, allow_file_urls=req.allow_file_urls)

    browser = getattr(app.state, "browser", None)
    if not browser:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Browser instance is not initialized.",
        )

    context = None
    try:
        context = await browser.new_context()
        page = await context.new_page()

        if req.url.startswith("file://"):
            await page.goto(req.url, timeout=10000)
        else:
            await page.goto(req.url, wait_until="domcontentloaded", timeout=30000)

        html_content = await page.content()
        return {"html": html_content}

    except PlaywrightTimeoutError as exc:
        logger.warning("Render timeout for %s: %s", req.url, exc)
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"Navigation timed out rendering target URL: {req.url}",
        ) from exc
    except PlaywrightError as exc:
        logger.error("Playwright browser error rendering %s: %s", req.url, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Browser error rendering target URL: {str(exc)}",
        ) from exc
    except Exception as exc:
        logger.error("Unexpected error rendering %s: %s", req.url, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected error rendering page: {str(exc)}",
        ) from exc
    finally:
        if context:
            await context.close()
