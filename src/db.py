"""Thin async Postgres connection helper using asyncpg.

Usage:
    pool = await create_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT 1")
    await pool.close()
"""

from datetime import date, datetime
from decimal import Decimal
import json
from typing import Any
from uuid import UUID

import asyncpg

from src.config import Settings

JSONB_FIELDS: set[str] = {
    "experience",
    "education",
    "skills",
    "certifications",
    "links",
    "requirements",
    "keywords",
    "review_artifact",
    "content",
}


def _serialize_value(val: Any) -> Any:
    """Recursively convert non-JSON-serializable types (UUID, datetime, date, Decimal) to JSON-native types."""
    if isinstance(val, UUID):
        return str(val)
    if isinstance(val, (datetime, date)):
        return val.isoformat()
    if isinstance(val, Decimal):
        return float(val)
    if isinstance(val, dict):
        return {k: _serialize_value(v) for k, v in val.items()}
    if isinstance(val, list):
        return [_serialize_value(item) for item in val]
    return val


def parse_db_row(row: asyncpg.Record | dict[str, Any] | None) -> dict[str, Any]:
    """Convert an asyncpg Record or dict into a dictionary, automatically deserializing JSON strings for JSONB fields
    and serializing non-JSON-native objects (UUID, datetime, date, Decimal).
    """
    if row is None:
        return {}

    data = dict(row)
    for key, value in data.items():
        if key in JSONB_FIELDS and isinstance(value, str):
            try:
                value = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                pass
        data[key] = _serialize_value(value)
    return data


async def create_pool(
    settings: Settings | None = None,
    *,
    min_size: int = 2,
    max_size: int = 10,
) -> asyncpg.Pool:
    """Create and return an asyncpg connection pool.

    Parameters
    ----------
    settings:
        Application settings. If *None*, loaded from the environment.
    min_size / max_size:
        Pool bounds; keep small for local dev.
    """
    if settings is None:
        settings = Settings.load()

    pool = await asyncpg.create_pool(
        dsn=settings.database_url,
        min_size=min_size,
        max_size=max_size,
    )
    return pool
