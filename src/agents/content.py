"""Resume and Content Agent.

Responsibilities (ARCHITECTURE.md §2.2):
- Tailor master profile to target job keywords without fabrication.
- Generate role-specific cover letter grounded strictly in profile facts.
- Enforce deterministic number normalization & context verification.
- Collect soft claims into a needs_review list without silently passing.
- Persist resume_variants (JSONB content + needs_review) and cover_letters (TEXT content) in Postgres.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import re
from typing import Any, Union

import asyncpg
from pydantic import BaseModel, Field

from src.llm.openai_client import call_openai

logger = logging.getLogger(__name__)


class FabricationError(Exception):
    """Raised when generated resume or cover letter contains hard fabricated content."""

    def __init__(self, violations: list[str]) -> None:
        super().__init__(f"Fabrication check failed: {'; '.join(violations)}")
        self.violations = violations


# --- Pydantic Output Schemas ---------------------------------------------

class TailoredExperienceEntry(BaseModel):
    title: str = Field(description="Job title from master profile")
    company: str = Field(description="Company name from master profile")
    dates: str = Field(description="Employment dates from master profile")
    bullets: list[str] = Field(description="Rephrased/reordered bullet points matching target keywords")


class TailoredEducationEntry(BaseModel):
    degree: str = Field(description="Degree from master profile")
    institution: str = Field(description="Institution from master profile")
    year: Union[int, str] = Field(description="Graduation year from master profile")


class TailoredResumeResult(BaseModel):
    summary: str = Field(description="Tailored professional summary")
    experience: list[TailoredExperienceEntry] = Field(description="Tailored experience entries")
    skills: list[str] = Field(description="Skills reordered by job relevance")
    education: list[TailoredEducationEntry] = Field(description="Education history")
    certifications: list[str] = Field(default_factory=list, description="Certifications")
    needs_review: list[str] = Field(default_factory=list, description="Flagged soft claims needing human review")


class CoverLetterResult(BaseModel):
    cover_letter: str = Field(description="Full text of the cover letter")
    company_referenced: str = Field(description="Target company referenced")
    title_referenced: str = Field(description="Target title referenced")
    key_points: list[str] = Field(default_factory=list, description="Highlights emphasized")
    needs_review: list[str] = Field(default_factory=list, description="Flagged soft claims needing human review")


# --- Number Normalization & Verification Logic -----------------------------

WORD_TO_NUM: dict[str, float] = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10
}


def normalize_numeric_value(raw: str) -> tuple[float | None, str]:
    """Normalize numeric string to standard float value and canonical unit/type.

    Equivalences:
    - "2M+", "2,000,000", "2 million", "2m" -> (2000000.0, "")
    - "40%", "40 percent" -> (40.0, "%")
    - "four", "4" -> (4.0, "")
    - "$2M" -> (2000000.0, "$")
    """
    clean = raw.strip().lower().replace(",", "")

    # Determine unit
    unit = ""
    if "%" in clean or "percent" in clean:
        unit = "%"
    elif "$" in clean or "usd" in clean or "dollar" in clean:
        unit = "$"
    elif "ms" in clean:
        unit = "ms"
    elif "gb" in clean:
        unit = "gb"
    elif "tb" in clean:
        unit = "tb"
    elif "mb" in clean:
        unit = "mb"

    # Check word numbers first
    for word, val in WORD_TO_NUM.items():
        if word in clean:
            return float(val), unit

    match = re.search(r"(\d+(?:\.\d+)?)", clean)
    if not match:
        return None, ""

    val = float(match.group(1))

    # Multipliers (only if NOT 'ms' / 'mb')
    if "ms" not in clean and "mb" not in clean:
        if "million" in clean or re.search(r"\b\d+m\b", clean) or re.search(r"\b\d+m\+", clean):
            val *= 1_000_000
        elif "billion" in clean or re.search(r"\b\d+b\b", clean) or re.search(r"\b\d+b\+", clean):
            val *= 1_000_000_000
        elif "thousand" in clean or re.search(r"\b\d+k\b", clean) or re.search(r"\b\d+k\+", clean):
            val *= 1_000

    return val, unit


@dataclass(frozen=True)
class NumericClaim:
    raw_str: str
    norm_val: float
    unit: str
    context_tokens: set[str]


def extract_numeric_claims(text: str) -> list[NumericClaim]:
    """Extract all numeric claims with their normalized values, units, and context tokens."""
    claims: list[NumericClaim] = []

    pattern = re.compile(
        r"(\$?\d+(?:,\d{3})*(?:\.\d+)?\s*(?:million|m|billion|b|thousand|k)?\s*\+?\s*(?:percent|%|ms|s|gb|tb|mb)?|\b(?:one|two|three|four|five|six|seven|eight|nine|ten)\b)",
        re.IGNORECASE,
    )

    for match in re.finditer(pattern, text):
        start = match.start()
        # Skip token if it is part of an identifier like p95 or v2
        if start > 0 and text[start - 1].isalpha():
            continue

        raw_match = match.group(0).strip()
        if not raw_match or (raw_match.isalpha() and raw_match.lower() not in WORD_TO_NUM):
            continue

        norm_val, unit = normalize_numeric_value(raw_match)
        if norm_val is None:
            continue

        end_char = match.end()

        # Restrict context tokens to current line/sentence segment to prevent cross-bullet leaks
        before_segment = re.split(r"[\n.;]", text[:start])[-1]
        after_segment = re.split(r"[\n.;]", text[end_char:])[0]

        before_words = re.findall(r"\b[a-zA-Z0-9]+\b", before_segment.lower())[-3:]
        after_words = re.findall(r"\b[a-zA-Z0-9]+\b", after_segment.lower())[:3]

        context_tokens = set(before_words + after_words)

        claims.append(
            NumericClaim(
                raw_str=raw_match,
                norm_val=norm_val,
                unit=unit,
                context_tokens=context_tokens,
            )
        )

    return claims


def extract_profile_text(profile: dict[str, Any]) -> str:
    """Combine all text fields in master profile into a single canonical string."""
    parts: list[str] = []
    if summary := profile.get("summary"):
        parts.append(str(summary))

    for exp in profile.get("experience", []):
        if title := exp.get("title"):
            parts.append(str(title))
        if company := exp.get("company"):
            parts.append(str(company))
        for bullet in exp.get("bullets", []):
            parts.append(str(bullet))

    for edu in profile.get("education", []):
        if degree := edu.get("degree"):
            parts.append(str(degree))
        if inst := edu.get("institution"):
            parts.append(str(inst))

    for cert in profile.get("certifications", []):
        parts.append(str(cert))

    return "\n".join(parts)


def verify_resume_no_fabrication(
    resume: TailoredResumeResult,
    profile: dict[str, Any],
) -> tuple[bool, list[str], list[str]]:
    """Verify generated resume against master profile.

    Returns:
        (is_valid, hard_violations, needs_review_items)
    """
    hard_violations: list[str] = []
    needs_review: list[str] = []

    profile_text = extract_profile_text(profile)
    profile_numeric_claims = extract_numeric_claims(profile_text)

    # 1. Company & Title check
    master_exp = profile.get("experience", [])
    valid_companies = {exp.get("company", "").strip().lower() for exp in master_exp}
    valid_titles = {exp.get("title", "").strip().lower() for exp in master_exp}

    for exp in resume.experience:
        if exp.company.strip().lower() not in valid_companies:
            hard_violations.append(f"Invented employer: '{exp.company}' not in master profile.")
        if exp.title.strip().lower() not in valid_titles:
            hard_violations.append(f"Invented job title: '{exp.title}' not in master profile.")

    # 2. Education & Certifications check
    master_edu = profile.get("education", [])
    valid_insts = {edu.get("institution", "").strip().lower() for edu in master_edu}
    for edu in resume.education:
        if edu.institution.strip().lower() not in valid_insts:
            hard_violations.append(f"Invented institution: '{edu.institution}' not in master profile.")

    master_certs = {str(c).strip().lower() for c in profile.get("certifications", [])}
    for cert in resume.certifications:
        if cert.strip().lower() not in master_certs:
            hard_violations.append(f"Invented certification: '{cert}' not in master profile.")

    # 3. Numeric Claim Verification
    resume_text = resume.summary + "\n" + "\n".join(
        b for exp in resume.experience for b in exp.bullets
    )
    generated_numeric_claims = extract_numeric_claims(resume_text)

    for gen_claim in generated_numeric_claims:
        # Match normalized numeric value against profile claims
        matching_profile_claims = [
            pc for pc in profile_numeric_claims if abs(pc.norm_val - gen_claim.norm_val) < 0.01
        ]

        if not matching_profile_claims:
            hard_violations.append(
                f"Invented numeric metric: '{gen_claim.raw_str}' not present in master profile."
            )
        else:
            # Number matches; verify unit and context match
            context_match = False
            for pc in matching_profile_claims:
                if pc.unit == gen_claim.unit:
                    overlap = pc.context_tokens.intersection(gen_claim.context_tokens)
                    if overlap or (not pc.context_tokens and not gen_claim.context_tokens):
                        context_match = True
                        break

            if not context_match:
                needs_review.append(
                    f"Numeric metric '{gen_claim.raw_str}' exists in profile, but unit/context differs "
                    f"(generated context tokens: {sorted(list(gen_claim.context_tokens))})."
                )

    is_valid = len(hard_violations) == 0
    return is_valid, hard_violations, needs_review


def verify_cover_letter_no_fabrication(
    cover_letter_res: CoverLetterResult,
    profile: dict[str, Any],
    job: dict[str, Any],
) -> tuple[bool, list[str], list[str]]:
    """Verify generated cover letter against master profile and job details.

    Returns:
        (is_valid, hard_violations, needs_review_items)
    """
    hard_violations: list[str] = []
    needs_review: list[str] = []

    profile_text = extract_profile_text(profile)
    profile_numeric_claims = extract_numeric_claims(profile_text)

    # Add job requirements metrics if mentioned
    job_req_text = " ".join(job.get("requirements", []))
    job_numeric_claims = extract_numeric_claims(job_req_text)
    allowed_numeric_claims = profile_numeric_claims + job_numeric_claims

    letter_text = cover_letter_res.cover_letter
    letter_numeric_claims = extract_numeric_claims(letter_text)

    for gen_claim in letter_numeric_claims:
        matching_claims = [
            pc for pc in allowed_numeric_claims if abs(pc.norm_val - gen_claim.norm_val) < 0.01
        ]

        if not matching_claims:
            hard_violations.append(
                f"Invented numeric metric in cover letter: '{gen_claim.raw_str}'."
            )
        else:
            context_match = False
            for pc in matching_claims:
                if pc.unit == gen_claim.unit:
                    overlap = pc.context_tokens.intersection(gen_claim.context_tokens)
                    if overlap or (not pc.context_tokens and not gen_claim.context_tokens):
                        context_match = True
                        break

            if not context_match:
                needs_review.append(
                    f"Cover letter metric '{gen_claim.raw_str}' context differs from master profile/job."
                )

    is_valid = len(hard_violations) == 0
    return is_valid, hard_violations, needs_review


# --- Agent Core Functions -----------------------------------------------

def generate_tailored_resume(
    job: dict[str, Any],
    profile: dict[str, Any],
) -> TailoredResumeResult:
    """Call OpenAI to generate a tailored resume variant from master profile."""
    user_prompt = (
        f"TARGET JOB DETAILS:\n"
        f"Title: {job.get('title')}\n"
        f"Company: {job.get('company')}\n"
        f"Keywords: {json.dumps(job.get('keywords', []))}\n"
        f"Requirements: {json.dumps(job.get('requirements', []))}\n\n"
        f"CANDIDATE MASTER PROFILE:\n"
        f"{json.dumps(profile, indent=2)}\n"
    )

    resume_res = call_openai(
        prompt_name="resume_tailor",
        user_message=user_prompt,
        response_model=TailoredResumeResult,
    )

    is_valid, hard_violations, needs_review = verify_resume_no_fabrication(resume_res, profile)
    if not is_valid:
        raise FabricationError(hard_violations)

    resume_res.needs_review.extend(needs_review)
    return resume_res


def generate_cover_letter(
    job: dict[str, Any],
    profile: dict[str, Any],
) -> CoverLetterResult:
    """Call OpenAI to generate a role-specific cover letter."""
    user_prompt = (
        f"TARGET JOB DETAILS:\n"
        f"Title: {job.get('title')}\n"
        f"Company: {job.get('company')}\n"
        f"Keywords: {json.dumps(job.get('keywords', []))}\n"
        f"Requirements: {json.dumps(job.get('requirements', []))}\n\n"
        f"CANDIDATE MASTER PROFILE:\n"
        f"{json.dumps(profile, indent=2)}\n"
    )

    letter_res = call_openai(
        prompt_name="cover_letter",
        user_message=user_prompt,
        response_model=CoverLetterResult,
    )

    is_valid, hard_violations, needs_review = verify_cover_letter_no_fabrication(
        letter_res, profile, job
    )
    if not is_valid:
        raise FabricationError(hard_violations)

    letter_res.needs_review.extend(needs_review)
    return letter_res


# --- Persistence Functions ----------------------------------------------

async def persist_resume_variant(
    job_id: str,
    profile_id: str,
    content: dict[str, Any],
    file_path: str | None,
    pool: asyncpg.Pool,
) -> dict[str, Any]:
    """Persist a tailored resume variant to resume_variants table in Postgres."""
    query = """
        INSERT INTO resume_variants (job_id, profile_id, content, file_path)
        VALUES ($1::uuid, $2::uuid, $3::jsonb, $4)
        RETURNING id, job_id, profile_id, content, file_path, created_at;
    """

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            query,
            job_id,
            profile_id,
            json.dumps(content),
            file_path,
        )

    return dict(row) if row else {}


async def persist_cover_letter(
    job_id: str,
    profile_id: str,
    content_text: str,
    pool: asyncpg.Pool,
) -> dict[str, Any]:
    """Persist a cover letter to cover_letters table in Postgres."""
    query = """
        INSERT INTO cover_letters (job_id, profile_id, content)
        VALUES ($1::uuid, $2::uuid, $3)
        RETURNING id, job_id, profile_id, content, created_at;
    """

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            query,
            job_id,
            profile_id,
            content_text,
        )

    return dict(row) if row else {}


async def generate_and_persist_content(
    job_id: str,
    profile_id: str,
    job: dict[str, Any],
    profile: dict[str, Any],
    pool: asyncpg.Pool | None = None,
) -> dict[str, Any]:
    """Orchestrate Phase 2 content generation and optional Postgres persistence."""
    resume_res = generate_tailored_resume(job, profile)
    letter_res = generate_cover_letter(job, profile)

    resume_content = resume_res.model_dump()
    letter_text = letter_res.cover_letter

    if letter_res.needs_review:
        review_block = "\n\n[Needs Review:\n" + "\n".join(f"- {nr}" for nr in letter_res.needs_review) + "]"
        letter_text += review_block

    persisted_resume = None
    persisted_letter = None

    if pool is not None:
        persisted_resume = await persist_resume_variant(job_id, profile_id, resume_content, None, pool)
        persisted_letter = await persist_cover_letter(job_id, profile_id, letter_text, pool)

    return {
        "job_id": job_id,
        "profile_id": profile_id,
        "resume": resume_content,
        "cover_letter": letter_text,
        "needs_review": resume_res.needs_review + letter_res.needs_review,
        "persisted_resume": persisted_resume,
        "persisted_letter": persisted_letter,
    }
