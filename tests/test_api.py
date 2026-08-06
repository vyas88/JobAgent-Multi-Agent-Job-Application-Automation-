"""API unit tests using FastAPI TestClient with mocked agent functions and DB pool.

No real database, OpenAI, Google Calendar, or Playwright browser instances are invoked in unit tests.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.agents.application import ApprovalError, FormFillError
from src.agents.content import FabricationError
from src.agents.tracking import OrphanedCalendarEventError
from src.api.dependencies import get_calendar_client, get_db_pool
from src.api.main import app
from src.exceptions import NotFoundError
from src.services.calendar_service import MockCalendarClient

TEST_HEADERS = {"X-API-Key": "dev-api-key"}


@pytest.fixture
def client_and_mocks():
    """FastAPI TestClient with overridden pool and calendar dependencies."""
    mock_pool = MagicMock()
    mock_pool.close = AsyncMock()
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

    mock_cal = MockCalendarClient()

    app.dependency_overrides[get_db_pool] = lambda: mock_pool
    app.dependency_overrides[get_calendar_client] = lambda: mock_cal

    with patch("asyncpg.create_pool", new=AsyncMock(return_value=mock_pool)):
        with TestClient(app) as test_client:
            yield test_client, mock_pool, mock_conn, mock_cal

    app.dependency_overrides.clear()


# --- 1. Authentication & Security Tests -----------------------------------

class TestAPIAuthentication:
    """Verify X-API-Key authentication behavior."""

    def test_api_key_missing_or_invalid_returns_401(self, client_and_mocks) -> None:
        """Mutating POST endpoints must return 401 Unauthorized without valid X-API-Key header."""
        client, _, _, _ = client_and_mocks

        # Missing header
        res1 = client.post("/jobs/analyze", json={"source_url": "http://example.com/job"})
        assert res1.status_code == 401

        # Invalid key
        res2 = client.post(
            "/jobs/analyze",
            json={"source_url": "http://example.com/job"},
            headers={"X-API-Key": "wrong-key"},
        )
        assert res2.status_code == 401

    def test_api_key_valid_passes(self, client_and_mocks) -> None:
        """Valid X-API-Key header allows request to proceed."""
        client, _, _, _ = client_and_mocks

        with patch("src.agents.discovery.analyze_job") as mock_analyze:
            mock_analyze.return_value = {"job": {"id": str(uuid4())}, "outcome": "success", "error": None}
            res = client.post(
                "/jobs/analyze",
                json={"source_url": "http://example.com/job"},
                headers=TEST_HEADERS,
            )
            assert res.status_code == 200


# --- 2. Endpoints & Exception Mapping Tests ------------------------------

class TestAPIEndpoints:
    """Verify endpoint routing, status code mappings, and guardrails."""

    @patch("src.agents.discovery.analyze_job")
    def test_post_jobs_analyze_success(self, mock_analyze, client_and_mocks) -> None:
        """POST /jobs/analyze returns 200 and parsed job details."""
        client, _, _, _ = client_and_mocks
        job_id = str(uuid4())
        mock_analyze.return_value = {"job": {"id": job_id, "title": "Engineer"}, "outcome": "success", "error": None}

        res = client.post(
            "/jobs/analyze",
            json={"source_url": "http://example.com/job"},
            headers=TEST_HEADERS,
        )
        assert res.status_code == 200
        assert res.json()["outcome"] == "success"

    @patch("src.agents.content.generate_and_persist_content")
    def test_post_content_generate_fabrication_error(self, mock_gen, client_and_mocks) -> None:
        """FabricationError during content generation maps to 422 Unprocessable Entity."""
        client, _, mock_conn, _ = client_and_mocks
        job_id = str(uuid4())
        profile_id = str(uuid4())

        mock_conn.fetchrow.side_effect = [
            {"id": job_id, "title": "Engineer"},
            {"id": profile_id, "full_name": "Alex"},
        ]

        mock_gen.side_effect = FabricationError(["Invented metric: '95%'"])

        res = client.post(
            "/content/generate",
            json={"job_id": job_id, "profile_id": profile_id},
            headers=TEST_HEADERS,
        )

        assert res.status_code == 422
        assert res.json()["error_type"] == "fabrication_detected"
        assert "95%" in res.json()["violations"][0]

    def test_post_applications_approve_success(self, client_and_mocks) -> None:
        """POST /applications/{id}/approve transitions status from pending_review -> approved."""
        client, _, mock_conn, _ = client_and_mocks
        app_id = str(uuid4())
        job_id = str(uuid4())

        # 1. SELECT status returns 'pending_review'
        # 2. SELECT FOR UPDATE in update_status
        # 3. UPDATE applications in update_status
        mock_conn.fetchrow.side_effect = [
            {"status": "pending_review"},
            {"status": "pending_review"},
            {"id": app_id, "job_id": job_id, "status": "approved", "updated_at": "2026-08-06T14:00:00Z"},
        ]

        res = client.post(
            f"/applications/{app_id}/approve",
            json={"reason": "User clicked approve"},
            headers=TEST_HEADERS,
        )

        assert res.status_code == 200
        assert res.json()["status"] == "approved"

    def test_post_applications_approve_refuses_when_not_pending_review(self, client_and_mocks) -> None:
        """POST /applications/{id}/approve must REFUSE with 409 Conflict if app status is not pending_review."""
        client, _, mock_conn, _ = client_and_mocks
        app_id = str(uuid4())

        # Status is 'draft' (not pending_review)
        mock_conn.fetchrow.return_value = {"status": "draft"}

        res = client.post(
            f"/applications/{app_id}/approve",
            json={"reason": "Approve draft"},
            headers=TEST_HEADERS,
        )

        assert res.status_code == 409
        assert res.json()["error_type"] == "approval_refused"

    @patch("src.agents.application.submit_application")
    def test_post_applications_submit_refuses_when_not_approved(self, mock_submit, client_and_mocks) -> None:
        """POST /applications/{id}/submit returns 409 Conflict when status is not approved."""
        client, _, _, _ = client_and_mocks
        app_id = str(uuid4())

        mock_submit.side_effect = ApprovalError(f"Application {app_id} is in status 'pending_review', not 'approved'. Submission refused.")

        res = client.post(
            f"/applications/{app_id}/submit",
            json={},
            headers=TEST_HEADERS,
        )

        assert res.status_code == 409
        assert res.json()["error_type"] == "approval_refused"

    def test_post_applications_status_rejects_forbidden_states(self, client_and_mocks) -> None:
        """POST /applications/{id}/status must return 409 Conflict when setting approved, submitted, or submit_uncertain."""
        client, _, _, _ = client_and_mocks
        app_id = str(uuid4())

        for forbidden in ["approved", "submitted", "submit_uncertain"]:
            res = client.post(
                f"/applications/{app_id}/status",
                json={"new_status": forbidden},
                headers=TEST_HEADERS,
            )
            assert res.status_code == 409
            assert res.json()["error_type"] == "forbidden_status_transition"

    @patch("src.agents.tracking.update_status")
    def test_post_applications_status_allowed_state_success(self, mock_update, client_and_mocks) -> None:
        """POST /applications/{id}/status allows valid transitions like 'in_review'."""
        client, _, _, _ = client_and_mocks
        app_id = str(uuid4())
        job_id = str(uuid4())

        mock_update.return_value = {
            "id": app_id,
            "job_id": job_id,
            "status": "in_review",
            "updated_at": "2026-08-06T14:00:00Z",
        }

        res = client.post(
            f"/applications/{app_id}/status",
            json={"new_status": "in_review", "reason": "Recruiter message"},
            headers=TEST_HEADERS,
        )

        assert res.status_code == 200
        assert res.json()["status"] == "in_review"

    @patch("src.agents.tracking.schedule_interview")
    def test_post_applications_interview_orphaned_calendar_error_500(self, mock_sched, client_and_mocks) -> None:
        """OrphanedCalendarEventError during interview scheduling maps to 500 containing event ID."""
        client, _, _, _ = client_and_mocks
        app_id = str(uuid4())

        mock_sched.side_effect = OrphanedCalendarEventError(
            "Database transaction failed after creating Google Calendar event 'evt_orphaned_999'. Compensation delete also failed."
        )

        res = client.post(
            f"/applications/{app_id}/interview",
            json={"scheduled_at": "2026-08-15T10:00:00Z", "notes": "Deep dive"},
            headers=TEST_HEADERS,
        )

        assert res.status_code == 500
        assert res.json()["error_type"] == "orphaned_calendar_event"
        assert res.json()["calendar_event_id"] == "evt_orphaned_999"

    def test_get_application_not_found_returns_404(self, client_and_mocks) -> None:
        """GET /applications/{id} returns 404 Not Found when NotFoundError is raised."""
        client, _, mock_conn, _ = client_and_mocks
        app_id = str(uuid4())

        mock_conn.fetchrow.return_value = None

        res = client.get(f"/applications/{app_id}")
        assert res.status_code == 404
        assert res.json()["error_type"] == "not_found"
