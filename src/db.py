"""Thin async Postgres connection helper using asyncpg.

Usage:
    pool = await create_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT 1")
    await pool.close()
"""

from __future__ import annotations

import asyncpg

from src.config import Settings


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
