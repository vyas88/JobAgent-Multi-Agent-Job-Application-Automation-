"""FastAPI application layer exposing pipeline endpoints for agent orchestration."""

from __future__ import annotations

from contextlib import asynccontextmanager
import json
import logging
from uuid import UUID

import asyncpg
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse

from src.agents import application, content, discovery, tracking
from src.agents.application import ApprovalError, FormFillError
from src.agents.content import FabricationError
from src.agents.tracking import CalendarServiceError, DatabaseError, OrphanedCalendarEventError
from src.api.dependencies import get_calendar_client, get_db_pool, verify_api_key
from src.api.models import (
    AnalyzeJobRequest,
    AnalyzeJobResponse,
    ApplicationDetailResponse,
    ApproveApplicationRequest,
    GenerateContentRequest,
    GenerateContentResponse,
    JobDetailResponse,
    PrefillApplicationRequest,
    PrefillApplicationResponse,
    ScheduleInterviewRequest,
    ScheduleInterviewResponse,
    SubmitApplicationRequest,
    SubmitApplicationResponse,
    UpdateStatusRequest,
    UpdateStatusResponse,
)
from src.config import Settings
from src.db import parse_db_row
from src.exceptions import ForbiddenStatusTransitionError, NotFoundError
from src.services.calendar_service import GoogleCalendarClient, GoogleCalendarClientProtocol
from src.services.playwright_service import GreenhouseParseError

logger = logging.getLogger(__name__)

