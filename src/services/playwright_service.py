"""Playwright service HTTP client, HTML parsers, and Greenhouse form automation.

The Playwright browser automation runs in a standalone containerized
service (e.g., FastAPI + Playwright) or directly via Playwright in Python.
This module provides HTML parsing, form mapping, and Playwright browser actions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

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


@dataclass(frozen=True)
class FormMappingResult:
    """Structured output of form mapping logic."""

    field_map: dict[str, dict[str, Any]]
    unanswered_questions: list[str]
    captcha_detected: bool
    missing_required_fields: bool


def parse_greenhouse_job_page(html: str) -> ParsedJobData:
    """Parse raw HTML from a Greenhouse job page.

    This function is completely pure: it takes an HTML string, uses BeautifulSoup
    to extract fields, and does NOT launch a browser or make network calls.
    Supports both standard Greenhouse layout and new job-boards.greenhouse.io layout.
    """
    if not html or not html.strip():
        raise GreenhouseParseError("HTML content is empty.")

    soup = BeautifulSoup(html, "html.parser")

    # Title extraction (.app-title, h1.app-title, h1.section-header--large, .section-header--large, or #main h1)
    title_el = soup.select_one(".app-title, h1.app-title, h1.section-header--large, .section-header--large, .application--header--title, #main h1")
    title = title_el.get_text(strip=True) if title_el else None
    if not title:
        raise GreenhouseParseError("Missing required element: job title.")

    # Company name extraction (.company-name or .sub-heading)
    # Note: New layout does not include an explicit company element. Default to "Unknown" if missing.
    company_el = soup.select_one(".company-name, .sub-heading")
    company = company_el.get_text(strip=True) if company_el else "Unknown"

    # Location extraction (.location)
    location_el = soup.select_one(".location, .application--header--text")
    location = location_el.get_text(strip=True) if location_el else None

    # Raw JD text extraction (#content, .content, .job-post-content, .job__description.body, or .job-post-container)
    content_el = soup.select_one("#content, .content, .job-post-content, .job__description.body, .job__description, .job-post-container")
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


def map_greenhouse_fields(
    html: str,
    profile: dict[str, Any],
    resume_path: str | None = None,
    cover_letter_text: str | None = None,
) -> FormMappingResult:
    """Pure, synchronous field mapping for Greenhouse forms.

    Maps candidate profile fields to standard form selectors.
    Custom/screening questions are NEVER auto-answered; they are left blank
    and recorded in unanswered_questions.
    """
    soup = BeautifulSoup(html, "html.parser")
    field_map: dict[str, dict[str, Any]] = {}
    unanswered_questions: list[str] = []

    # Check CAPTCHA
    captcha_elements = soup.select("iframe[src*='recaptcha'], iframe[src*='hcaptcha'], .g-recaptcha, .h-captcha")
    captcha_detected = len(captcha_elements) > 0

    full_name = profile.get("full_name", "")
    name_parts = full_name.split()
    first_name = name_parts[0] if name_parts else ""
    last_name = name_parts[-1] if len(name_parts) > 1 else ""
    email = profile.get("email", "")
    phone = profile.get("phone", "")
    links = profile.get("links", {})
    if isinstance(links, str):
        links = json.loads(links) if links else {}
    elif not isinstance(links, dict):
        links = {}

    # Helper to check if required
    def is_required(el: Any) -> bool:
        return el.get("aria-required") == "true" or el.has_attr("required") or "aria-required" in el.attrs

    # Map standard fields
    # First Name
    if fn_el := soup.select_one("#first_name, input[name*='first_name']"):
        selector = f"#{fn_el.get('id')}" if fn_el.get("id") else "input[name*='first_name']"
        field_map[selector] = {"value": first_name, "type": "text", "required": is_required(fn_el), "status": "filled"}

    # Last Name
    if ln_el := soup.select_one("#last_name, input[name*='last_name']"):
        selector = f"#{ln_el.get('id')}" if ln_el.get("id") else "input[name*='last_name']"
        field_map[selector] = {"value": last_name, "type": "text", "required": is_required(ln_el), "status": "filled"}

    # Email
    if em_el := soup.select_one("#email, input[name*='email']"):
        selector = f"#{em_el.get('id')}" if em_el.get("id") else "input[name*='email']"
        field_map[selector] = {"value": email, "type": "text", "required": is_required(em_el), "status": "filled"}

    # Phone
    if ph_el := soup.select_one("#phone, input[name*='phone']"):
        selector = f"#{ph_el.get('id')}" if ph_el.get("id") else "input[name*='phone']"
        field_map[selector] = {"value": phone, "type": "text", "required": is_required(ph_el), "status": "filled"}

    # Resume File
    if res_el := soup.select_one("#resume, input[type='file'][name*='resume']"):
        selector = f"#{res_el.get('id')}" if res_el.get("id") else "input[type='file'][name*='resume']"
        field_map[selector] = {"value": resume_path or "", "type": "file", "required": is_required(res_el), "status": "uploaded" if resume_path else "missing"}

    # Cover Letter Text
    if cl_el := soup.select_one("#cover_letter_text, textarea[name*='cover_letter']"):
        selector = f"#{cl_el.get('id')}" if cl_el.get("id") else "textarea[name*='cover_letter']"
        field_map[selector] = {"value": cover_letter_text or "", "type": "textarea", "required": is_required(cl_el), "status": "filled" if cover_letter_text else "empty"}

    # LinkedIn / Website / GitHub links
    if li_el := soup.select_one("input[name*='linkedin'], input[id*='linkedin']"):
        selector = f"#{li_el.get('id')}" if li_el.get("id") else "input[name*='linkedin']"
        field_map[selector] = {"value": links.get("linkedin", ""), "type": "text", "required": is_required(li_el), "status": "filled"}

    if gh_el := soup.select_one("input[name*='github'], input[id*='github']"):
        selector = f"#{gh_el.get('id')}" if gh_el.get("id") else "input[name*='github']"
        field_map[selector] = {"value": links.get("github", ""), "type": "text", "required": is_required(gh_el), "status": "filled"}

    # Custom screening questions (never auto-answer!)
    standard_ids = {"first_name", "last_name", "email", "phone", "resume", "cover_letter_text", "linkedin", "github"}
    form_inputs = soup.select("form fieldset .field, form .field, form div.field")

    missing_required_fields = False

    for field_div in form_inputs:
        input_el = field_div.select_one("input, textarea, select")
        if not input_el:
            continue

        el_id = input_el.get("id", "")
        el_name = input_el.get("name", "")

        if any(std in el_id or std in el_name for std in standard_ids):
            continue

        # Extract label text for custom screening question
        label_el = field_div.select_one("label")
        label_text = label_el.get_text(strip=True) if label_el else el_name or el_id

        if label_text and label_text not in unanswered_questions:
            unanswered_questions.append(label_text)
            if is_required(input_el):
                missing_required_fields = True

    return FormMappingResult(
        field_map=field_map,
        unanswered_questions=unanswered_questions,
        captcha_detected=captcha_detected,
        missing_required_fields=missing_required_fields,
    )


async def prefill_greenhouse_form(
    page_or_url: str,
    profile: dict[str, Any],
    resume_path: str,
    cover_letter_text: str | None = None,
    screenshot_dir: str = "artifacts/screenshots",
) -> dict[str, Any]:
    """Execute Playwright browser pre-fill on a Greenhouse form.

    STOPS BEFORE SUBMIT BUTTON. NEVER CLICKS SUBMIT.
    Captures screenshot and returns field map & unanswered questions.
    """
    Path(screenshot_dir).mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        if page_or_url.startswith("http://") or page_or_url.startswith("https://") or page_or_url.startswith("file://"):
            await page.goto(page_or_url)
        else:
            await page.set_content(page_or_url)

        html = await page.content()
        mapping = map_greenhouse_fields(html, profile, resume_path, cover_letter_text)

        # Fill mapped fields
        for selector, info in mapping.field_map.items():
            val = info["value"]
            if not val:
                continue

            try:
                if info["type"] == "file":
                    file_input = page.locator(selector)
                    if await file_input.count() > 0:
                        await file_input.set_input_files(val)
                elif info["type"] == "textarea":
                    ta = page.locator(selector)
                    if await ta.count() > 0:
                        await ta.fill(val)
                else:
                    inp = page.locator(selector)
                    if await inp.count() > 0:
                        await inp.fill(val)
            except Exception as exc:
                logger.warning("Failed to fill selector %s: %s", selector, exc)

        # Capture screenshot (STOP BEFORE SUBMIT)
        screenshot_filename = f"prefill_{hash(page_or_url) & 0xFFFFFFFF}.png"
        screenshot_path = str(Path(screenshot_dir) / screenshot_filename)
        await page.screenshot(path=screenshot_path, full_page=True)
        await browser.close()

        return {
            "screenshot_path": screenshot_path,
            "field_map": mapping.field_map,
            "unanswered_questions": mapping.unanswered_questions,
            "manual_completion_required": mapping.captcha_detected or mapping.missing_required_fields or len(mapping.unanswered_questions) > 0,
            "captcha_detected": mapping.captcha_detected,
        }


async def submit_greenhouse_form(page_or_url: str) -> bool:
    """Execute Playwright submit button click on an approved Greenhouse form.

    ONLY called when application status is 'approved'.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        if page_or_url.startswith("http://") or page_or_url.startswith("https://") or page_or_url.startswith("file://"):
            await page.goto(page_or_url)
        else:
            await page.set_content(page_or_url)

        submit_btn = page.locator("input[type='submit'], button[type='submit'], #submit_app")
        if await submit_btn.count() == 0:
            await browser.close()
            raise Exception("Submit button not found on Greenhouse page.")

        await submit_btn.first.click()
        await page.wait_for_load_state("networkidle", timeout=10000)
        await browser.close()
        return True


async def fetch_page(
    url: str,
    *,
    client_url: str | None = None,
    settings: Settings | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> str:
    """Fetch a rendered page via the Playwright service HTTP endpoint."""
    base_url = client_url or (settings.playwright_service_url if settings else Settings.load().playwright_service_url)

    async with httpx.AsyncClient(
        base_url=base_url,
        timeout=timeout,
    ) as client:
        response = await client.post(
            "/render",
            json={"url": url},
        )
        response.raise_for_status()
        return response.json()["html"]


fetch_greenhouse_job_page = fetch_page
