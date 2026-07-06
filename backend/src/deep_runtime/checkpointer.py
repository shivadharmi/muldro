"""Durable langgraph checkpointer for the deep runtime (Step 6A.5).

Builds an AsyncPostgresSaver over a dedicated small psycopg3 connection pool, constructed
once at app lifespan. Replaces the per-call MemorySaver so a future 6B interrupt() can pause
a chat turn and resume after a separate approval round-trip. In 6A.5 no interrupt fires, so
the saver is durable-but-inert. The psycopg3 pool is separate from the app's asyncpg pool
(different drivers, same Postgres); it is small because AsyncPostgresSaver serializes DB ops
behind one asyncio.Lock.
"""

from __future__ import annotations

import logging

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)


def to_psycopg_dsn(database_url: str) -> str:
    """SQLAlchemy async DSN → plain psycopg3 DSN (strip the +asyncpg driver suffix)."""
    return database_url.replace("+asyncpg", "", 1)


async def build_async_postgres_saver(
    database_url: str,
) -> tuple[AsyncPostgresSaver, AsyncConnectionPool]:
    """Open a small psycopg3 pool + an AsyncPostgresSaver over it; run setup() once.

    Accepts either a plain ``postgresql://`` DSN or a SQLAlchemy async
    ``postgresql+asyncpg://`` DSN — the ``+asyncpg`` driver tag is stripped before
    passing the DSN to psycopg3.

    Returns ``(saver, pool)``. The caller MUST ``await pool.close()`` on shutdown.
    Construct this inside the running event loop: AsyncPostgresSaver builds an
    ``asyncio.Lock`` at ``__init__`` time.
    """
    pool = AsyncConnectionPool(
        to_psycopg_dsn(database_url),
        min_size=1,
        max_size=4,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    await pool.open()
    saver = AsyncPostgresSaver(pool)
    await saver.setup()
    logger.info("[deep_runtime] durable AsyncPostgresSaver initialized")
    return saver, pool
