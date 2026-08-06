"""Discovery and Analysis Agent.

Responsibilities (ARCHITECTURE.md §2.1):
- Extract raw JD text and metadata from rendered Greenhouse HTML pages.
- Parse JDs into structured requirements and ranked ATS keywords via OpenAI.
- Compute deterministic fit scores against candidate profiles using word-boundary matching and synonym mapping.
- Persist job records in Postgres with an upsert policy on duplicate source_url.
- Gracefully handle parse failures with an in-memory manual-review outcome without writing partial/corrupted DB rows.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any

import asyncpg
from pydantic import BaseModel, Field

from src.llm.openai_client import call_openai
from src.services.playwright_service import (
    GreenhouseParseError,
    ParsedJobData,
    fetch_greenhouse_job_page,
    parse_greenhouse_job_page,
)

if TYPE_CHECKING:
    from src.config import Settings

logger = logging.getLogger(__name__)

# Common tech/domain synonym map for normalized matching.
SYNONYM_MAP: dict[str, str] = {
    "js": "javascript",
    "ts": "typescript",
    "py": "python",
    "postgres": "postgresql",
    "k8s": "kubernetes",
    "reactjs": "react",
    "node": "nodejs",
    "aws": "amazon web services",
    "gcp": "google cloud platform",
}

# Stopwords to filter out when tokenizing requirements/keywords.
STOPWORDS: set[str] = {
    "a", "an", "the", "and", "or", "but", "if", "because", "as", "what", "which",
    "this", "that", "these", "those", "then", "just", "so", "than", "such",
    "both", "through", "about", "against", "between", "into", "throughout",
    "during", "before", "after", "above", "below", "to", "from", "up", "upon",
    "down", "in", "out", "on", "off", "over", "under", "again", "further",
    "with", "without", "at", "by", "for", "with", "about", "against", "between",
    "into", "through", "during", "before", "after", "above", "below", "to",
    "from", "up", "down", "in", "out", "on", "off", "over", "under", "again",
    "further", "then", "once", "here", "there", "when", "where", "why", "how",
    "all", "any", "both", "each", "few", "more", "most", "other", "some", "such",
    "no", "nor", "not", "only", "own", "same", "so", "than", "too", "very",
    "s", "t", "can", "will", "just", "don", "should", "now", "be", "is", "are",
    "was", "were", "been", "being", "have", "has", "had", "having", "do", "does",
    "did", "doing", "would", "could", "should", "ought", "i", "you", "he", "she",
    "it", "we", "they", "them", "their", "my", "your", "his", "her", "its", "our",
    "experience", "years", "year", "development", "building", "work", "working",
    "team", "engineer", "senior", "role", "looking", "plus", "must", "strong",
    "ability", "knowledge", "understanding", "good", "great", "well"
}


class JobAnalysisResult(BaseModel):
    """Structured LLM output for job description analysis."""

    title: str = Field(description="Job title extracted or refined from the JD")
    company: str = Field(description="Company name")
    location: str | None = Field(default=None, description="Job location")
    requirements: list[str] = Field(description="Key requirements and qualifications")
    keywords: list[str] = Field(description="Ranked list of ATS keywords")


def tokenize_text(text: str) -> set[str]:
    """Tokenize text into lowercased, synonym-mapped word-boundary tokens."""
    raw_tokens = re.findall(r"\b[a-zA-Z0-9_-]+\b", text.lower())
    tokens = set()
    for tok in raw_tokens:
        mapped = SYNONYM_MAP.get(tok, tok)
        if mapped not in STOPWORDS and len(mapped) > 1:
            tokens.add(mapped)
    return tokens


def extract_profile_vocabulary(profile: dict[str, Any]) -> set[str]:
    """Extract and normalize all vocabulary tokens from a candidate profile."""
    terms: set[str] = set()

    # Skills
    for skill in profile.get("skills", []):
        terms.update(tokenize_text(str(skill)))

    # Summary
    if summary := profile.get("summary"):
        terms.update(tokenize_text(str(summary)))

    # Experience
    for exp in profile.get("experience", []):
        if title := exp.get("title"):
            terms.update(tokenize_text(str(title)))
        for bullet in exp.get("bullets", []):
            terms.update(tokenize_text(str(bullet)))

    # Education
    for edu in profile.get("education", []):
        if degree := edu.get("degree"):
            terms.update(tokenize_text(str(degree)))
        if field := edu.get("institution"):
            terms.update(tokenize_text(str(field)))

    # Certifications
    for cert in profile.get("certifications", []):
        terms.update(tokenize_text(str(cert)))

    return terms


def score_fit(
    keywords: list[str],
    requirements: list[str],
    profile: dict[str, Any],
) -> float:
    """Compute a deterministic fit score (0.0 to 100.0) between profile and JD.

    Word-boundary matching with synonym mapping and keyword ranking weights.
    """
    profile_vocab = extract_profile_vocabulary(profile)
    if not profile_vocab:
        return 0.0

    # 1. Keyword Score (60% weight)
    # Deduplicate keywords post-normalization
    normalized_keywords: list[tuple[str, set[str]]] = []
    seen_kw: set[str] = set()

    for kw in keywords:
        kw_tokens = tokenize_text(kw)
        if not kw_tokens:
            continue
        kw_key = " ".join(sorted(kw_tokens))
        if kw_key not in seen_kw:
            seen_kw.add(kw_key)
            normalized_keywords.append((kw, kw_tokens))

    if not normalized_keywords:
        kw_score = 0.0
    else:
        total_kw_weight = 0.0
        matched_kw_weight = 0.0

        for idx, (_, kw_tokens) in enumerate(normalized_keywords):
            weight = 2.0 if idx < 5 else 1.0
            total_kw_weight += weight
            # Keyword matches if all its tokens exist in the candidate's profile
            if kw_tokens.issubset(profile_vocab):
                matched_kw_weight += weight

        kw_score = (matched_kw_weight / total_kw_weight) * 100.0 if total_kw_weight > 0 else 0.0

    # 2. Requirements Score (40% weight)
    if not requirements:
        req_score = kw_score  # Fallback if no requirements extracted
    else:
        matched_req_count = 0
        total_req_count = 0

        for req in requirements:
            req_tokens = tokenize_text(req)
            if not req_tokens:
                continue
            total_req_count += 1
            # Requirement matches if at least 40% of its key tokens are found in profile
            matches = req_tokens.intersection(profile_vocab)
            if len(matches) / len(req_tokens) >= 0.4:
                matched_req_count += 1

        req_score = (matched_req_count / total_req_count) * 100.0 if total_req_count > 0 else 0.0

    final_score = 0.6 * kw_score + 0.4 * req_score
    return round(min(100.0, max(0.0, final_score)), 2)


def parse_job(html: str) -> ParsedJobData:
    """Pure parser: extract title, company, location, and raw_jd from HTML.

    Raises GreenhouseParseError if required elements are missing.
    """
    return parse_greenhouse_job_page(html)


def analyze_jd(raw_jd: str) -> JobAnalysisResult:
    """Call OpenAI with discovery_analyze prompt to extract requirements and keywords."""
    return call_openai(
        prompt_name="discovery_analyze",
        user_message=raw_jd,
        response_model=JobAnalysisResult,
    )


async def persist_job(job_dict: dict[str, Any], pool: asyncpg.Pool) -> dict[str, Any]:
    """Persist or upsert a job row into Postgres using asyncpg pool."""
    query = """
        INSERT INTO jobs (
            source_url, platform, title, company, location, raw_jd,
            requirements, keywords, fit_score, status
        ) VALUES (
            $1, $2::platform, $3, $4, $5, $6,
            $7::jsonb, $8::jsonb, $9, $10::job_status
        )
        ON CONFLICT (source_url) DO UPDATE SET
            title = EXCLUDED.title,
            company = EXCLUDED.company,
            location = EXCLUDED.location,
            raw_jd = EXCLUDED.raw_jd,
            requirements = EXCLUDED.requirements,
            keywords = EXCLUDED.keywords,
            fit_score = EXCLUDED.fit_score,
            status = EXCLUDED.status,
            updated_at = now()
        RETURNING id, source_url, platform, title, company, location, raw_jd,
                  requirements, keywords, fit_score, status, created_at, updated_at;
    """

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            query,
            job_dict["source_url"],
            job_dict["platform"],
            job_dict["title"],
            job_dict["company"],
            job_dict.get("location"),
            job_dict["raw_jd"],
            json.dumps(job_dict["requirements"]),
            json.dumps(job_dict["keywords"]),
            job_dict["fit_score"],
            job_dict["status"],
        )

    return dict(row) if row else job_dict


async def analyze_job(
    source_url: str,
    html_content: str | None = None,
    profile: dict[str, Any] | None = None,
    pool: asyncpg.Pool | None = None,
    fit_threshold: float = 60.0,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Orchestrate the Discovery and Analysis Agent flow for a job page.

    Steps:
    1. Fetch HTML via Playwright service if html_content is not provided.
    2. Fetch candidate profile from DB or fixture if profile is not provided.
    3. Parse HTML (pure). On parse failure, return manual-review outcome without calling LLM or DB.
    4. Analyze JD via OpenAI.
    5. Score fit against candidate profile.
    6. Determine job status ('qualified' if fit_score >= threshold, else 'disqualified').
    7. Persist job row in Postgres if pool is provided.
    """
    if html_content is None:
        if settings is None:
            from src.config import Settings
            settings = Settings.load()
        html_content = await fetch_greenhouse_job_page(
            source_url,
            client_url=settings.playwright_service_url,
        )

    if profile is None:
        if pool is not None:
            async with pool.acquire() as conn:
                prof_row = await conn.fetchrow("SELECT * FROM profiles ORDER BY created_at DESC LIMIT 1;")
                if prof_row:
                    profile = dict(prof_row)
                    if isinstance(profile.get("links"), str):
                        profile["links"] = json.loads(profile["links"])
        if profile is None:
            master_path = Path("fixtures/master_profile.json")
            if master_path.exists():
                profile = json.loads(master_path.read_text(encoding="utf-8"))
            else:
                profile = {}

    # 3. Parse HTML
    try:
        parsed = parse_job(html_content)
    except GreenhouseParseError as err:
        logger.warning("Greenhouse parsing failed for %s: %s", source_url, err)
        # Parse failure is an in-memory outcome; DO NOT insert into DB.
        return {
            "status": "parse_failed",
            "source_url": source_url,
            "error": str(err),
        }

    # 4. LLM Analysis
    analysis = analyze_jd(parsed.raw_jd)

    # 5. Fit Scoring
    fit_score = score_fit(analysis.keywords, analysis.requirements, profile)

    # 6. Job Status
    status = "qualified" if fit_score >= fit_threshold else "disqualified"

    job_data: dict[str, Any] = {
        "source_url": source_url,
        "platform": "greenhouse",
        "title": analysis.title or parsed.title,
        "company": analysis.company or parsed.company,
        "location": analysis.location or parsed.location,
        "raw_jd": parsed.raw_jd,
        "requirements": analysis.requirements,
        "keywords": analysis.keywords,
        "fit_score": fit_score,
        "status": status,
    }

    # 7. Persist to DB
    if pool is not None:
        return await persist_job(job_data, pool)

    return job_data
