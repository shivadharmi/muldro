"""Tests for MCP bridge initialization resilience.

Covers the four behaviors that keep worker-thread startup from stalling
on slow/hung external MCP servers:

1. Pool is usable immediately (before background discovery completes).
2. ``timeout_seconds`` actually bounds discovery.
3. HTTP ``list_tools`` is bounded per-server.
4. Discovery across servers runs in parallel.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

_SKIP_KEYS = {"PYTEST_CURRENT_TEST", "JARVIS_SKIP_MCP_BRIDGE"}


@pytest.fixture(autouse=True)
def _bypass_pytest_skip(monkeypatch):
    """``initialize_mcp_bridge`` short-circuits in test env — neutralize that
    guard so we can exercise the real wiring + background task paths.

    Direct env manipulation is insufficient: pytest re-writes
    ``PYTEST_CURRENT_TEST`` when it transitions between setup/call/teardown
    phases, clobbering any ``monkeypatch.delenv``/``setenv`` from the
    fixture. Patch the module's ``os.environ.get`` instead so the skip
    check sees ``None`` for both guard keys regardless of what pytest does."""
    import os as _os

    from src.connectors import mcp_bridge

    real_get = _os.environ.get

    def _filtered_get(key, default=None):
        if key in _SKIP_KEYS:
            return None
        return real_get(key, default)

    monkeypatch.setattr(mcp_bridge.os.environ, "get", _filtered_get)


@pytest_asyncio.fixture
async def _reset_bridge_module():
    """Clear module-level state before and after each test so one test's
    ``_session_pool`` / ``_discovery_task`` can't leak into another."""
    from src.connectors import mcp_bridge

    mcp_bridge._session_pool = None
    mcp_bridge._discovery_task = None
    yield
    task = mcp_bridge._discovery_task
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    mcp_bridge._session_pool = None
    mcp_bridge._discovery_task = None


class TestBridgeWireVsDiscover:
    """``initialize_mcp_bridge`` must wire the pool synchronously and
    (when ``defer_discovery=True``) run discovery in the background."""

    @pytest.mark.asyncio
    async def test_pool_available_before_discovery_completes(self, _reset_bridge_module):
        """``get_session_pool()`` returns non-None as soon as the call returns,
        even if discovery is still sleeping in the background."""
        from src.connectors import mcp_bridge

        slow_called = asyncio.Event()
        can_finish = asyncio.Event()

        async def slow_discovery(self):
            slow_called.set()
            await can_finish.wait()
            return 0

        with patch(
            "src.integrations.mcp_pool.WorkspaceMCPPool.initialize_from_db",
            new=slow_discovery,
        ):
            task = await mcp_bridge.initialize_mcp_bridge(
                oauth_manager=None,
                timeout_seconds=30,
                defer_discovery=True,
            )

            assert mcp_bridge.get_session_pool() is not None, (
                "Session pool must be wired before the background task finishes"
            )
            assert task is not None and not task.done(), (
                "Discovery should still be running in the background"
            )

            await asyncio.wait_for(slow_called.wait(), timeout=1)
            can_finish.set()
            await task

    @pytest.mark.asyncio
    async def test_defer_false_awaits_discovery_inline(self, _reset_bridge_module):
        """With ``defer_discovery=False``, the call must not return until
        discovery completes — used by tests that want deterministic ordering."""
        from src.connectors import mcp_bridge

        completed = []

        async def quick_discovery(self):
            await asyncio.sleep(0.01)
            completed.append(True)
            return 0

        with patch(
            "src.integrations.mcp_pool.WorkspaceMCPPool.initialize_from_db",
            new=quick_discovery,
        ):
            task = await mcp_bridge.initialize_mcp_bridge(
                oauth_manager=None,
                timeout_seconds=30,
                defer_discovery=False,
            )

            assert task is None, "defer_discovery=False should not return a task"
            assert completed, "Discovery must complete before the call returns"