FORBIDDEN_GENERIC_STATUS_TRANSITIONS: set[str] = {
    "approved",
    "submitted",
    "submit_uncertain",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager creating DB pool and calendar client."""
    settings = Settings.load()
    app.state.pool = await asyncpg.create_pool(settings.database_url)
    app.state.calendar_client = GoogleCalendarClient(settings)
    yield
    await app.state.pool.close()


app = FastAPI(
    title="JobAgent Pipeline Orchestration API",
    version="0.1.0",
    lifespan=lifespan,
)


# --- Exception Handlers ---

@app.exception_handler(ApprovalError)
async def approval_error_handler(request: Request, exc: ApprovalError):
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={"detail": str(exc), "error_type": "approval_refused"},
    )


@app.exception_handler(ForbiddenStatusTransitionError)
async def forbidden_status_handler(request: Request, exc: ForbiddenStatusTransitionError):
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={"detail": str(exc), "error_type": "forbidden_status_transition"},
    )


@app.exception_handler(FabricationError)
async def fabrication_error_handler(request: Request, exc: FabricationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "detail": str(exc),
            "error_type": "fabrication_detected",
            "violations": exc.violations,
        },
    )


@app.exception_handler(NotFoundError)
async def not_found_error_handler(request: Request, exc: NotFoundError):
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"detail": str(exc), "error_type": "not_found"},
    )


@app.exception_handler(GreenhouseParseError)
@app.exception_handler(FormFillError)
async def parse_fill_error_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": str(exc), "error_type": "parse_or_fill_error"},
    )


@app.exception_handler(ValueError)
async def validation_error_handler(request: Request, exc: ValueError):
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": str(exc), "error_type": "validation_error"},
    )


@app.exception_handler(OrphanedCalendarEventError)
async def orphaned_calendar_event_handler(request: Request, exc: OrphanedCalendarEventError):
    # Extract event_id safely from exception
    msg = str(exc)
    event_id = msg.split("'")[1] if "'" in msg else "unknown"
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": str(exc),
            "error_type": "orphaned_calendar_event",
            "calendar_event_id": event_id,
        },
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled API exception: %s", exc, exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error", "error_type": "internal_error"},
    )


# --- Endpoint Handlers ---

@app.post(
    "/jobs/analyze",
    response_model=AnalyzeJobResponse,
    dependencies=[Depends(verify_api_key)],
)
async def analyze_job_endpoint(
    req: AnalyzeJobRequest,
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    """Analyze job posting via Discovery Agent and persist row."""
    settings = Settings.load()
    res = await discovery.analyze_job(req.source_url, pool=pool, settings=settings)
    if res.get("status") == "parse_failed":
        return {
            "job": None,
            "outcome": "parse_failed",
            "error": res.get("error"),
        }

    return {
        "job": res,
        "outcome": res.get("status", "success"),
        "error": None,
    }


@app.post(
    "/content/generate",
    response_model=GenerateContentResponse,
    dependencies=[Depends(verify_api_key)],
)
async def generate_content_endpoint(
    req: GenerateContentRequest,
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    """Generate tailored resume and cover letter via Content Agent."""
    # Fetch job and profile from DB
    async with pool.acquire() as conn:
        job_row = await conn.fetchrow("SELECT * FROM jobs WHERE id = $1::uuid;", str(req.job_id))
        if not job_row:
            raise NotFoundError(f"Job {req.job_id} not found.")

        profile_row = await conn.fetchrow("SELECT * FROM profiles WHERE id = $1::uuid;", str(req.profile_id))
        if not profile_row:
            raise NotFoundError(f"Profile {req.profile_id} not found.")

    res = await content.generate_and_persist_content(
        job_id=str(req.job_id),
        profile_id=str(req.profile_id),
        job=parse_db_row(job_row),
        profile=parse_db_row(profile_row),
        pool=pool,
    )
    return res


@app.post(
    "/applications/prefill",
    response_model=PrefillApplicationResponse,
    dependencies=[Depends(verify_api_key)],
)
async def prefill_application_endpoint(
    req: PrefillApplicationRequest,
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    """Pre-fill Greenhouse form via Application Agent (STOPS BEFORE SUBMIT)."""
    async with pool.acquire() as conn:
        job_row = await conn.fetchrow("SELECT * FROM jobs WHERE id = $1::uuid;", str(req.job_id))
        if not job_row:
            raise NotFoundError(f"Job {req.job_id} not found.")

        profile_row = await conn.fetchrow("SELECT * FROM profiles WHERE id = $1::uuid;", str(req.profile_id))
        if not profile_row:
            raise NotFoundError(f"Profile {req.profile_id} not found.")

        res_row = await conn.fetchrow("SELECT * FROM resume_variants WHERE id = $1::uuid;", str(req.resume_variant_id))
        if not res_row:
            raise NotFoundError(f"ResumeVariant {req.resume_variant_id} not found.")

        cl_row = None
        if req.cover_letter_id:
            cl_row = await conn.fetchrow("SELECT * FROM cover_letters WHERE id = $1::uuid;", str(req.cover_letter_id))
            if not cl_row:
                raise NotFoundError(f"CoverLetter {req.cover_letter_id} not found.")

    res = await application.prefill_application(
        job_id=str(req.job_id),
        profile_id=str(req.profile_id),
        resume_variant_id=str(req.resume_variant_id),
        cover_letter_id=str(req.cover_letter_id) if req.cover_letter_id else None,
        profile=parse_db_row(profile_row),
        job=parse_db_row(job_row),
        resume_variant=parse_db_row(res_row),
        cover_letter=parse_db_row(cl_row) if cl_row else None,
        pool=pool,
        page_or_url=req.page_or_url,
    )
    return parse_db_row(res)


@app.post(
    "/applications/{application_id}/approve",
    response_model=UpdateStatusResponse,
    dependencies=[Depends(verify_api_key)],
)
async def approve_application_endpoint(
    application_id: UUID,
    req: ApproveApplicationRequest = ApproveApplicationRequest(),
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    """Explicit human approval endpoint: transitions status ONLY from 'pending_review' -> 'approved'."""
    app_str = str(application_id)
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("SELECT status FROM applications WHERE id = $1::uuid;", app_str)
            if not row:
                raise NotFoundError(f"Application {app_str} not found.")

            curr_status = row["status"]
            if curr_status != "pending_review":
                raise ApprovalError(
                    f"Application {app_str} is in status '{curr_status}', not 'pending_review'. Approval refused."
                )

            # Transition status to approved
            updated_app = await tracking.update_status(
                application_id=app_str,
                new_status="approved",
                pool=pool,
                reason=req.reason or "Human approved via API",
                conn=conn,
            )
            # Set approved_at timestamp
            await conn.execute("UPDATE applications SET approved_at = now() WHERE id = $1::uuid;", app_str)

    return updated_app


@app.post(
    "/applications/{application_id}/submit",
    response_model=SubmitApplicationResponse,
    dependencies=[Depends(verify_api_key)],
)
async def submit_application_endpoint(
    application_id: UUID,
    req: SubmitApplicationRequest = SubmitApplicationRequest(),
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    """Execute submission via atomic approval gate (Refuses unless status is 'approved')."""
    res = await application.submit_application(
        application_id=str(application_id),
        pool=pool,
        page_or_url=req.page_or_url,
    )
    return res


@app.post(
    "/applications/{application_id}/status",
    response_model=UpdateStatusResponse,
    dependencies=[Depends(verify_api_key)],
)
async def update_status_endpoint(
    application_id: UUID,
    req: UpdateStatusRequest,
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    """Update application status generically (Forbids setting approved, submitted, submit_uncertain)."""
    if req.new_status in FORBIDDEN_GENERIC_STATUS_TRANSITIONS:
        raise ForbiddenStatusTransitionError(
            f"Status '{req.new_status}' cannot be set via generic status endpoint. Use dedicated workflow endpoint."
        )

    res = await tracking.update_status(
        application_id=str(application_id),
        new_status=req.new_status,
        pool=pool,
        reason=req.reason,
    )
    return res


@app.post(
    "/applications/{application_id}/interview",
    response_model=ScheduleInterviewResponse,
    dependencies=[Depends(verify_api_key)],
)
async def schedule_interview_endpoint(
    application_id: UUID,
    req: ScheduleInterviewRequest,
    pool: asyncpg.Pool = Depends(get_db_pool),
    calendar_client: GoogleCalendarClientProtocol = Depends(get_calendar_client),
):
    """Schedule interview: creates Google Calendar event, inserts interviews row, moves status to 'interview'."""
    res = await tracking.schedule_interview(
        application_id=str(application_id),
        scheduled_at=req.scheduled_at,
        pool=pool,
        calendar_client=calendar_client,
        notes=req.notes,
    )
    return res


@app.get(
    "/applications/{application_id}",
    response_model=ApplicationDetailResponse,
)
async def get_application_endpoint(
    application_id: UUID,
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    """Get full application details including status history and interview records."""
    app_str = str(application_id)
    async with pool.acquire() as conn:
        app_row = await conn.fetchrow("SELECT * FROM applications WHERE id = $1::uuid;", app_str)
        if not app_row:
            raise NotFoundError(f"Application {app_str} not found.")

        hist_rows = await conn.fetch(
            "SELECT * FROM status_history WHERE application_id = $1::uuid ORDER BY changed_at ASC;",
            app_str,
        )
        int_rows = await conn.fetch(
            "SELECT * FROM interviews WHERE application_id = $1::uuid ORDER BY scheduled_at ASC;",
            app_str,
        )

    app_dict = parse_db_row(app_row)
    app_dict["status_history"] = [parse_db_row(r) for r in hist_rows]
    app_dict["interviews"] = [parse_db_row(r) for r in int_rows]
    return app_dict


@app.get(
    "/jobs/{job_id}",
    response_model=JobDetailResponse,
)
async def get_job_endpoint(
    job_id: UUID,
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    """Get job details by ID."""
    job_str = str(job_id)
    async with pool.acquire() as conn:
        job_row = await conn.fetchrow("SELECT * FROM jobs WHERE id = $1::uuid;", job_str)
        if not job_row:
            raise NotFoundError(f"Job {job_str} not found.")

    return parse_db_row(job_row)
