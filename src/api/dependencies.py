"""FastAPI dependencies for authentication, settings, DB connection pool, and Calendar client."""

from __future__ import annotations

import asyncpg
from fastapi import HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

from src.config import Settings
from src.services.calendar_service import GoogleCalendarClientProtocol

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def get_settings() -> Settings:
    """Get application settings."""
    return Settings.load()


async def verify_api_key(
    request: Request,
    api_key: str | None = Security(api_key_header),
) -> str:
    """Verify X-API-Key header against configured API_KEY setting.

    Raises 401 Unauthorized if missing or invalid.
    """
    settings = get_settings()
    expected_key = settings.api_key

    if not api_key or api_key != expected_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API Key",
        )
    return api_key


def get_db_pool(request: Request) -> asyncpg.Pool:
    """Dependency getter for asyncpg connection pool."""
    pool: asyncpg.Pool | None = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database connection pool is not initialized.",
        )
    return pool


def get_calendar_client(request: Request) -> GoogleCalendarClientProtocol:
    """Dependency getter for Google Calendar client."""
    client: GoogleCalendarClientProtocol | None = getattr(request.app.state, "calendar_client", None)
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Google Calendar client is not initialized.",
        )
    return client
