"""Domain exceptions for JobAgent.

These exceptions map to explicit HTTP status codes in the FastAPI layer:
- NotFoundError -> 404 Not Found
- ForbiddenStatusTransitionError -> 409 Conflict
"""

from __future__ import annotations


class NotFoundError(Exception):
    """Raised when a requested resource (Job, Profile, Application) does not exist."""


class ForbiddenStatusTransitionError(Exception):
    """Raised when a status transition is requested via generic status endpoint that is forbidden."""
