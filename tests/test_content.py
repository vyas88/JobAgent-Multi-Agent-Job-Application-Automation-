"""Tests for Phase 2 — Resume and Content Agent.

These tests run against fixtures and mock external API/DB calls.
No live web portals or real credentials are used.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.content import (
    CoverLetterResult,
    FabricationError,
    TailoredEducationEntry,
    TailoredExperienceEntry,
    TailoredResumeResult,
    extract_numeric_claims,
    generate_and_persist_content,
    generate_cover_letter,
    generate_tailored_resume,
    normalize_numeric_value,
    persist_cover_letter,
    persist_resume_variant,
    verify_cover_letter_no_fabrication,
    verify_resume_no_fabrication,
)


# --- 1. Number Normalization Equivalence Tests ----------------------------

class TestNumberNormalization:
    """Verify normalize_numeric_value equivalence across phrasing variations."""

    def test_million_equivalences(self) -> None:
        """2M+, 2,000,000, and 2 million should normalize to identical numeric values."""
        v1, u1 = normalize_numeric_value("2M+")
        v2, u2 = normalize_numeric_value("2,000,000")
        v3, u3 = normalize_numeric_value("2 million")

        assert v1 == 2_000_000.0
        assert v2 == 2_000_000.0
        assert v3 == 2_000_000.0
        assert v1 == v2 == v3

    def test_percentage_equivalences(self) -> None:
        """40% and 40 percent should normalize to identical percentage values."""
        v1, u1 = normalize_numeric_value("40%")
        v2, u2 = normalize_numeric_value("40 percent")

        assert v1 == 40.0
        assert v2 == 40.0
        assert u1 == "%"
        assert u2 == "%"

    def test_word_number_equivalences(self) -> None:
        """four and 4 should normalize to identical numeric values."""
        v1, _ = normalize_numeric_value("four")
        v2, _ = normalize_numeric_value("4")

        assert v1 == 4.0
        assert v2 == 4.0

    def test_currency_equivalences(self) -> None:
        """$2M and 2 million USD should normalize to identical currency values."""
        v1, u1 = normalize_numeric_value("$2M")
        v2, u2 = normalize_numeric_value("2 million USD")

        assert v1 == 2_000_000.0
        assert v2 == 2_000_000.0
        assert u1 == "$"
        assert u2 == "$"


# --- 2. No-Fabrication Verification Tests --------------------------------

class TestNoFabricationVerification:
    """Verify resume and cover letter no-fabrication guardrails."""

    def test_absent_number_hard_fails(self, master_profile: dict) -> None:
        """Generating a number absent from master profile must hard-fail."""
        resume = TailoredResumeResult(
            summary="Full-stack engineer with 6 years experience.",
            experience=[
                TailoredExperienceEntry(
                    title="Senior Software Engineer",
                    company="TechCorp Inc.",
                    dates="2022-01 to present",
                    bullets=[
                        "Led microservices platform serving 2M+ daily requests.",
                        "Increased team productivity by 95% using automation.",  # 95% absent from profile!
                    ],
                )
            ],
            skills=["Python", "FastAPI"],
            education=[
                TailoredEducationEntry(
                    degree="B.S. Computer Science",
                    institution="University of California, Berkeley",
                    year=2017,
                )
            ],
            certifications=["AWS Certified Solutions Architect – Associate"],
        )

        is_valid, hard_violations, _ = verify_resume_no_fabrication(resume, master_profile)

        assert not is_valid
        assert any("95" in v for v in hard_violations)

    def test_mismatched_context_flags_needs_review(self, master_profile: dict) -> None:
        """Matching number with mismatched context/unit must populate needs_review, not hard-fail."""
        resume = TailoredResumeResult(
            summary="Full-stack engineer with 6 years experience.",
            experience=[
                TailoredExperienceEntry(
                    title="Senior Software Engineer",
                    company="TechCorp Inc.",
                    dates="2022-01 to present",
                    bullets=[
                        "Managed 60% reduction in team size.",  # Profile has "reducing deployment time by 60%"
                    ],
                )
            ],
            skills=["Python"],
            education=[
                TailoredEducationEntry(
                    degree="B.S. Computer Science",
                    institution="University of California, Berkeley",
                    year=2017,
                )
            ],
        )

        is_valid, hard_violations, needs_review = verify_resume_no_fabrication(resume, master_profile)

        assert is_valid  # Number 60% exists in profile, so does not hard-fail
        assert len(hard_violations) == 0
        assert len(needs_review) > 0
        assert any("60%" in nr for nr in needs_review)

    def test_legitimate_paraphrase_passes(self, master_profile: dict) -> None:
        """Legitimate paraphrase using equivalent numbers and matching context passes cleanly."""
        resume = TailoredResumeResult(
            summary="Full-stack software engineer with 6 years experience.",
            experience=[
                TailoredExperienceEntry(
                    title="Senior Software Engineer",
                    company="TechCorp Inc.",
                    dates="2022-01 to present",
                    bullets=[
                        "Architected microservices handling 2 million daily requests.",  # Paraphrase of "2M+ daily requests"
                        "Mentored four junior engineers through code reviews.",         # Paraphrase of "team of 4 junior engineers"
                    ],
                )
            ],
            skills=["Python", "FastAPI", "PostgreSQL"],
            education=[
                TailoredEducationEntry(
                    degree="B.S. Computer Science",
                    institution="University of California, Berkeley",
                    year=2017,
                )
            ],
            certifications=["AWS Certified Solutions Architect – Associate"],
        )

        is_valid, hard_violations, needs_review = verify_resume_no_fabrication(resume, master_profile)

        assert is_valid
        assert len(hard_violations) == 0

    def test_invented_employer_hard_fails(self, master_profile: dict) -> None:
        """Invented employer name must cause a hard failure."""
        resume = TailoredResumeResult(
            summary="Full-stack engineer with 6 years experience.",
            experience=[
                TailoredExperienceEntry(
                    title="Senior Software Engineer",
                    company="Fake Corp LLC",  # Invented!
                    dates="2022-01 to present",
                    bullets=["Wrote code."],
                )
            ],
            skills=["Python"],
            education=[],
        )

        is_valid, hard_violations, _ = verify_resume_no_fabrication(resume, master_profile)

        assert not is_valid
        assert any("Fake Corp LLC" in v for v in hard_violations)


# --- 3. Cover Letter Verification Tests -----------------------------------

class TestCoverLetterVerification:
    """Verify cover letter grounding against profile and job posting."""

    def test_cover_letter_invented_number_hard_fails(self, master_profile: dict) -> None:
        """Cover letter with invented number must hard-fail."""
        job = {"title": "Senior Backend Engineer", "company": "Acme Corp", "requirements": []}
        cover_res = CoverLetterResult(
            cover_letter="I managed 80 engineers at TechCorp.",  # Profile has 4 engineers
            company_referenced="Acme Corp",
            title_referenced="Senior Backend Engineer",
        )

        is_valid, hard_violations, _ = verify_cover_letter_no_fabrication(cover_res, master_profile, job)

        assert not is_valid
        assert any("80" in v for v in hard_violations)

    def test_cover_letter_grounded_passes(self, master_profile: dict) -> None:
        """Cover letter referencing job title, company, and valid profile metrics passes."""
        job = {"title": "Senior Backend Engineer", "company": "Acme Corp", "requirements": ["5+ years experience"]}
        cover_res = CoverLetterResult(
            cover_letter="Dear Acme Corp Hiring Team,\n\nI am excited to apply for the Senior Backend Engineer role. "
                         "At TechCorp Inc., I led a microservices platform serving 2M+ daily requests using Python.",
            company_referenced="Acme Corp",
            title_referenced="Senior Backend Engineer",
            key_points=["Microservices platform", "2M+ daily requests"],
        )

        is_valid, hard_violations, _ = verify_cover_letter_no_fabrication(cover_res, master_profile, job)

        assert is_valid
        assert len(hard_violations) == 0


# --- 4. Agent Function & Persistence Tests (Mocked) ----------------------

class TestAgentGenerationAndPersistence:
    """Verify LLM generation and Postgres persistence functions."""

    @patch("src.agents.content.call_openai")
    def test_generate_tailored_resume_calls_openai(self, mock_call_openai: MagicMock, master_profile: dict) -> None:
        """generate_tailored_resume should invoke OpenAI and run verification."""
        expected_resume = TailoredResumeResult(
            summary="Full-stack software engineer with 6 years experience.",
            experience=[
                TailoredExperienceEntry(
                    title="Senior Software Engineer",
                    company="TechCorp Inc.",
                    dates="2022-01 to present",
                    bullets=["Led platform serving 2M+ daily requests."],
                )
            ],
            skills=["Python", "FastAPI"],
            education=[
                TailoredEducationEntry(
                    degree="B.S. Computer Science",
                    institution="University of California, Berkeley",
                    year=2017,
                )
            ],
        )
        mock_call_openai.return_value = expected_resume

        job = {"title": "Senior Backend Engineer", "company": "Acme Corp", "keywords": ["Python"]}
        result = generate_tailored_resume(job, master_profile)

        assert result.summary == expected_resume.summary
        mock_call_openai.assert_called_once()

    @patch("src.agents.content.call_openai")
    def test_generate_tailored_resume_fabrication_raises(self, mock_call_openai: MagicMock, master_profile: dict) -> None:
        """generate_tailored_resume should raise FabricationError on fabrication."""
        fake_resume = TailoredResumeResult(
            summary="Full-stack software engineer with 6 years experience.",
            experience=[
                TailoredExperienceEntry(
                    title="Senior Software Engineer",
                    company="Invented Employer Ltd",
                    dates="2022-01 to present",
                    bullets=["Did things."],
                )
            ],
            skills=["Python"],
            education=[],
        )
        mock_call_openai.return_value = fake_resume

        job = {"title": "Engineer", "company": "Acme"}
        with pytest.raises(FabricationError, match="Invented employer"):
            generate_tailored_resume(job, master_profile)

    @patch("src.agents.content.call_openai")
    def test_generate_cover_letter_calls_openai(self, mock_call_openai: MagicMock, master_profile: dict) -> None:
        """generate_cover_letter should invoke OpenAI and return valid CoverLetterResult."""
        expected_letter = CoverLetterResult(
            cover_letter="Dear Acme Corp,\n\nI am applying for Senior Backend Engineer.",
            company_referenced="Acme Corp",
            title_referenced="Senior Backend Engineer",
        )
        mock_call_openai.return_value = expected_letter

        job = {"title": "Senior Backend Engineer", "company": "Acme Corp"}
        result = generate_cover_letter(job, master_profile)

        assert result.company_referenced == "Acme Corp"
        mock_call_openai.assert_called_once()

    @pytest.mark.asyncio
    @patch("src.agents.content.generate_cover_letter")
    @patch("src.agents.content.generate_tailored_resume")
    async def test_generate_and_persist_content_orchestration(
        self,
        mock_resume: MagicMock,
        mock_letter: MagicMock,
        master_profile: dict,
    ) -> None:
        """generate_and_persist_content orchestrates resume and cover letter generation and persistence."""
        mock_resume.return_value = TailoredResumeResult(
            summary="Engineer summary with 6 years experience.",
            experience=[],
            skills=["Python"],
            education=[],
            needs_review=["Soft claim check"],
        )
        mock_letter.return_value = CoverLetterResult(
            cover_letter="Cover letter body text...",
            company_referenced="Acme Corp",
            title_referenced="Engineer",
            needs_review=["Check phrase"],
        )

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

        mock_conn.fetchrow.side_effect = [
            {"id": "res-uuid", "job_id": "j-uuid", "profile_id": "p-uuid", "content": "{}", "file_path": None, "created_at": "now"},
            {"id": "let-uuid", "job_id": "j-uuid", "profile_id": "p-uuid", "content": "text", "created_at": "now"},
        ]

        job = {"title": "Engineer", "company": "Acme Corp"}
        res = await generate_and_persist_content(
            job_id="j-uuid",
            profile_id="p-uuid",
            job=job,
            profile=master_profile,
            pool=mock_pool,
        )

        assert res["job_id"] == "j-uuid"
        assert res["persisted_resume"]["id"] == "res-uuid"
        assert res["persisted_letter"]["id"] == "let-uuid"
        assert len(res["needs_review"]) == 2

    @pytest.mark.asyncio
    async def test_persist_resume_variant_postgres(self) -> None:
        """persist_resume_variant should insert JSONB content into Postgres matching live schema."""
        mock_pool = MagicMock()
        mock_conn = AsyncMock()

        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        mock_conn.fetchrow.return_value = {
            "id": "11111111-1111-1111-1111-111111111111",
            "job_id": "22222222-2222-2222-2222-222222222222",
            "profile_id": "33333333-3333-3333-3333-333333333333",
            "content": '{"summary": "Test summary"}',
            "file_path": None,
            "created_at": "2026-08-06T00:00:00Z",
        }

        res = await persist_resume_variant(
            job_id="22222222-2222-2222-2222-222222222222",
            profile_id="33333333-3333-3333-3333-333333333333",
            content={"summary": "Test summary"},
            file_path=None,
            pool=mock_pool,
        )

        assert res["id"] == "11111111-1111-1111-1111-111111111111"
        mock_conn.fetchrow.assert_called_once()

    @pytest.mark.asyncio
    async def test_persist_cover_letter_postgres(self) -> None:
        """persist_cover_letter should insert text content into Postgres matching live schema."""
        mock_pool = MagicMock()
        mock_conn = AsyncMock()

        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        mock_conn.fetchrow.return_value = {
            "id": "44444444-4444-4444-4444-444444444444",
            "job_id": "22222222-2222-2222-2222-222222222222",
            "profile_id": "33333333-3333-3333-3333-333333333333",
            "content": "Dear Hiring Manager...",
            "created_at": "2026-08-06T00:00:00Z",
        }

        res = await persist_cover_letter(
            job_id="22222222-2222-2222-2222-222222222222",
            profile_id="33333333-3333-3333-3333-333333333333",
            content_text="Dear Hiring Manager...",
            pool=mock_pool,
        )

        assert res["id"] == "44444444-4444-4444-4444-444444444444"
        mock_conn.fetchrow.assert_called_once()
