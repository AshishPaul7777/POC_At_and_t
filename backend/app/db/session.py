"""Async database access.

Two connection paths, deliberately separate:

  * ``engine`` / ``session_scope`` - the pooled SQLAlchemy path for ordinary
    request and worker queries.
  * ``raw_listen_connection`` - a dedicated asyncpg connection for LISTEN.
    A listener must hold its connection open for the life of the subscription,
    which is exactly what a pool is designed to prevent, so it is never taken
    from the pool.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

_settings = get_settings()

engine = create_async_engine(
    _settings.database_url,
    echo=False,
    pool_size=10,
    max_overflow=5,
    pool_pre_ping=True,   # a laptop that slept mid-run leaves dead connections
    pool_recycle=1800,
)

SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Transactional scope. Commits on success, rolls back on any exception."""
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def raw_listen_connection() -> asyncpg.Connection:
    """A dedicated connection for LISTEN/NOTIFY, outside the pool.

    SQLAlchemy's URL carries a ``+asyncpg`` driver marker that asyncpg itself
    does not understand, so it is stripped here.
    """
    dsn = _settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    return await asyncpg.connect(dsn)


async def ping() -> dict[str, str]:
    """Liveness probe used by /health. Reports the server version and identity."""
    from sqlalchemy import text

    async with SessionLocal() as session:
        row = (
            await session.execute(
                text(
                    "SELECT current_database() AS db, current_user AS usr, "
                    "split_part(version(), ',', 1) AS ver"
                )
            )
        ).one()
        return {"database": row.db, "user": row.usr, "version": row.ver}


async def schema_state() -> dict[str, int | bool]:
    """Confirm the schema is actually applied, not merely that Postgres answers.

    A health check that only pings the socket will report green against an empty
    database, which is the failure this is here to catch.
    """
    from sqlalchemy import text

    async with SessionLocal() as session:
        tables = (
            await session.scalar(
                text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
                )
            )
        ) or 0
        has_append_event = bool(
            await session.scalar(
                text("SELECT to_regprocedure('append_event(uuid,text,text,jsonb)') IS NOT NULL")
            )
        )
        return {"tables": int(tables), "append_event_fn": has_append_event}
