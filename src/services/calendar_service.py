"""Google Calendar service integration.

Provides an interface for scheduling interview events on Google Calendar
using OAuth 2.0 with the least-privilege scope:
    https://www.googleapis.com/auth/calendar.events

Includes an injectable MockCalendarClient for automated unit and integration tests.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import Any, Protocol

from src.config import Settings

logger = logging.getLogger(__name__)

# Least privilege scope required for managing calendar events.
CALENDAR_SCOPE = ["https://www.googleapis.com/auth/calendar.events"]


class GoogleCalendarClientProtocol(Protocol):
    """Protocol for Google Calendar event management."""

    async def create_interview_event(
        self,
        title: str,
        company: str,
        scheduled_at: datetime,
        duration_minutes: int = 45,
        description: str | None = None,
    ) -> str:
        """Create a Google Calendar interview event and return event ID."""
        ...

    async def delete_event(self, calendar_event_id: str) -> bool:
        """Delete a calendar event by ID. Returns True if deleted."""
        ...


class GoogleCalendarClient:
    """Production Google Calendar API client using OAuth credentials."""

    def __init__(self, settings: Settings | None = None) -> None:
        if settings is None:
            settings = Settings.load()

        self.settings = settings

    def _get_credentials(self) -> Any:
        """Build Google OAuth Credentials object from env settings."""
        from google.oauth2.credentials import Credentials

        if not self.settings.google_refresh_token or not self.settings.google_client_id or not self.settings.google_client_secret:
            raise RuntimeError("Google OAuth credentials missing in environment variables.")

        return Credentials(
            token=None,
            refresh_token=self.settings.google_refresh_token,
            client_id=self.settings.google_client_id,
            client_secret=self.settings.google_client_secret,
            token_uri="https://oauth2.googleapis.com/token",
            scopes=CALENDAR_SCOPE,
        )

    async def create_interview_event(
        self,
        title: str,
        company: str,
        scheduled_at: datetime,
        duration_minutes: int = 45,
        description: str | None = None,
    ) -> str:
        """Create a Google Calendar event for an interview."""
        from googleapiclient.discovery import build

        creds = self._get_credentials()
        service = build("calendar", "v3", credentials=creds)

        if scheduled_at.tzinfo is None:
            scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)

        end_at = scheduled_at + timedelta(minutes=duration_minutes)

        event_body = {
            "summary": f"Interview: {title} at {company}",
            "description": description or f"Scheduled interview for {title} position at {company}.",
            "start": {"dateTime": scheduled_at.isoformat()},
            "end": {"dateTime": end_at.isoformat()},
        }

        created_event = service.events().insert(calendarId="primary", body=event_body).execute()
        return created_event["id"]

    async def delete_event(self, calendar_event_id: str) -> bool:
        """Delete an event from Google Calendar."""
        from googleapiclient.discovery import build

        creds = self._get_credentials()
        service = build("calendar", "v3", credentials=creds)

        service.events().delete(calendarId="primary", eventId=calendar_event_id).execute()
        return True


class MockCalendarClient:
    """Mock Google Calendar client for unit and integration testing."""

    def __init__(
        self,
        event_id: str = "evt_mock_12345",
        should_fail: bool = False,
        delete_should_fail: bool = False,
    ) -> None:
        self.event_id = event_id
        self.should_fail = should_fail
        self.delete_should_fail = delete_should_fail
        self.created_events: list[dict[str, Any]] = []
        self.deleted_events: list[str] = []

    async def create_interview_event(
        self,
        title: str,
        company: str,
        scheduled_at: datetime,
        duration_minutes: int = 45,
        description: str | None = None,
    ) -> str:
        """Mock event creation."""
        if self.should_fail:
            raise RuntimeError("Google Calendar API connection error")

        event_data = {
            "event_id": self.event_id,
            "title": title,
            "company": company,
            "scheduled_at": scheduled_at,
            "duration_minutes": duration_minutes,
            "description": description,
        }
        self.created_events.append(event_data)
        return self.event_id

    async def delete_event(self, calendar_event_id: str) -> bool:
        """Mock event deletion."""
        if self.delete_should_fail:
            raise RuntimeError("Google Calendar API delete_event failed")

        self.deleted_events.append(calendar_event_id)
        return True
