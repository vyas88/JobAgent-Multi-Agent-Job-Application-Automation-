"""Tests for Phase 1 — Discovery and Analysis Agent (Greenhouse only).

These tests run against saved HTML fixtures and mock external API/DB calls.
No live web portals or real credentials are used.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.discovery import (
    SYNONYM_MAP,
    JobAnalysisResult,
    analyze_jd,
    analyze_job,
    extract_profile_vocabulary,
    parse_job,
    persist_job,
    score_fit,
    tokenize_text,
)
from src.services.playwright_service import GreenhouseParseError, parse_greenhouse_job_page


# --- 1. Pure HTML Parsing Tests ------------------------------------------

class TestGreenhouseParser:
    """Verify parse_greenhouse_job_page pure HTML parser."""

    def test_parse_valid_fixture(self, greenhouse_page_html: str) -> None:
        """parse_greenhouse_job_page should correctly extract fields from fixture HTML."""
        result = parse_greenhouse_job_page(greenhouse_page_html)

        assert result.title == "Senior Backend Engineer"
        assert result.company == "Acme Corp"
        assert result.location == "San Francisco, CA (Hybrid)"
        assert "About Acme Corp" in result.raw_jd
        assert "What You'll Do" in result.raw_jd

    def test_parse_raises_on_empty_html(self) -> None:
        """Should raise GreenhouseParseError when HTML is empty."""
        with pytest.raises(GreenhouseParseError, match="empty"):
            parse_greenhouse_job_page("")

    def test_parse_raises_on_missing_title(self) -> None:
        """Should raise GreenhouseParseError when title is missing."""
        html = '<div class="company-name">Acme</div><div id="content">Long JD content text here...</div>'
        with pytest.raises(GreenhouseParseError, match="job title"):
            parse_greenhouse_job_page(html)

    def test_parse_missing_company_defaults_to_unknown(self) -> None:
        """Missing company element defaults to 'Unknown' without raising parse_failed."""
        html = '<h1 class="app-title">Engineer</h1><div id="content">Long JD content text here...</div>'
        result = parse_greenhouse_job_page(html)
        assert result.title == "Engineer"
        assert result.company == "Unknown"

    def test_parse_raises_on_missing_jd(self) -> None:
        """Should raise GreenhouseParseError when JD content is missing."""
        html = '<h1 class="app-title">Engineer</h1><div class="company-name">Acme</div>'
        with pytest.raises(GreenhouseParseError, match="job description content"):
            parse_greenhouse_job_page(html)

    def test_parse_jobboards_new_layout_fixture(self) -> None:
        """parse_greenhouse_job_page parses new job-boards.greenhouse.io layout fixture cleanly."""
        fixture_path = Path("fixtures/greenhouse_jobboards_layout.html")
        assert fixture_path.exists(), "New layout fixture file must exist"
        html = fixture_path.read_text(encoding="utf-8")
        result = parse_greenhouse_job_page(html)

        assert result.title == "Senior Forward Deployed Engineer (Remote Build)"
        assert result.company == "Unknown"
        assert len(result.raw_jd) > 100


# --- 2. Tokenization and Fit Scoring Tests --------------------------------

class TestFitScoring:
    """Verify fit_score computation with synonym mapping and word boundaries."""

    def test_tokenize_text_synonyms(self) -> None:
        """tokenize_text should map tech synonyms correctly."""
        tokens = tokenize_text("Building services with JS, K8s, and Postgres")
        assert "javascript" in tokens
        assert "kubernetes" in tokens
        assert "postgresql" in tokens
        assert "js" not in tokens
        assert "k8s" not in tokens

    def test_extract_profile_vocabulary(self, master_profile: dict) -> None:
        """extract_profile_vocabulary should extract and normalize candidate skills."""
        vocab = extract_profile_vocabulary(master_profile)

        assert "python" in vocab
        assert "fastapi" in vocab
        assert "postgresql" in vocab
        assert "docker" in vocab

    def test_score_fit_high_match(self, master_profile: dict) -> None:
        """score_fit should return high score for matching profile skills."""
        keywords = ["Python", "FastAPI", "PostgreSQL", "Docker", "REST APIs"]
        requirements = [
            "Experience with Python and FastAPI microservices.",
            "Proficiency in PostgreSQL database optimization.",
        ]

        score = score_fit(keywords, requirements, master_profile)
        assert score >= 70.0

    def test_score_fit_low_match(self, master_profile: dict) -> None:
        """score_fit should return low score for unmatched skills."""
        keywords = ["Swift", "iOS", "CoreData", "SwiftUI", "Xcode"]
        requirements = ["5+ years iOS native development experience with Swift."]

        score = score_fit(keywords, requirements, master_profile)
        assert score < 30.0

    def test_score_fit_empty_profile(self) -> None:
        """score_fit should return 0.0 for an empty profile."""
        score = score_fit(["Python"], ["Req"], {})
        assert score == 0.0


# --- 3. LLM Analysis Test (Mocked) ---------------------------------------

class TestAnalyzeJD:
    """Verify analyze_jd invokes OpenAI client correctly."""

    @patch("src.agents.discovery.call_openai")
    def test_analyze_jd_calls_openai(self, mock_call_openai: MagicMock) -> None:
        """analyze_jd should invoke call_openai with discovery_analyze prompt."""
        expected_result = JobAnalysisResult(
            title="Senior Backend Engineer",
            company="Acme Corp",
            location="San Francisco, CA",
            requirements=["5+ years Python"],
            keywords=["Python", "FastAPI"],
        )
        mock_call_openai.return_value = expected_result

        result = analyze_jd("Raw JD text...")

        mock_call_openai.assert_called_once_with(
            prompt_name="discovery_analyze",
            user_message="Raw JD text...",
            response_model=JobAnalysisResult,
        )
        assert result == expected_result


# --- 4. Database Persistence Test (Mocked Pool) --------------------------

class TestPersistJob:
    """Verify persist_job performs Postgres upsert."""

    @pytest.mark.asyncio
    async def test_persist_job_upsert(self) -> None:
        """persist_job should execute asyncpg upsert query."""
        mock_pool = MagicMock()
        mock_conn = AsyncMock()

        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        mock_conn.fetchrow.return_value = {
            "id": "123e4567-e89b-12d3-a456-426614174000",
            "source_url": "https://boards.greenhouse.io/acme/jobs/123",
            "platform": "greenhouse",
            "title": "Senior Backend Engineer",
            "company": "Acme Corp",
            "location": "San Francisco, CA",
            "raw_jd": "JD text",
            "requirements": '["5+ years Python"]',
            "keywords": '["Python"]',
            "fit_score": 85.0,
            "status": "qualified",
            "created_at": "2026-08-06T00:00:00Z",
            "updated_at": "2026-08-06T00:00:00Z",
        }

        job_data = {
            "source_url": "https://boards.greenhouse.io/acme/jobs/123",
            "platform": "greenhouse",
            "title": "Senior Backend Engineer",
            "company": "Acme Corp",
            "location": "San Francisco, CA",
            "raw_jd": "JD text",
            "requirements": ["5+ years Python"],
            "keywords": ["Python"],
            "fit_score": 85.0,
            "status": "qualified",
        }

        result = await persist_job(job_data, mock_pool)

        assert result["id"] == "123e4567-e89b-12d3-a456-426614174000"
        mock_conn.fetchrow.assert_called_once()


# --- 5. End-to-End Orchestration Tests ----------------------------------

class TestAnalyzeJobOrchestration:
    """Verify top-level analyze_job orchestration."""

    @pytest.mark.asyncio
    @patch("src.agents.discovery.analyze_jd")
    async def test_analyze_job_success_path(
        self,
        mock_analyze_jd: MagicMock,
        greenhouse_page_html: str,
        master_profile: dict,
    ) -> None:
        """analyze_job should parse, analyze, score, and return qualified job."""
        mock_analyze_jd.return_value = JobAnalysisResult(
            title="Senior Backend Engineer",
            company="Acme Corp",
            location="San Francisco, CA",
            requirements=["5+ years experience in Python", "PostgreSQL database optimization"],
            keywords=["Python", "FastAPI", "PostgreSQL", "Docker", "CI/CD"],
        )

        url = "https://boards.greenhouse.io/acme/jobs/12345"
        result = await analyze_job(url, greenhouse_page_html, master_profile)

        assert result["source_url"] == url
        assert result["platform"] == "greenhouse"
        assert result["title"] == "Senior Backend Engineer"
        assert result["company"] == "Acme Corp"
        assert result["fit_score"] >= 60.0
        assert result["status"] == "qualified"

    @pytest.mark.asyncio
    @patch("src.agents.discovery.analyze_jd")
    async def test_analyze_job_disqualified_path(
        self,
        mock_analyze_jd: MagicMock,
        greenhouse_page_html: str,
        master_profile: dict,
    ) -> None:
        """analyze_job should set status to disqualified for low fit score."""
        mock_analyze_jd.return_value = JobAnalysisResult(
            title="iOS Developer",
            company="Acme Corp",
            location="San Francisco, CA",
            requirements=["10+ years Objective-C and Swift"],
            keywords=["Swift", "iOS", "CoreData", "SwiftUI"],
        )

        url = "https://boards.greenhouse.io/acme/jobs/999"
        result = await analyze_job(url, greenhouse_page_html, master_profile)

        assert result["status"] == "disqualified"
        assert result["fit_score"] < 60.0

    @pytest.mark.asyncio
    @patch("src.agents.discovery.analyze_jd")
    async def test_analyze_job_parse_failed_path(
        self,
        mock_analyze_jd: MagicMock,
        master_profile: dict,
    ) -> None:
        """On GreenhouseParseError, analyze_job should return parse_failed in-memory outcome without calling LLM or DB."""
        bad_html = "<html><body>Invalid structure missing elements</body></html>"
        url = "https://boards.greenhouse.io/acme/jobs/bad"

        mock_pool = MagicMock()

        result = await analyze_job(url, bad_html, master_profile, pool=mock_pool)

        # Assert manual-review outcome returned
        assert result["status"] == "parse_failed"
        assert result["source_url"] == url
        assert "error" in result

        # Assert LLM and DB calls were NEVER made
        mock_analyze_jd.assert_not_called()
        mock_pool.acquire.assert_not_called()
