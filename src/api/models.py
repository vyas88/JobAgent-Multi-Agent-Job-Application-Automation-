"""Pydantic request and response models for FastAPI endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


# --- Jobs Endpoints ---

class AnalyzeJobRequest(BaseModel):
    source_url: str = Field(..., description="Greenhouse job posting URL")


class AnalyzeJobResponse(BaseModel):
    job: dict[str, Any] | None = None
    outcome: str
    error: str | None = None


class JobDetailResponse(BaseModel):
    id: UUID
    source_url: str
    platform: str
    title: str
    company: str
    location: str | None = None
    raw_jd: str | None = None
    requirements: list[Any] = []
    keywords: list[Any] = []
    fit_score: float | None = None
    status: str
    created_at: datetime
    updated_at: datetime


# --- Content Endpoints ---

class GenerateContentRequest(BaseModel):
    job_id: UUID
    profile_id: UUID


class GenerateContentResponse(BaseModel):
    job_id: UUID
    persisted_resume: dict[str, Any]
    persisted_letter: dict[str, Any]
    needs_review: list[str] = []


# --- Application Endpoints ---

class PrefillApplicationRequest(BaseModel):
    job_id: UUID
    profile_id: UUID
    resume_variant_id: UUID
    cover_letter_id: UUID | None = None
    page_or_url: str | None = None


class PrefillApplicationResponse(BaseModel):
    id: UUID | None = None
    job_id: UUID
    resume_variant_id: UUID | None = None
    cover_letter_id: UUID | None = None
    status: str
    review_artifact: dict[str, Any]


class ApproveApplicationRequest(BaseModel):
    reason: str | None = "Human approved via API"


class SubmitApplicationRequest(BaseModel):
    page_or_url: str | None = None


class SubmitApplicationResponse(BaseModel):
    id: UUID
    job_id: UUID
    status: str
    submitted_at: datetime | None = None


class UpdateStatusRequest(BaseModel):
    new_status: str
    reason: str | None = None


class UpdateStatusResponse(BaseModel):
    id: UUID
    job_id: UUID
    status: str
    updated_at: datetime


class ScheduleInterviewRequest(BaseModel):
    scheduled_at: datetime
    notes: str | None = None


class ScheduleInterviewResponse(BaseModel):
    interview: dict[str, Any]
    application: dict[str, Any]


class ApplicationDetailResponse(BaseModel):
    id: UUID
    job_id: UUID
    resume_variant_id: UUID | None = None
    cover_letter_id: UUID | None = None
    status: str
    review_artifact: dict[str, Any] | None = None
    approved_at: datetime | None = None
    submitted_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    status_history: list[dict[str, Any]] = []
    interviews: list[dict[str, Any]] = []
