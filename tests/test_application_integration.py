"""Integration tests for Phase 3 — Application Agent (Playwright & Neon DB).

These tests run against a real headless Playwright browser and live Neon DB.
They are marked with @pytest.mark.integration and SKIPPED by default in CI/unit runs.

To run:
    pytest -m integration
"""

from __future__ import annotations

import json
from pathlib import Path

import asyncpg
import pytest

from src.agents.application import (
    prefill_application,
    render_resume_to_file,
    submit_application,
)
from src.config import Settings
from src.services.playwright_service import prefill_greenhouse_form, submit_greenhouse_form


@pytest.mark.integration
@pytest.mark.asyncio
async def test_playwright_greenhouse_prefill_fixture(tmp_path: Path) -> None:
    """Real Playwright headless browser pre-fills fixture form and STOPS before submit."""
    fixture_path = Path("fixtures/greenhouse_job_page.html").resolve()
    file_url = f"file://{fixture_path}"

    profile = json.loads(Path("fixtures/master_profile.json").read_text(encoding="utf-8"))
    resume_file = tmp_path / "resume.txt"
    resume_file.write_text("Test resume content")

    res = await prefill_greenhouse_form(
        page_or_url=file_url,
        profile=profile,
        resume_path=str(resume_file),
        cover_letter_text="Dear Hiring Team...",
        screenshot_dir=str(tmp_path / "screenshots"),
    )

    assert "screenshot_path" in res
    assert Path(res["screenshot_path"]).exists()
    assert res["field_map"]["#first_name"]["value"] == "Alex"
    assert res["field_map"]["#email"]["value"] == "alex.johnson@example.com"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_playwright_submit_application_live_db(tmp_path: Path) -> None:
    """Full application lifecycle against live Neon DB and real headless browser."""
    settings = Settings.load()
    fixture_path = Path("fixtures/greenhouse_job_page.html").resolve()
    file_url = f"file://{fixture_path}"

    profile_data = json.loads(Path("fixtures/master_profile.json").read_text(encoding="utf-8"))
    pool = await asyncpg.create_pool(settings.database_url)

    test_source_url = f"https://boards.greenhouse.io/testco/jobs/{hash(file_url) & 0xFFFFFF}"

    try:
        async with pool.acquire() as conn:
            # 1. Insert Profile
            prof_row = await conn.fetchrow(
                """
                INSERT INTO profiles (full_name, email, phone, links)
                VALUES ($1, $2, $3, $4::jsonb)
                RETURNING id;
                """,
                profile_data["full_name"],
                profile_data["email"],
                profile_data["phone"],
                json.dumps(profile_data["links"]),
            )
            profile_id = prof_row["id"]

            # 2. Insert Job
            job_row = await conn.fetchrow(
                """
                INSERT INTO jobs (source_url, platform, title, company, status)
                VALUES ($1, 'greenhouse'::platform, 'Software Engineer', 'Acme Corp', 'qualified'::job_status)
                RETURNING id;
                """,
                test_source_url,
            )
            job_id = job_row["id"]

            # 3. Insert Resume Variant
            res_file = render_resume_to_file("integration-res-1", {"summary": "Eng summary"}, output_dir=str(tmp_path))
            res_row = await conn.fetchrow(
                """
                INSERT INTO resume_variants (job_id, profile_id, content, file_path)
                VALUES ($1::uuid, $2::uuid, $3::jsonb, $4)
                RETURNING id;
                """,
                job_id,
                profile_id,
                json.dumps({"summary": "Eng summary"}),
                res_file,
            )
            resume_variant_id = res_row["id"]

        # 4. Prefill Application
        app_data = await prefill_application(
            job_id=str(job_id),
            profile_id=str(profile_id),
            resume_variant_id=str(resume_variant_id),
            cover_letter_id=None,
            profile=profile_data,
            job={"source_url": test_source_url},
            resume_variant={"content": {"summary": "Eng summary"}, "file_path": res_file},
            pool=pool,
            page_or_url=file_url,
            skip_browser=False,
        )
        app_id = app_data["id"]

        # 5. Move status to 'approved' via DB update (simulating human approval)
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE applications SET status = 'approved'::application_status WHERE id = $1::uuid;",
                app_id,
            )

        # 6. Execute Approval-Gated Submission
        sub_res = await submit_application(
            application_id=str(app_id),
            pool=pool,
            page_or_url=file_url,
            skip_browser=False,
        )

        assert sub_res["status"] == "submitted"

        # Verify DB state
        async with pool.acquire() as conn:
            check_row = await conn.fetchrow("SELECT status, submitted_at FROM applications WHERE id = $1::uuid;", app_id)
            assert check_row["status"] == "submitted"
            assert check_row["submitted_at"] is not None

    finally:
        # Clean up test rows
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM status_history WHERE application_id IN (SELECT id FROM applications WHERE job_id IN (SELECT id FROM jobs WHERE source_url = $1));", test_source_url)
            await conn.execute("DELETE FROM applications WHERE job_id IN (SELECT id FROM jobs WHERE source_url = $1);", test_source_url)
            await conn.execute("DELETE FROM resume_variants WHERE job_id IN (SELECT id FROM jobs WHERE source_url = $1);", test_source_url)
            await conn.execute("DELETE FROM jobs WHERE source_url = $1;", test_source_url)
            await conn.execute("DELETE FROM profiles WHERE email = $1;", profile_data["email"])

        await pool.close()
