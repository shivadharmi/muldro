"""Tests for idle MCP session reaper."""

import inspect
import time
from unittest.mock import AsyncMock

from src.integrations.session_pool import SessionEntry, UserMCPSessionPool


async def test_cleanup_idle_releases_managed_process():
    pool = UserMCPSessionPool(ttl_seconds=0.0)
    ctx = AsyncMock()
    ctx.__aexit__ = AsyncMock(return_value=None)
    key = ("ws", "google-workspace", "u")
    entry = SessionEntry(
        client=AsyncMock(),
        client_ctx=ctx,
        server_name="google-workspace",
        user_id="u",
        tools={},
        managed_server="google-workspace",
    )
    entry.last_used = time.monotonic() - 10
    pool._sessions[key] = entry
    released = []
    pool._release_managed = AsyncMock(side_effect=lambda e: released.append(e.managed_server))

    removed = await pool.cleanup_idle()
    assert removed == 1
    assert released == ["google-workspace"]


def test_scheduler_tick_invokes_cleanup_idle():
    from src.services.scheduler import run_health_tick as tick_mod

    src = inspect.getsource(tick_mod)
    assert "cleanup_idle" in src
