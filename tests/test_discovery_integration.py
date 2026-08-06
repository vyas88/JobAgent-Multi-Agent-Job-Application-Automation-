"""Integration test for Phase 1 — Discovery & Analysis Agent against live Neon DB.

This test requires a valid DATABASE_URL in environment or .env.
It is marked with @pytest.mark.integration and is SKIPPED BY DEFAULT in CI and unit test runs.

Run explicitly with:
    pytest -m integration
"""

from __future__ import annotations

import json
import pytest

from src.agents.discovery import persist_job
from src.config import Settings
from src.db import create_pool


@pytest.mark.integration
@pytest.mark.asyncio
async def test_persist_job_live_neon_db() -> None:
    """Integration test verifying persist_job upsert and profile insertion against live Neon DB."""
    settings = Settings.load()
    pool = await create_pool(settings, min_size=1, max_size=2)

    test_source_url = "https://boards.greenhouse.io/integration-test-company/jobs/77777"
    test_profile_email = "integration.test.candidate@example.com"

    try:
        async with pool.acquire() as conn:
            # 1. Insert a test profile row
            profile_row = await conn.fetchrow(
                """
                INSERT INTO profiles (full_name, email, location, summary, experience, skills)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb)
                RETURNING id, full_name, email;
                """,
                "Integration Candidate",
                test_profile_email,
                "San Francisco, CA",
                "Senior Engineer Profile",
                json.dumps([{"title": "Senior Engineer", "company": "Test Co"}]),
                json.dumps(["Python", "PostgreSQL", "FastAPI"]),
            )
            assert profile_row is not None
            assert profile_row["email"] == test_profile_email
            profile_id = profile_row["id"]

            # 2. First persist_job call (Initial Insert)
            job_data_1 = {
                "source_url": test_source_url,
                "platform": "greenhouse",
                "title": "Lead Software Engineer",
                "company": "Integration Test Corp",
                "location": "San Francisco, CA (Hybrid)",
                "raw_jd": "Full job description text for integration testing...",
                "requirements": ["7+ years experience in Python", "Deep PostgreSQL knowledge"],
                "keywords": ["Python", "PostgreSQL", "FastAPI", "AsyncIO"],
                "fit_score": 88.50,
                "status": "qualified",
            }

            res1 = await persist_job(job_data_1, pool)
            assert res1["source_url"] == test_source_url
            assert res1["title"] == "Lead Software Engineer"

            # Verify readback from live DB
            readback = await conn.fetchrow("SELECT * FROM jobs WHERE source_url = $1;", test_source_url)
            assert readback is not None
            assert readback["title"] == "Lead Software Engineer"
            assert readback["company"] == "Integration Test Corp"
            assert float(readback["fit_score"]) == 88.50
            assert readback["status"] == "qualified"

            # 3. Second persist_job call with same source_url (Upsert Update)
            job_data_2 = {
                "source_url": test_source_url,  # Same UNIQUE key
                "platform": "greenhouse",
                "title": "Principal Software Engineer",  # Updated title
                "company": "Integration Test Corp",
                "location": "Remote",
                "raw_jd": "Updated job description text...",
                "requirements": ["10+ years experience in Python"],
                "keywords": ["Python", "PostgreSQL", "Architecture"],
                "fit_score": 95.00,  # Updated fit_score
                "status": "qualified",
            }

            res2 = await persist_job(job_data_2, pool)
            assert res2["title"] == "Principal Software Engineer"

            # Assert ON CONFLICT updated existing row rather than duplicating
            count_rows = await conn.fetchval("SELECT COUNT(*) FROM jobs WHERE source_url = $1;", test_source_url)
            assert count_rows == 1

            readback_updated = await conn.fetchrow("SELECT * FROM jobs WHERE source_url = $1;", test_source_url)
            assert readback_updated["title"] == "Principal Software Engineer"
            assert float(readback_updated["fit_score"]) == 95.00

    finally:
        # 4. Clean up test rows in a finally block
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM jobs WHERE source_url = $1;", test_source_url)
            await conn.execute("DELETE FROM profiles WHERE email = $1;", test_profile_email)

            # Confirm cleanup left DB empty of test rows
            remaining_jobs = await conn.fetchval("SELECT COUNT(*) FROM jobs WHERE source_url = $1;", test_source_url)
            remaining_profiles = await conn.fetchval("SELECT COUNT(*) FROM profiles WHERE email = $1;", test_profile_email)

            assert remaining_jobs == 0
            assert remaining_profiles == 0

        await pool.close()
