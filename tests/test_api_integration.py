"""Integration tests for Phase 5a — FastAPI Pipeline Layer (Neon DB + MockCalendarClient).

Runs full end-to-end HTTP pipeline requests against live Neon Postgres.
No real OpenAI, Google Calendar, or Playwright browser instances are invoked in automated runs.

Marked with @pytest.mark.integration and SKIPPED by default in CI/unit runs.

To run:
    pytest -m integration
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import asyncpg
import httpx
import pytest

from src.agents.application import render_resume_to_file
from src.api.dependencies import get_calendar_client, get_db_pool, get_settings
from src.api.main import app
from src.config import Settings
from src.services.calendar_service import MockCalendarClient

TEST_HEADERS = {"X-API-Key": "dev-api-key"}


@pytest.mark.integration
@pytest.mark.asyncio
async def test_api_full_pipeline_end_to_end_live_db(tmp_path: Path) -> None:
    """End-to-end API pipeline test against live Neon DB and MockCalendarClient."""
    settings = Settings.load()
    pool = await asyncpg.create_pool(settings.database_url)
    cal_client = MockCalendarClient(event_id="evt_api_integration_777")
    fixture_path = Path("fixtures/greenhouse_job_page.html").resolve()
    file_url = f"file://{fixture_path}"

    app.dependency_overrides[get_db_pool] = lambda: pool
    app.dependency_overrides[get_calendar_client] = lambda: cal_client
    mock_settings = Settings(
        database_url=settings.database_url,
        openai_api_key=settings.openai_api_key,
        api_key="dev-api-key",
    )
    app.dependency_overrides[get_settings] = lambda: mock_settings

    test_source_url = f"https://boards.greenhouse.io/testco/jobs/{hash(file_url) & 0xFFFFFF}"
    profile_data = json.loads(Path("fixtures/master_profile.json").read_text(encoding="utf-8"))

    try:
        # Seed Profile & Job directly in Neon DB (bypasses real OpenAI & external Playwright container)
        async with pool.acquire() as conn:
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
            profile_id = str(prof_row["id"])

            job_row = await conn.fetchrow(
                """
                INSERT INTO jobs (source_url, platform, title, company, status)
                VALUES ($1, 'greenhouse'::platform, 'Lead API Engineer', 'PipelineCorp', 'qualified'::job_status)
                RETURNING id;
                """,
                test_source_url,
            )
            job_id = str(job_row["id"])

            res_file = render_resume_to_file("api-res-1", {"summary": "Eng summary"}, output_dir=str(tmp_path))
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
            resume_variant_id = str(res_row["id"])

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. POST /applications/prefill
            prefill_res = await client.post(
                "/applications/prefill",
                json={
                    "job_id": job_id,
                    "profile_id": profile_id,
                    "resume_variant_id": resume_variant_id,
                    "cover_letter_id": None,
                    "page_or_url": file_url,
                },
                headers=TEST_HEADERS,
            )
            assert prefill_res.status_code == 200
            app_id = prefill_res.json()["id"]
            assert prefill_res.json()["status"] == "pending_review"

            # 2. POST /applications/{id}/submit -> EXPECT 409 CONFLICT (not approved yet)
            sub_refuse = await client.post(
                f"/applications/{app_id}/submit",
                json={"page_or_url": file_url},
                headers=TEST_HEADERS,
            )
            assert sub_refuse.status_code == 409
            assert sub_refuse.json()["error_type"] == "approval_refused"

            # 3. POST /applications/{id}/status new_status='approved' -> EXPECT 409 CONFLICT (forbidden transition)
            status_refuse = await client.post(
                f"/applications/{app_id}/status",
                json={"new_status": "approved"},
                headers=TEST_HEADERS,
            )
            assert status_refuse.status_code == 409
            assert status_refuse.json()["error_type"] == "forbidden_status_transition"

            # 4. POST /applications/{id}/approve -> EXPECT 200 (pending_review -> approved)
            approve_res = await client.post(
                f"/applications/{app_id}/approve",
                json={"reason": "Approved by reviewer"},
                headers=TEST_HEADERS,
            )
            assert approve_res.status_code == 200
            assert approve_res.json()["status"] == "approved"

            # 5. POST /applications/{id}/submit -> EXPECT 200 (approved -> submitted)
            submit_res = await client.post(
                f"/applications/{app_id}/submit",
                json={"page_or_url": file_url},
                headers=TEST_HEADERS,
            )
            assert submit_res.status_code == 200
            assert submit_res.json()["status"] == "submitted"

            # 6. POST /applications/{id}/status new_status='in_review' -> EXPECT 200
            in_review_res = await client.post(
                f"/applications/{app_id}/status",
                json={"new_status": "in_review", "reason": "Recruiter response"},
                headers=TEST_HEADERS,
            )
            assert in_review_res.status_code == 200
            assert in_review_res.json()["status"] == "in_review"

            # 7. POST /applications/{id}/interview -> EXPECT 200
            sched_time = datetime(2026, 8, 20, 15, 0, tzinfo=timezone.utc).isoformat()
            interview_res = await client.post(
                f"/applications/{app_id}/interview",
                json={"scheduled_at": sched_time, "notes": "Onsite interview"},
                headers=TEST_HEADERS,
            )
            assert interview_res.status_code == 200
            assert interview_res.json()["application"]["status"] == "interview"

            # 8. GET /applications/{id} -> EXPECT 200 with full history & interviews
            get_res = await client.get(f"/applications/{app_id}")
            assert get_res.status_code == 200
            assert get_res.json()["status"] == "interview"
            assert len(get_res.json()["status_history"]) >= 3
            assert len(get_res.json()["interviews"]) == 1

            # 9. GET /jobs/{id} -> EXPECT 200
            get_job_res = await client.get(f"/jobs/{job_id}")
            assert get_job_res.status_code == 200
            assert get_job_res.json()["title"] == "Lead API Engineer"

    finally:
        # Clean up test rows in Neon DB
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM interviews WHERE application_id IN (SELECT id FROM applications WHERE job_id IN (SELECT id FROM jobs WHERE source_url = $1));", test_source_url)
            await conn.execute("DELETE FROM status_history WHERE application_id IN (SELECT id FROM applications WHERE job_id IN (SELECT id FROM jobs WHERE source_url = $1));", test_source_url)
            await conn.execute("DELETE FROM applications WHERE job_id IN (SELECT id FROM jobs WHERE source_url = $1);", test_source_url)
            await conn.execute("DELETE FROM resume_variants WHERE job_id IN (SELECT id FROM jobs WHERE source_url = $1);", test_source_url)
            await conn.execute("DELETE FROM jobs WHERE source_url = $1;", test_source_url)
            await conn.execute("DELETE FROM profiles WHERE email = $1;", profile_data["email"])

        await pool.close()
        app.dependency_overrides.clear()
