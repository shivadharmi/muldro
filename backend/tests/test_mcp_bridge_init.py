"""Tests for MCP bridge initialization.

Covers the key behaviors of the register-only startup model:

1. Pool is wired synchronously (no background task returned).
2. ``timeout_seconds`` bounds the DB registration call.
3. ``initialize_from_db`` registers configs without calling ``discover_tools``.
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
    guard so we can exercise the real wiring paths.

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
    ``_session_pool`` can't leak into another."""
    from src.connectors import mcp_bridge

    mcp_bridge._session_pool = None
    yield
    mcp_bridge._session_pool = None


class TestBridgeWire:
    """``initialize_mcp_bridge`` must wire the pool synchronously and
    return None (no background task)."""

    @pytest.mark.asyncio
    async def test_pool_available_after_init(self, _reset_bridge_module):
        """``get_session_pool()`` returns non-None after initialize_mcp_bridge
        returns and no task is returned."""
        from src.connectors import mcp_bridge

        async def fast_registration(self):
            return 2

        with patch(
            "src.integrations.mcp_pool.WorkspaceMCPPool.initialize_from_db",
            new=fast_registration,
        ):
            result = await mcp_bridge.initialize_mcp_bridge(
                oauth_manager=None,
                timeout_seconds=30,
            )

        assert mcp_bridge.get_session_pool() is not None, (
            "Session pool must be wired after initialize_mcp_bridge returns"
        )
        assert result is None, "initialize_mcp_bridge must return None (no background task)"

    @pytest.mark.asyncio
    async def test_returns_none_not_task(self, _reset_bridge_module):
        """initialize_mcp_bridge must return None, not an asyncio.Task."""
        from src.connectors import mcp_bridge

        async def instant_register(self):
            return 0

        with patch(
            "src.integrations.mcp_pool.WorkspaceMCPPool.initialize_from_db",
            new=instant_register,
        ):
            result = await mcp_bridge.initialize_mcp_bridge(oauth_manager=None)

        assert not isinstance(result, asyncio.Task), "initialize_mcp_bridge must not return a Task"


class TestRegistrationTimeout:
    """``timeout_seconds`` is a real bound on DB config registration."""

    @pytest.mark.asyncio
    async def test_timeout_seconds_is_enforced(self, _reset_bridge_module, caplog):
        """A hanging ``initialize_from_db`` is cut off at ``timeout_seconds``
        and the warning is logged (not re-raised)."""
        from src.connectors import mcp_bridge

        async def hanging_registration(self):
            await asyncio.sleep(10)
            return 0

        with (
            patch(
                "src.integrations.mcp_pool.WorkspaceMCPPool.initialize_from_db",
                new=hanging_registration,
            ),
            caplog.at_level("WARNING", logger="src.connectors.mcp_bridge"),
        ):
            start = time.monotonic()
            await mcp_bridge.initialize_mcp_bridge(
                oauth_manager=None,
                timeout_seconds=0.05,
            )
            elapsed = time.monotonic() - start

        assert elapsed < 1.0, f"Timeout was not enforced (elapsed={elapsed:.2f}s)"
        assert any(
            "registration exceeded" in rec.message.lower()
            or "lazy on first use" in rec.message.lower()
            for rec in caplog.records
        ), "Expected a warning about exceeding the registration budget"


class TestNoEagerDiscovery:
    """initialize_from_db must not call discover_tools or spawn sessions."""

    @pytest.mark.asyncio
    async def test_initialize_from_db_registers_only(self):
        """initialize_from_db must register configs without calling discover_tools."""
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
                config=None,
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
        session_pool.discover_tools = AsyncMock()
        session_pool.register_server_config = MagicMock()

        pool = WorkspaceMCPPool(session_pool=session_pool)

        with (
            patch("src.models.database.get_session_factory", new=mock_factory_getter),
            patch(
                "src.integrations.mcp_pool._installation_to_config",
                side_effect=lambda inst: {
                    "transport": inst.transport,
                    "url": inst.remote_url,
                    "auth_provider": inst.auth_provider,
                },
            ),
        ):
            count = await pool.initialize_from_db()

        assert count == 4
        # Crucially: no eager discovery calls
        session_pool.discover_tools.assert_not_called()
