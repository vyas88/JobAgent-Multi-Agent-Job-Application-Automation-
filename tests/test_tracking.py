"""Unit tests for Phase 4 — Tracking and Scheduling Agent.

These tests use AsyncMock for DB pools and MockCalendarClient for calendar calls.
No real Google API or live DB calls are executed in unit tests.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.tracking import (
    CalendarServiceError,
    DatabaseError,
    OrphanedCalendarEventError,
    schedule_interview,
    update_status,
)
from src.services.calendar_service import MockCalendarClient


def _make_mock_pool_and_conn() -> tuple[MagicMock, AsyncMock]:
    """Helper to build a mock asyncpg pool and conn with async transaction support."""
    mock_pool = MagicMock()
    mock_conn = AsyncMock()
    mock_conn.transaction = MagicMock()
    mock_tx = AsyncMock()
    mock_conn.transaction.return_value = mock_tx
    mock_tx.__aenter__.return_value = mock_tx
    mock_tx.__aexit__.return_value = None

    mock_pool_cm = AsyncMock()
    mock_pool_cm.__aenter__.return_value = mock_conn
    mock_pool_cm.__aexit__.return_value = None
    mock_pool.acquire.return_value = mock_pool_cm

    return mock_pool, mock_conn


# --- 1. update_status Unit Tests -----------------------------------------

class TestUpdateStatus:
    """Verify update_status validation, atomicity, and no-op behavior."""

    @pytest.mark.asyncio
    async def test_update_status_atomic_write(self) -> None:
        """update_status must update applications.status and insert status_history atomically."""
        mock_pool, mock_conn = _make_mock_pool_and_conn()

        # 1. SELECT status returns 'submitted'
        # 2. UPDATE applications returns updated app dict
        mock_conn.fetchrow.side_effect = [
            {"status": "submitted"},
            {
                "id": "app-123",
                "job_id": "job-1",
                "status": "in_review",
                "review_artifact": None,
                "created_at": "now",
                "updated_at": "now",
            },
        ]

        res = await update_status("app-123", "in_review", pool=mock_pool, reason="Recruiter email")

        assert res["status"] == "in_review"
        # Verify status_history INSERT was executed
        assert mock_conn.execute.called
        audit_call = mock_conn.execute.call_args_list[0]
        assert "INSERT INTO status_history" in audit_call[0][0]

    @pytest.mark.asyncio
    async def test_update_status_uses_passed_conn(self) -> None:
        """update_status must reuse passed conn without acquiring from pool."""
        mock_pool, mock_conn = _make_mock_pool_and_conn()

        mock_conn.fetchrow.side_effect = [
            {"status": "submitted"},
            {"id": "app-123", "status": "in_review"},
        ]

        res = await update_status("app-123", "in_review", pool=mock_pool, conn=mock_conn)

        assert res["status"] == "in_review"
        # pool.acquire MUST NOT be called when conn is provided
        assert not mock_pool.acquire.called

    @pytest.mark.asyncio
    async def test_update_status_no_op_when_old_equals_new(self) -> None:
        """update_status must return early without UPDATE or audit insert when old_status == new_status."""
        mock_pool, mock_conn = _make_mock_pool_and_conn()

        # 1. SELECT status returns 'submitted'
        # 2. SELECT full app row
        mock_conn.fetchrow.side_effect = [
            {"status": "submitted"},
            {"id": "app-123", "status": "submitted"},
        ]

        res = await update_status("app-123", "submitted", pool=mock_pool)

        assert res["status"] == "submitted"
        # UPDATE / INSERT status_history MUST NOT be executed
        assert not mock_conn.execute.called

    @pytest.mark.asyncio
    async def test_update_status_rejects_invalid_enum(self) -> None:
        """update_status must raise ValueError for invalid status string."""
        mock_pool = MagicMock()

        with pytest.raises(ValueError, match="Invalid application status: 'invalid_status'"):
            await update_status("app-123", "invalid_status", pool=mock_pool)


# --- 2. schedule_interview Unit Tests -----------------------------------

class TestScheduleInterview:
    """Verify schedule_interview orchestration, calendar calls, and failure rollbacks."""

    @pytest.mark.asyncio
    async def test_schedule_interview_happy_path(self) -> None:
        """schedule_interview creates calendar event, inserts interviews row, and sets status='interview'."""
        mock_pool, mock_conn = _make_mock_pool_and_conn()

        scheduled_time = datetime(2026, 8, 10, 14, 0, tzinfo=timezone.utc)
        cal_client = MockCalendarClient(event_id="evt_test_999")

        # Mock DB calls:
        # 1. FETCH app title/company
        # 2. INSERT interviews RETURNING row
        # 3. SELECT FOR UPDATE status in update_status
        # 4. UPDATE applications in update_status
        mock_conn.fetchrow.side_effect = [
            {"id": "app-123", "title": "Senior Engineer", "company": "Acme Corp"},
            {"id": "int-123", "application_id": "app-123", "scheduled_at": scheduled_time, "calendar_event_id": "evt_test_999", "notes": "Screen"},
            {"status": "submitted"},
            {"id": "app-123", "status": "interview"},
        ]

        res = await schedule_interview(
            application_id="app-123",
            scheduled_at=scheduled_time,
            pool=mock_pool,
            calendar_client=cal_client,
            notes="Screen",
        )

        assert res["interview"]["calendar_event_id"] == "evt_test_999"
        assert res["application"]["status"] == "interview"
        assert len(cal_client.created_events) == 1

    @pytest.mark.asyncio
    async def test_schedule_interview_calendar_failure_no_db_row(self) -> None:
        """If calendar event creation fails, 0 DB writes occur and CalendarServiceError is raised."""
        mock_pool, mock_conn = _make_mock_pool_and_conn()

        mock_conn.fetchrow.return_value = {"id": "app-123", "title": "Engineer", "company": "Acme"}
        cal_client = MockCalendarClient(should_fail=True)

        scheduled_time = datetime(2026, 8, 10, 14, 0, tzinfo=timezone.utc)

        with pytest.raises(CalendarServiceError, match="Google Calendar API connection error"):
            await schedule_interview(
                application_id="app-123",
                scheduled_at=scheduled_time,
                pool=mock_pool,
                calendar_client=cal_client,
            )

        # Confirm INSERT interviews was never called
        assert not mock_conn.execute.called

    @pytest.mark.asyncio
    async def test_schedule_interview_db_failure_compensation_success(self) -> None:
        """If DB transaction fails after calendar event creation, calendar event is deleted via delete_event."""
        mock_pool, mock_conn = _make_mock_pool_and_conn()

        cal_client = MockCalendarClient(event_id="evt_to_delete_123")
        scheduled_time = datetime(2026, 8, 10, 14, 0, tzinfo=timezone.utc)

        # 1. App info fetch succeeds
        # 2. INSERT interviews DB query throws exception
        mock_conn.fetchrow.side_effect = [
            {"id": "app-123", "title": "Engineer", "company": "Acme"},
            RuntimeError("Postgres FK violation error"),
        ]

        with pytest.raises(DatabaseError, match="successfully rolled back"):
            await schedule_interview(
                application_id="app-123",
                scheduled_at=scheduled_time,
                pool=mock_pool,
                calendar_client=cal_client,
            )

        # Verify compensation delete was executed for the calendar event
        assert "evt_to_delete_123" in cal_client.deleted_events

    @pytest.mark.asyncio
    async def test_schedule_interview_compensation_delete_failure_raises_orphaned_error(self) -> None:
        """If DB fails AND compensation delete fails, OrphanedCalendarEventError is raised surfacing event_id."""
        mock_pool, mock_conn = _make_mock_pool_and_conn()

        cal_client = MockCalendarClient(event_id="evt_orphaned_777", delete_should_fail=True)
        scheduled_time = datetime(2026, 8, 10, 14, 0, tzinfo=timezone.utc)

        # DB throws exception
        mock_conn.fetchrow.side_effect = [
            {"id": "app-123", "title": "Engineer", "company": "Acme"},
            RuntimeError("DB write connection dropped"),
        ]

        with pytest.raises(OrphanedCalendarEventError, match="evt_orphaned_777"):
            await schedule_interview(
                application_id="app-123",
                scheduled_at=scheduled_time,
                pool=mock_pool,
                calendar_client=cal_client,
            )

    @pytest.mark.asyncio
    async def test_schedule_interview_status_update_failure_rolls_back_interviews(self) -> None:
        """If update_status fails inside schedule_interview transaction, the interviews row rolls back together."""
        mock_pool, mock_conn = _make_mock_pool_and_conn()

        cal_client = MockCalendarClient(event_id="evt_rollback_555")
        scheduled_time = datetime(2026, 8, 10, 14, 0, tzinfo=timezone.utc)

        # 1. Fetch app title
        # 2. INSERT interviews succeeds
        # 3. SELECT status in update_status returns None (Application not found) -> raises ValueError
        mock_conn.fetchrow.side_effect = [
            {"id": "app-123", "title": "Engineer", "company": "Acme"},
            {"id": "int-123", "calendar_event_id": "evt_rollback_555"},
            None,  # App not found inside update_status
        ]

        with pytest.raises(DatabaseError):
            await schedule_interview(
                application_id="app-123",
                scheduled_at=scheduled_time,
                pool=mock_pool,
                calendar_client=cal_client,
            )

        # Compensation delete was triggered
        assert "evt_rollback_555" in cal_client.deleted_events
