"""Step 6A.5: build_async_postgres_saver returns a durable AsyncPostgresSaver over a
dedicated psycopg3 pool; setup() is idempotent and a checkpoint round-trips."""

from __future__ import annotations

import asyncio

import asyncpg
import pytest
from langgraph.checkpoint.base import empty_checkpoint
from ulid import ULID

from src.config.settings import get_settings
from src.deep_runtime.checkpointer import build_async_postgres_saver, to_psycopg_dsn


def _db_reachable() -> bool:
    dsn = get_settings().database_url.replace("+asyncpg", "", 1)

    async def _probe() -> None:
        conn = await asyncpg.connect(dsn=dsn)
        try:
            await conn.execute("SELECT 1")
        finally:
            await conn.close()

    try:
        asyncio.run(_probe())
        return True
    except Exception:  # pragma: no cover
        return False


def test_to_psycopg_dsn_strips_asyncpg() -> None:
    """Unit test — no DB needed; always runs regardless of Postgres availability."""
    assert to_psycopg_dsn("postgresql+asyncpg://u:p@h/db") == "postgresql://u:p@h/db"
    # Idempotent: already-plain DSN is unchanged.
    assert to_psycopg_dsn("postgresql://u:p@h/db") == "postgresql://u:p@h/db"


@pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
async def test_saver_round_trips_a_checkpoint() -> None:
    """Real-DB round-trip: setup() is idempotent and aput/aget_tuple work correctly."""
    database_url = get_settings().database_url
    saver, pool = await build_async_postgres_saver(database_url)

    # Unique thread_id per run — no collision risk across parallel test runs.
    thread_id = f"t-6a5-{ULID()}"
    dsn = to_psycopg_dsn(database_url)

    try:
        cfg = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}

        # Before any writes, the thread has no checkpoint.
        assert await saver.aget_tuple(cfg) is None

        # Write a minimal checkpoint (empty_checkpoint() provides all required keys).
        checkpoint = empty_checkpoint()
        # CheckpointMetadata is total=False — only source+step+parents are needed here.
        metadata: dict = {"source": "update", "step": 0, "parents": {}}
        new_versions: dict[str, str | int | float] = {}
        await saver.aput(cfg, checkpoint, metadata, new_versions)

        # Verify the checkpoint round-trips.
        result = await saver.aget_tuple(cfg)
        assert result is not None
        assert result.config["configurable"]["thread_id"] == thread_id

    finally:
        # Best-effort cleanup of checkpoint rows so CI state stays tidy.
        try:
            conn = await asyncpg.connect(dsn=dsn)
            try:
                for tbl in ("checkpoint_blobs", "checkpoint_writes", "checkpoints"):
                    try:
                        await conn.execute(
                            f"DELETE FROM {tbl} WHERE thread_id = $1",
                            thread_id,
                        )
                    except Exception:  # noqa: BLE001 - table may not exist on first run
                        pass
            finally:
                await conn.close()
        except Exception:  # noqa: BLE001 - cleanup is best-effort
            pass
        await pool.close()


@pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
async def test_build_is_idempotent() -> None:
    """setup() can be called twice without error (CREATE ... IF NOT EXISTS semantics)."""
    database_url = get_settings().database_url
    saver1, pool1 = await build_async_postgres_saver(database_url)
    await pool1.close()

    saver2, pool2 = await build_async_postgres_saver(database_url)
    await pool2.close()

    # Both savers constructed without error — idempotency confirmed.
    assert saver1 is not saver2
