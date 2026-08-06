"""Tracking and Scheduling Agent.

Responsibilities (ARCHITECTURE.md §2.4):
- Status updates + append-only status_history audit trail.
- Every status change goes through update_status so status_history is never bypassed.
- Atomically updates applications.status AND inserts status_history row in the same transaction.
- On confirmed interview: creates Google Calendar event, inserts interviews row, and moves status to 'interview'.
- Handles compensation rollback cleanly if DB transaction fails after calendar event creation.
"""

from __future__ import annotations

from datetime import datetime
import logging
from typing import Any

import asyncpg

from src.services.calendar_service import GoogleCalendarClientProtocol

logger = logging.getLogger(__name__)


class CalendarServiceError(Exception):
    """Raised when Google Calendar API fails during event creation."""


class DatabaseError(Exception):
    """Raised when DB transaction fails during interview scheduling."""


class OrphanedCalendarEventError(Exception):
    """Raised when DB transaction fails after Google Calendar event creation AND compensation delete also fails."""


VALID_APPLICATION_STATUSES: set[str] = {
    "draft",
    "pre_filled",
    "pending_review",
    "approved",
    "submitted",
    "submit_uncertain",
    "in_review",
    "interview",
    "offer",
    "rejected",
    "withdrawn",
}


async def _execute_status_update(
    conn: asyncpg.Connection,
    application_id: str,
    new_status: str,
    reason: str | None = None,
) -> dict[str, Any]:
    """Internal helper to execute status update and status_history insert on an active connection."""
    # 1. Lock row & read current status
    read_query = "SELECT status FROM applications WHERE id = $1::uuid FOR UPDATE;"
    row = await conn.fetchrow(read_query, application_id)
    if not row:
        raise ValueError(f"Application {application_id} not found.")

    old_status = row["status"]

    # 2. No-op if status unchanged
    if old_status == new_status:
        full_query = "SELECT id, job_id, resume_variant_id, cover_letter_id, status, review_artifact, approved_at, submitted_at, created_at, updated_at FROM applications WHERE id = $1::uuid;"
        app_row = await conn.fetchrow(full_query, application_id)
        return dict(app_row)

    # 3. Update applications status
    update_query = """
        UPDATE applications
        SET status = $2::application_status,
            updated_at = now()
        WHERE id = $1::uuid
        RETURNING id, job_id, resume_variant_id, cover_letter_id, status, review_artifact, approved_at, submitted_at, created_at, updated_at;
    """
    updated_app_row = await conn.fetchrow(update_query, application_id, new_status)

    # 4. Insert status_history audit record
    audit_query = """
        INSERT INTO status_history (application_id, old_status, new_status, reason)
        VALUES ($1::uuid, $2::application_status, $3::application_status, $4);
    """
    await conn.execute(audit_query, application_id, old_status, new_status, reason)

    return dict(updated_app_row)


async def update_status(
    application_id: str,
    new_status: str,
    pool: asyncpg.Pool,
    reason: str | None = None,
    conn: asyncpg.Connection | None = None,
) -> dict[str, Any]:
    """Atomically update applications.status and record an append-only status_history row.

    Validates new_status against VALID_APPLICATION_STATUSES enum set.
    If old_status == new_status, no-op.
    Reuses passed connection 'conn' if provided, ensuring transactional atomicity.
    """
    if new_status not in VALID_APPLICATION_STATUSES:
        raise ValueError(f"Invalid application status: '{new_status}'")

    if conn is not None:
        # Reuse existing transaction connection
        return await _execute_status_update(conn, application_id, new_status, reason)

    # Acquire new connection from pool and run in transaction block
    async with pool.acquire() as new_conn:
        async with new_conn.transaction():
            return await _execute_status_update(new_conn, application_id, new_status, reason)


async def schedule_interview(
    application_id: str,
    scheduled_at: datetime,
    pool: asyncpg.Pool,
    calendar_client: GoogleCalendarClientProtocol,
    notes: str | None = None,
) -> dict[str, Any]:
    """Schedule an interview: creates Google Calendar event, inserts interviews row, and moves status to 'interview'.

    Handles compensation rollback cleanly if DB transaction fails after calendar event creation.
    """
    # 1. Fetch job title and company for calendar event summary
    query_info = """
        SELECT a.id, j.title, j.company
        FROM applications a
        JOIN jobs j ON a.job_id = j.id
        WHERE a.id = $1::uuid;
    """
    async with pool.acquire() as conn:
        app_info = await conn.fetchrow(query_info, application_id)

    if not app_info:
        raise ValueError(f"Application {application_id} not found.")

    title = app_info["title"]
    company = app_info["company"]

    # 2. Step 1: Create Google Calendar Event (External API call outside DB transaction)
    try:
        calendar_event_id = await calendar_client.create_interview_event(
            title=title,
            company=company,
            scheduled_at=scheduled_at,
            description=notes,
        )
    except Exception as exc:
        logger.error("Failed to create Google Calendar event for app %s: %s", application_id, exc)
        raise CalendarServiceError(f"Failed to create Google Calendar event: {exc}") from exc

    # 3. Step 2: Single Transactional DB Block
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                # A. Insert interviews row
                interview_row = await conn.fetchrow(
                    """
                    INSERT INTO interviews (application_id, scheduled_at, calendar_event_id, notes)
                    VALUES ($1::uuid, $2, $3, $4)
                    RETURNING id, application_id, scheduled_at, calendar_event_id, notes, created_at;
                    """,
                    application_id,
                    scheduled_at,
                    calendar_event_id,
                    notes,
                )

                # B. Update status to 'interview' via update_status on SAME connection
                updated_app = await update_status(
                    application_id=application_id,
                    new_status="interview",
                    pool=pool,
                    reason="Interview scheduled",
                    conn=conn,  # MUST reuse same connection & transaction!
                )
    except Exception as db_exc:
        logger.error("DB transaction failed during schedule_interview for app %s: %s", application_id, db_exc)
        # Step 3: Attempt Compensation Delete
        try:
            await calendar_client.delete_event(calendar_event_id)
        except Exception as del_exc:
            logger.error(
                "CRITICAL: Failed to rollback Google Calendar event %s after DB failure: %s",
                calendar_event_id,
                del_exc,
            )
            raise OrphanedCalendarEventError(
                f"Database transaction failed after creating Google Calendar event '{calendar_event_id}'. "
                f"Compensation delete also failed ({del_exc}). Manual cleanup required for calendar event '{calendar_event_id}'."
            ) from db_exc

        raise DatabaseError(
            f"Database transaction failed for schedule_interview. Calendar event '{calendar_event_id}' was successfully rolled back: {db_exc}"
        ) from db_exc

    return {
        "interview": dict(interview_row),
        "application": updated_app,
    }
