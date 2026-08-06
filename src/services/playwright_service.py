"""Playwright service HTTP client and HTML parsers.

The Playwright browser automation runs in a standalone containerized
service (e.g., FastAPI + Playwright). This module provides an HTTP
client for calling that service from agent code, as well as pure HTML
parsers for extracting content from rendered pages.

The service URL is configured via the PLAYWRIGHT_SERVICE_URL env var.
"""

from __future__ import annotations

from dataclasses import dataclass
import httpx
from bs4 import BeautifulSoup

from src.config import Settings

# Default timeout for Playwright service calls (seconds).
_DEFAULT_TIMEOUT = 30.0


class GreenhouseParseError(Exception):
    """Raised when required elements are missing from a Greenhouse job page."""


@dataclass(frozen=True)
class ParsedJobData:
    """Structured data extracted from a raw job page HTML."""

    title: str
    company: str
    location: str | None
    raw_jd: str


def parse_greenhouse_job_page(html: str) -> ParsedJobData:
    """Parse raw HTML from a Greenhouse job page.

    This function is completely pure: it takes an HTML string, uses BeautifulSoup
    to extract fields, and does NOT launch a browser or make network calls.

    Raises
    ------
    GreenhouseParseError
        If the title, company, or job description container cannot be found or is empty.
    """
    if not html or not html.strip():
        raise GreenhouseParseError("HTML content is empty.")

    soup = BeautifulSoup(html, "html.parser")

    # Title extraction (.app-title, h1.app-title, or h1 inside #main)
    title_el = soup.select_one(".app-title, h1.app-title, #main h1")
    title = title_el.get_text(strip=True) if title_el else None
    if not title:
        raise GreenhouseParseError("Missing required element: job title.")

    # Company name extraction (.company-name or .sub-heading)
    company_el = soup.select_one(".company-name, .sub-heading")
    company = company_el.get_text(strip=True) if company_el else None
    if not company:
        raise GreenhouseParseError("Missing required element: company name.")

    # Location extraction (.location)
    location_el = soup.select_one(".location")
    location = location_el.get_text(strip=True) if location_el else None

    # Raw JD text extraction (#content, .content, or #main)
    content_el = soup.select_one("#content, .content, .job-post-content")
    if not content_el:
        content_el = soup.select_one("#main")

    raw_jd = content_el.get_text(separator="\n", strip=True) if content_el else None
    if not raw_jd or len(raw_jd) < 20:
        raise GreenhouseParseError("Missing required element: job description content.")

    return ParsedJobData(
        title=title,
        company=company,
        location=location,
        raw_jd=raw_jd,
    )


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