class TestDiscoveryTimeout:
    """``timeout_seconds`` is a real bound, not a dead parameter."""

    @pytest.mark.asyncio
    async def test_timeout_seconds_is_enforced(self, _reset_bridge_module, caplog):
        """A hanging ``initialize_from_db`` is cut off at ``timeout_seconds``
        and the warning is logged (not re-raised)."""
        from src.connectors import mcp_bridge

        async def hanging_discovery(self):
            await asyncio.sleep(10)
            return 0

        with (
            patch(
                "src.integrations.mcp_pool.WorkspaceMCPPool.initialize_from_db",
                new=hanging_discovery,
            ),
            caplog.at_level("WARNING", logger="src.connectors.mcp_bridge"),
        ):
            start = time.monotonic()
            await mcp_bridge.initialize_mcp_bridge(
                oauth_manager=None,
                timeout_seconds=0.05,
                defer_discovery=False,
            )
            elapsed = time.monotonic() - start

        assert elapsed < 1.0, f"Timeout was not enforced (elapsed={elapsed:.2f}s)"
        assert any("discovery exceeded" in rec.message.lower() for rec in caplog.records), (
            "Expected a warning about exceeding the discovery budget"
        )


class TestHttpDiscoveryTimeout:
    """``session_pool.discover_tools`` must not hang on a slow HTTP server."""

    @pytest.mark.asyncio
    async def test_hanging_list_tools_is_bounded(self, monkeypatch, caplog):
        """A server whose ``list_tools`` never returns must produce a warning
        and an empty list within the configured budget."""
        from src.integrations import session_pool as sp

        monkeypatch.setattr(sp, "HTTP_DISCOVERY_TIMEOUT_SECONDS", 0.05)

        class _HangingClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def list_tools(self):
                await asyncio.Event().wait()  # block forever

        monkeypatch.setattr(sp, "Client", _HangingClient)

        pool = sp.UserMCPSessionPool()
        pool._server_configs[("ws_test", "slow_server")] = {
            "transport": "streamable-http",
            "url": "http://127.0.0.1:0",
            "auth_provider": "none",
        }

        with caplog.at_level("WARNING", logger="src.integrations.session_pool"):
            start = time.monotonic()
            result = await pool.discover_tools("slow_server", workspace_id="ws_test")
            elapsed = time.monotonic() - start

        assert result == []
        assert elapsed < 1.0, f"discover_tools did not respect the budget (elapsed={elapsed:.2f}s)"
        assert any("timed out" in rec.message.lower() for rec in caplog.records)


class TestParallelDiscovery:
    """HTTP discovery across multiple servers must run in parallel."""

    @pytest.mark.asyncio
    async def test_http_servers_discovered_concurrently(self):
        """With four HTTP servers each taking 0.2s, ``initialize_from_db``
        should complete well under the serial 0.8s total."""
        from src.integrations.mcp_pool import WorkspaceMCPPool

        installations = [
            MagicMock(
                workspace_id=f"ws_{i}",
                server_name=f"srv_{i}",
                transport="streamable-http",
                enabled=True,
                status="active",
                auth_provider="none",
                remote_url=f"http://srv-{i}.local",
                config_json={},
            )
            for i in range(4)
        ]

        mock_db = MagicMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = installations
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_factory_callable = MagicMock(return_value=mock_db)
        mock_factory_getter = MagicMock(return_value=mock_factory_callable)

        session_pool = MagicMock()

        async def slow_discover(server_name, *, workspace_id="", config=None):
            await asyncio.sleep(0.2)
            return []

        session_pool.discover_tools = AsyncMock(side_effect=slow_discover)

        pool = WorkspaceMCPPool(session_pool=session_pool)

        with (
            patch(
                "src.models.database.get_session_factory",
                new=mock_factory_getter,
            ),
            patch.object(pool, "_discover_stdio_schemas", new=AsyncMock(return_value=None)),
            patch(
                "src.integrations.mcp_pool._installation_to_config",
                side_effect=lambda inst: {
                    "transport": inst.transport,
                    "url": inst.remote_url,
                    "auth_provider": inst.auth_provider,
                },
            ),
        ):
            start = time.monotonic()
            count = await pool.initialize_from_db()
            elapsed = time.monotonic() - start

        assert count == 4
        assert session_pool.discover_tools.await_count == 4
        assert elapsed < 0.5, (
            f"HTTP discovery ran serially (elapsed={elapsed:.2f}s, expected <0.5s for 4×0.2s)"
        )
