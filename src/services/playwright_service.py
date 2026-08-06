"""Playwright service HTTP client, HTML parsers, and Greenhouse form automation.

The Playwright browser automation runs in a standalone containerized
service (e.g., FastAPI + Playwright) or directly via Playwright in Python.
This module provides HTML parsing, form mapping, and Playwright browser actions.
"""

from __future__ import annotations

import json
import re
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
    mapped_ids: set[str] = set()

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
        try:
            links = json.loads(links) if links else {}
        except Exception:
            links = {}
    elif not isinstance(links, dict):
        links = {}

    def is_required(el: Any, wrapper: Any = None) -> bool:
        if el.get("aria-required") == "true" or el.has_attr("required") or "aria-required" in el.attrs:
            return True
        if wrapper:
            if wrapper.select_one("[aria-required='true'], [required]"):
                return True
            lbl = wrapper.select_one("label, .label")
            if lbl and ("*" in lbl.get_text() or "required" in lbl.get_text().lower()):
                return True
            for star in wrapper.select("span, font, p, div"):
                if "*" in star.get_text():
                    return True
        return False

    def get_clean_label(el: Any, wrapper: Any = None) -> str:
        el_id = el.get("id", "")
        label_text = ""
        if el_id:
            lbl = soup.select_one(f'label[for="{el_id}"], [id="{el_id}-label"]')
            if lbl:
                label_text = lbl.get_text(strip=True)
        if not label_text and wrapper:
            lbl = wrapper.select_one("label, .label, legend")
            if lbl:
                label_text = lbl.get_text(strip=True)
        if not label_text:
            label_text = el.get("aria-label") or el.get("placeholder") or ""

        label_text = re.sub(r"\s*\*\s*$", "", label_text)
        label_text = re.sub(r"indicates a required field", "", label_text, flags=re.IGNORECASE)
        return label_text.strip()

    missing_required_fields = False

    def mark_standard_field(el: Any, val: str, ftype: str) -> None:
        nonlocal missing_required_fields
        if not el:
            return
        el_id = el.get("id", "")
        el_name = el.get("name", "")
        selector = f"#{el_id}" if el_id else f"input[name='{el_name}']"
        req = is_required(el)
        status = "uploaded" if ftype == "file" and val else ("filled" if val else "empty")
        field_map[selector] = {"value": val, "type": ftype, "required": req, "status": status}
        if el_id:
            mapped_ids.add(el_id)
        if el_name:
            mapped_ids.add(el_name)
        if req and not val:
            missing_required_fields = True

    # Standard identity fields mapping
    if fn_el := soup.select_one("#first_name, input[name*='first_name'], input[id*='first_name']"):
        mark_standard_field(fn_el, first_name, "text")

    if ln_el := soup.select_one("#last_name, input[name*='last_name'], input[id*='last_name']"):
        mark_standard_field(ln_el, last_name, "text")

    if em_el := soup.select_one("#email, input[name*='email'], input[id*='email']"):
        mark_standard_field(em_el, email, "text")

    if ph_el := soup.select_one("#phone, input[name*='phone'], input[id*='phone']"):
        mark_standard_field(ph_el, phone, "text")

    if res_el := soup.select_one("#resume, input[type='file'][name*='resume']"):
        mark_standard_field(res_el, resume_path or "", "file")

    if cl_el := soup.select_one("#cover_letter_text, textarea[name*='cover_letter'], #cover_letter"):
        mark_standard_field(cl_el, cover_letter_text or "", "textarea" if cl_el.name == "textarea" else "file")

    if li_el := soup.select_one("input[name*='linkedin'], input[id*='linkedin']"):
        if links.get("linkedin"):
            mark_standard_field(li_el, links.get("linkedin", ""), "text")

    if gh_el := soup.select_one("input[name*='github'], input[id*='github']"):
        if links.get("github"):
            mark_standard_field(gh_el, links.get("github", ""), "text")

    if ws_el := soup.select_one("input[name*='website'], input[id*='website']"):
        if links.get("website"):
            mark_standard_field(ws_el, links.get("website", ""), "text")

    # Custom screening questions and remaining required form inputs
    inputs = soup.select("form input, form textarea, form select")
    if not inputs:
        inputs = soup.select("input, textarea, select")

    seen_keys: set[str] = set()

    for el in inputs:
        el_id = el.get("id", "")
        el_name = el.get("name", "")
        el_type = el.get("type", el.name).lower()
        classes = el.get("class", [])

        if el_type in ("hidden", "submit", "button", "reset", "image"):
            continue
        # Skip internal styling or dummy inputs for react-select dropdowns
        if "requiredInput" in classes or ("select__input" in classes and not el_id):
            continue

        if el_id in mapped_ids or (el_name and el_name in mapped_ids):
            continue

        wrapper = el.find_parent(class_=lambda c: c and any(k in c for k in ["field", "wrapper", "select", "container", "input"]))
        lbl = get_clean_label(el, wrapper)

        if not el_id and not el_name and not lbl:
            continue

        key = el_id or el_name or lbl
        if key in seen_keys:
            continue
        seen_keys.add(key)

        req = is_required(el, wrapper)
        if req:
            missing_required_fields = True

        selector = f"#{el_id}" if el_id else (f"[name='{el_name}']" if el_name else el.name)
        q_str = f"{lbl} ({selector})" if lbl and selector not in lbl else (lbl or selector)
        unanswered_questions.append(q_str)

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
