# backend/core/database.py

"""Database and service connection helpers.

Provides async helpers for PostgreSQL (asyncpg pool), Redis (redis.asyncio) and Qdrant.
PostgreSQL uses a connection pool for high throughput and low latency.
"""

from __future__ import annotations

import asyncpg
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import redis.asyncio as aioredis
from qdrant_client import QdrantClient

from .config import settings

# ── PostgreSQL connection pool ──────────────────────────────────────────────────
# Using a pool (min=2, max=10) so connections are reused across requests.
# Previously every get_pg_conn() opened a fresh TCP connection — pool eliminates
# that overhead (typically 10-50 ms per new connection).
_pg_pool: asyncpg.Pool | None = None

async def init_pg_pool() -> None:
    """Create the asyncpg connection pool. Called once on application startup."""
    global _pg_pool
    if _pg_pool is not None:
        return
    _pg_pool = await asyncpg.create_pool(
        dsn=settings.DATABASE_URL,
        min_size=2,
        max_size=10,
        command_timeout=30,
    )

async def close_pg_pool() -> None:
    """Gracefully close the pool on application shutdown."""
    global _pg_pool
    if _pg_pool is not None:
        await _pg_pool.close()
        _pg_pool = None

@asynccontextmanager
async def get_pg_conn() -> AsyncGenerator[asyncpg.Connection, None]:
    """Acquire a connection from the pool and release it when done.

    Falls back to a fresh direct connection if the pool is not yet initialised
    (e.g. during startup health checks before the pool is ready).

    Yields:
        asyncpg.Connection: A pooled connection ready for queries.
    """
    global _pg_pool
    if _pg_pool is not None:
        async with _pg_pool.acquire() as conn:
            yield conn
    else:
        # Fallback: direct connection (only during very early startup)
        conn = await asyncpg.connect(dsn=settings.DATABASE_URL)
        try:
            yield conn
        finally:
            await conn.close()

# ── Redis client (asyncio) ──────────────────────────────────────────────────────
_redis_client: aioredis.Redis | None = None

def get_redis() -> aioredis.Redis:
    """Return a singleton async Redis client.

    The client is created on first call and reused thereafter.
    """
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis_client

# ── Qdrant client (synchronous) ─────────────────────────────────────────────────
_qdrant_client: QdrantClient | None = None

def get_qdrant() -> QdrantClient:
    """Return a singleton Qdrant client."""
    global _qdrant_client
    if _qdrant_client is None:
        _qdrant_client = QdrantClient(url=settings.QDRANT_URL, prefer_grpc=False)
    return _qdrant_client
