"""Integration tests for Phase 4 — Tracking and Scheduling Agent (Neon DB + MockCalendarClient).

These tests run against live Neon Postgres using DATABASE_URL and a MockCalendarClient.
No real Google API calls are made.
Marked with @pytest.mark.integration and SKIPPED by default in CI/unit runs.

To run:
    pytest -m integration
"""

from __future__ import annotations

from datetime import datetime, timezone
import json

from uuid import uuid4

import asyncpg
import pytest

from src.agents.tracking import schedule_interview, update_status
from src.config import Settings
from src.services.calendar_service import MockCalendarClient


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tracking_live_neon_db_status_update_and_interview() -> None:
    """Full lifecycle status update and interview scheduling against live Neon DB."""
    settings = Settings.load()
    pool = await asyncpg.create_pool(settings.database_url)

    test_url = f"https://boards.greenhouse.io/testco/jobs/{uuid4()}"
    cal_client = MockCalendarClient(event_id="evt_neon_integration_123")
    scheduled_time = datetime(2026, 8, 15, 10, 0, tzinfo=timezone.utc)

    profile_email = f"tracking.test.{uuid4()}@example.com"

    profile_id = None
    job_id = None
    app_id = None

    try:
        async with pool.acquire() as conn:
            # 1. Insert Profile
            prof_row = await conn.fetchrow(
                """
                INSERT INTO profiles (full_name, email)
                VALUES ('Tracking Test User', $1)
                RETURNING id;
                """,
                profile_email,
            )
            profile_id = prof_row["id"]

            # 2. Insert Job
            job_row = await conn.fetchrow(
                """
                INSERT INTO jobs (source_url, platform, title, company, status)
                VALUES ($1, 'greenhouse'::platform, 'Lead Backend Engineer', 'TrackingCorp', 'qualified'::job_status)
                RETURNING id;
                """,
                test_url,
            )
            job_id = job_row["id"]

            # 3. Insert Application in 'submitted' state
            app_row = await conn.fetchrow(
                """
                INSERT INTO applications (job_id, status)
                VALUES ($1::uuid, 'submitted'::application_status)
                RETURNING id;
                """,
                job_id,
            )
            app_id = str(app_row["id"])

        # 4. Test update_status ('submitted' -> 'in_review')
        up_res = await update_status(
            application_id=app_id,
            new_status="in_review",
            pool=pool,
            reason="Recruiter outreach email",
        )
        assert up_res["status"] == "in_review"

        # Verify status_history row in Neon DB
        async with pool.acquire() as conn:
            hist_rows = await conn.fetch(
                "SELECT old_status, new_status, reason FROM status_history WHERE application_id = $1::uuid ORDER BY changed_at ASC;",
                app_id,
            )
            assert len(hist_rows) == 1
            assert hist_rows[0]["old_status"] == "submitted"
            assert hist_rows[0]["new_status"] == "in_review"
            assert hist_rows[0]["reason"] == "Recruiter outreach email"

        # 5. Test schedule_interview ('in_review' -> 'interview')
        sched_res = await schedule_interview(
            application_id=app_id,
            scheduled_at=scheduled_time,
            pool=pool,
            calendar_client=cal_client,
            notes="Technical deep dive interview",
        )

        assert sched_res["application"]["status"] == "interview"
        assert sched_res["interview"]["calendar_event_id"] == "evt_neon_integration_123"

        # Verify interviews table and second status_history row in Neon DB
        async with pool.acquire() as conn:
            int_row = await conn.fetchrow(
                "SELECT application_id, calendar_event_id, notes FROM interviews WHERE application_id = $1::uuid;",
                app_id,
            )
            assert int_row is not None
            assert int_row["calendar_event_id"] == "evt_neon_integration_123"
            assert int_row["notes"] == "Technical deep dive interview"

            hist_rows_after = await conn.fetch(
                "SELECT old_status, new_status, reason FROM status_history WHERE application_id = $1::uuid ORDER BY changed_at ASC;",
                app_id,
            )
            assert len(hist_rows_after) == 2
            assert hist_rows_after[1]["old_status"] == "in_review"
            assert hist_rows_after[1]["new_status"] == "interview"
            assert hist_rows_after[1]["reason"] == "Interview scheduled"

    finally:
        # Clean up ONLY test rows created by this specific test run using exact UUIDs
        async with pool.acquire() as conn:
            if app_id:
                await conn.execute("DELETE FROM interviews WHERE application_id = $1::uuid;", app_id)
                await conn.execute("DELETE FROM status_history WHERE application_id = $1::uuid;", app_id)
                await conn.execute("DELETE FROM applications WHERE id = $1::uuid;", app_id)
            if job_id:
                await conn.execute("DELETE FROM jobs WHERE id = $1::uuid;", job_id)
            if profile_id:
                await conn.execute("DELETE FROM profiles WHERE id = $1::uuid;", profile_id)

        await pool.close()
