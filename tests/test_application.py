"""Unit tests for Phase 3 — Application Agent (Greenhouse).

These tests run against fixtures and mock external DB / browser calls.
No live web portals or Playwright browser instances are launched in unit tests.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.application import (
    ApprovalError,
    FormFillError,
    prefill_application,
    render_resume_to_file,
    submit_application,
)
from src.services.playwright_service import map_greenhouse_fields


@pytest.fixture
def greenhouse_html_fixture() -> str:
    """Load the greenhouse_job_page.html fixture content."""
    fixture_path = Path("fixtures/greenhouse_job_page.html")
    return fixture_path.read_text(encoding="utf-8")


@pytest.fixture
def master_profile() -> dict:
    """Load the master_profile.json fixture."""
    fixture_path = Path("fixtures/master_profile.json")
    return json.loads(fixture_path.read_text(encoding="utf-8"))


# --- 1. Pure Form Field Mapping Unit Tests --------------------------------

class TestGreenhouseFormMapping:
    """Verify map_greenhouse_fields mapping logic and guardrails."""

    def test_map_greenhouse_fields_success(self, greenhouse_html_fixture: str, master_profile: dict) -> None:
        """Standard candidate profile fields should map correctly to form selectors."""
        res = map_greenhouse_fields(
            html=greenhouse_html_fixture,
            profile=master_profile,
            resume_path="fixtures/sample_resume.pdf",
            cover_letter_text="Dear Hiring Team...",
        )

        assert "#first_name" in res.field_map
        assert res.field_map["#first_name"]["value"] == master_profile["full_name"].split()[0]
        assert "#email" in res.field_map
        assert res.field_map["#email"]["value"] == master_profile["email"]
        assert "#resume" in res.field_map
        assert res.field_map["#resume"]["status"] == "uploaded"

    def test_custom_questions_left_blank_and_flagged(self, master_profile: dict) -> None:
        """Custom/screening text questions MUST be left blank and added to unanswered_questions."""
        html_with_custom = """
        <form id="application_form">
            <div class="field"><input id="first_name" name="first_name" type="text" /></div>
            <div class="field"><input id="email" name="email" type="text" /></div>
            <div class="field">
                <label for="custom_q1">Why do you want to work at Acme Corp?</label>
                <textarea id="custom_q1" name="custom_q1"></textarea>
            </div>
        </form>
        """

        res = map_greenhouse_fields(html=html_with_custom, profile=master_profile)

        # Custom question must NOT be in field_map with filled value
        assert "#custom_q1" not in res.field_map or res.field_map["#custom_q1"].get("value") == ""
        # Must be flagged in unanswered_questions list
        assert len(res.unanswered_questions) == 1
        assert any("Why do you want to work at Acme Corp?" in q for q in res.unanswered_questions)

    def test_map_greenhouse_fields_captcha_detected(self, master_profile: dict) -> None:
        """CAPTCHA containers must set captcha_detected=True."""
        html_with_captcha = """
        <form>
            <input id="first_name" name="first_name" type="text" />
            <div class="g-recaptcha" data-sitekey="test"></div>
        </form>
        """
        res = map_greenhouse_fields(html=html_with_captcha, profile=master_profile)
        assert res.captcha_detected is True

    def test_unfilled_required_questions_yields_unanswered_questions_and_manual_completion_required_true(
        self, master_profile: dict
    ) -> None:
        """Form with custom/screening required questions yields non-empty unanswered_questions and manual_completion_required=true."""
        html_jobboards = Path("fixtures/greenhouse_jobboards_layout.html").read_text(encoding="utf-8")
        res = map_greenhouse_fields(
            html=html_jobboards,
            profile=master_profile,
            resume_path="fixtures/sample_resume.pdf",
            cover_letter_text="Dear Hiring Team...",
        )

        assert len(res.unanswered_questions) > 0
        assert res.missing_required_fields is True
        manual_completion_required = res.captcha_detected or res.missing_required_fields or len(res.unanswered_questions) > 0
        assert manual_completion_required is True

    def test_standard_form_only_yields_empty_unanswered_questions(self, master_profile: dict) -> None:
        """Form with only standard fields (all fillable from profile) yields empty unanswered_questions and manual_completion_required=false."""
        html_standard_only = """
        <form id="application_form">
            <input id="first_name" name="first_name" aria-required="true" type="text" />
            <input id="last_name" name="last_name" aria-required="true" type="text" />
            <input id="email" name="email" aria-required="true" type="text" />
            <input id="phone" name="phone" aria-required="true" type="text" />
            <input id="resume" name="resume" type="file" />
        </form>
        """
        res = map_greenhouse_fields(
            html=html_standard_only,
            profile=master_profile,
            resume_path="fixtures/sample_resume.pdf",
        )

        assert res.unanswered_questions == []
        assert res.missing_required_fields is False
        manual_completion_required = res.captcha_detected or res.missing_required_fields or len(res.unanswered_questions) > 0
        assert manual_completion_required is False


# --- 2. Resume File Rendering Unit Tests ----------------------------------

class TestResumeFileRendering:
    """Verify render_resume_to_file converts JSONB resume content to uploadable file."""

    def test_render_resume_to_file(self, tmp_path: Path) -> None:
        """render_resume_to_file should generate a formatted text file."""
        content = {
            "summary": "Experienced engineer.",
            "experience": [
                {
                    "title": "Senior Software Engineer",
                    "company": "TechCorp",
                    "dates": "2022-present",
                    "bullets": ["Architected backend services."],
                }
            ],
            "skills": ["Python", "FastAPI"],
            "education": [{"degree": "B.S. CS", "institution": "UC Berkeley", "year": 2017}],
        }

        output_file = render_resume_to_file(
            resume_variant_id="test-res-123",
            content=content,
            output_dir=str(tmp_path),
        )

        assert Path(output_file).exists()
        text = Path(output_file).read_text(encoding="utf-8")
        assert "Experienced engineer." in text
        assert "Senior Software Engineer at TechCorp" in text
        assert "Python, FastAPI" in text


# --- 3. Mandatory Approval Gate & Submission Unit Tests --------------------

class TestApprovalGateEnforcement:
    """Verify mandatory approval gate and atomic submission rules."""

    @pytest.mark.asyncio
    async def test_submit_application_refuses_when_draft(self) -> None:
        """submit_application must REFUSE when application status is 'draft'."""
        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

        # Atomic UPDATE returns 0 rows, SELECT status returns 'draft'
        mock_conn.fetchrow.side_effect = [
            None,
            {"status": "draft"},
        ]

        with pytest.raises(ApprovalError, match="status 'draft', not 'approved'"):
            await submit_application("app-uuid-1", pool=mock_pool, skip_browser=True)

    @pytest.mark.asyncio
    async def test_submit_application_refuses_when_pending_review(self) -> None:
        """submit_application must REFUSE when application status is 'pending_review'."""
        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

        # Atomic UPDATE returns 0 rows, SELECT status returns 'pending_review'
        mock_conn.fetchrow.side_effect = [
            None,
            {"status": "pending_review"},
        ]

        with pytest.raises(ApprovalError, match="status 'pending_review', not 'approved'"):
            await submit_application("app-uuid-2", pool=mock_pool, skip_browser=True)

    @pytest.mark.asyncio
    async def test_submit_application_refuses_when_already_submitted(self) -> None:
        """submit_application must REFUSE when application is already 'submitted' (Idempotency)."""
        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

        # Atomic UPDATE returns 0 rows, SELECT status returns 'submitted'
        mock_conn.fetchrow.side_effect = [
            None,
            {"status": "submitted"},
        ]

        with pytest.raises(ApprovalError, match="already submitted"):
            await submit_application("app-uuid-3", pool=mock_pool, skip_browser=True)

    @pytest.mark.asyncio
    async def test_submit_application_succeeds_when_approved(self) -> None:
        """submit_application must SUCCEED when status is 'approved'."""
        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

        # Atomic UPDATE returns 1 row
        mock_conn.fetchrow.return_value = {
            "id": "app-uuid-approved",
            "job_id": "job-uuid",
            "resume_variant_id": "res-uuid",
            "cover_letter_id": None,
            "status": "submitted",
            "review_artifact": {},
        }

        res = await submit_application("app-uuid-approved", pool=mock_pool, skip_browser=True)

        assert res["id"] == "app-uuid-approved"
        assert res["status"] == "submitted"
        # Audit entry executed
        assert mock_conn.execute.called

    @pytest.mark.asyncio
    @patch("src.agents.application.submit_greenhouse_form")
    async def test_submit_application_browser_throws_sets_submit_uncertain(
        self,
        mock_browser_submit: MagicMock,
    ) -> None:
        """Browser exception during submit must set status to 'submit_uncertain' and NEVER auto-revert to 'approved'."""
        mock_browser_submit.side_effect = RuntimeError("Network timeout clicking submit")

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

        # 1. Atomic UPDATE returns 1 row (slot claimed)
        # 2. Browser submit throws
        # 3. DB UPDATE sets status='submit_uncertain'
        mock_conn.fetchrow.side_effect = [
            {"id": "app-uuid-uncertain", "job_id": "j-1", "status": "submitted"},
            {"id": "app-uuid-uncertain", "status": "submit_uncertain", "review_artifact": {"submit_error": "timeout"}},
        ]

        with pytest.raises(FormFillError, match="submit_uncertain"):
            await submit_application(
                "app-uuid-uncertain",
                pool=mock_pool,
                page_or_url="http://example.com/job/1",
                skip_browser=False,
            )

        # Verify that status was updated to submit_uncertain
        calls = mock_conn.fetchrow.call_args_list
        assert any("submit_uncertain" in str(c) for c in calls)


# --- 4. Pre-fill Agent Function Unit Tests --------------------------------

class TestPrefillApplication:
    """Verify prefill_application orchestration and artifact generation."""

    @pytest.mark.asyncio
    async def test_prefill_application_creates_review_artifact(
        self,
        greenhouse_html_fixture: str,
        master_profile: dict,
        tmp_path: Path,
    ) -> None:
        """prefill_application should create review artifact JSONB and pending_review status."""
        dummy_resume_file = tmp_path / "resume.txt"
        dummy_resume_file.write_text("Resume content")

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

        mock_conn.fetchrow.return_value = {
            "id": "app-uuid-prefilled",
            "job_id": "job-uuid-1",
            "resume_variant_id": "res-uuid-1",
            "cover_letter_id": None,
            "status": "pending_review",
            "review_artifact": {"screenshot_path": "artifacts/screenshots/fixture_prefill.png"},
            "created_at": "now",
            "updated_at": "now",
        }

        res = await prefill_application(
            job_id="job-uuid-1",
            profile_id="prof-uuid-1",
            resume_variant_id="res-uuid-1",
            cover_letter_id=None,
            profile=master_profile,
            job={"source_url": "http://example.com/job/1"},
            resume_variant={"content": {"summary": "Eng summary"}, "file_path": str(dummy_resume_file)},
            cover_letter=None,
            pool=mock_pool,
            page_or_url=greenhouse_html_fixture,
            skip_browser=True,
        )

        assert res["status"] == "pending_review"
        assert "review_artifact" in res
