"""Regression test for the worker/MCP dual-loop bug (Step 11 Phase 3).

The D4 live-e2e failure (``update_execution`` in the worker) was NOT a FastMCP
transport problem — the in-memory transport creates fresh per-connection streams,
so sharing the server object across loops is fine. The real cause was the DB
session factory.

Internal MCP tools get their session from ``_shared._get_db()``. That helper used
the module-global ``_shared._db_factory``, which ``configure_tool_servers`` set
last-writer-wins on BOTH the API thread (per chat request, ``routes_chat``) and the
worker thread (startup, ``run.py``). The engine itself is ``threading.local`` (so
each thread's asyncpg pool binds to that thread's loop), but the shared GLOBAL
pointer meant a worker background tool call could use the API thread's loop-bound
engine → ``got Future attached to a different loop``.

Fix: ``_get_db()`` resolves the thread-local ``get_session_factory()`` when no
explicit override is configured, so each thread/loop uses its own engine. Tests may
still inject an override via ``configure()``.

This test drives two concurrent loops on two threads (the ``run.py`` API+worker
topology) with NO override configured (production mode) and asserts both reach the
DB. On the pre-fix code this raised ``RuntimeError('not configured')``; on the
buggy shared-global variant it would raise the cross-loop ``Future`` error.
"""

from __future__ import annotations

import asyncio
import threading

import pytest
from sqlalchemy import text

from src.config.settings import get_settings
from src.models import database as _database
from src.tools.intelligence_server import _shared


def _db_reachable() -> bool:
    import asyncpg

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


pytestmark = pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")


def test_get_db_uses_thread_local_factory_across_concurrent_loops():
    saved = _shared._db_factory
    _shared._db_factory = None  # production mode: no injected override
    results: dict[str, object] = {}
    barrier = threading.Barrier(2)

    def run_on_own_loop(key: str) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def _use() -> str:
            # Force both loops live simultaneously (the real API+worker race).
            barrier.wait(timeout=15)
            async with _shared._get_db() as db:
                await db.execute(text("SELECT 1"))
            return "OK"

        try:
            results[key] = loop.run_until_complete(_use())
        except Exception as exc:  # noqa: BLE001
            results[key] = f"{type(exc).__name__}: {str(exc)[:160]}"
        finally:
            # Dispose this thread's own engine so its pool doesn't leak.
            try:
                loop.run_until_complete(_database.get_engine().dispose())
            except Exception:  # noqa: BLE001, S110
                pass
            loop.close()

    threads = [threading.Thread(target=run_on_own_loop, args=(k,)) for k in ("api", "worker")]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
    finally:
        _shared._db_factory = saved

    for key in ("api", "worker"):
        assert results.get(key) == "OK", f"{key} loop failed: {results.get(key)!r}"


def test_get_db_honors_injected_override():
    """A configured override (the test seam) is still used instead of thread-local."""
    from contextlib import asynccontextmanager
    from unittest.mock import MagicMock

    sentinel = MagicMock(name="override_session")

    @asynccontextmanager
    async def _fake_session():
        yield sentinel

    factory = MagicMock(return_value=_fake_session())
    saved = _shared._db_factory
    _shared._db_factory = factory
    try:

        async def _run() -> object:
            async with _shared._get_db() as db:
                return db

        got = asyncio.run(_run())
    finally:
        _shared._db_factory = saved

    assert got is sentinel
    factory.assert_called_once()
