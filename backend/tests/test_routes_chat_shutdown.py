"""shutdown_orchestrator releases the process-wide orchestrator + shared Redis."""

from unittest.mock import AsyncMock

import pytest

from src.api import routes_chat


@pytest.mark.asyncio
async def test_shutdown_orchestrator_awaits_tasks_and_closes_shared_redis():
    orch = AsyncMock()
    shared_redis = AsyncMock()

    routes_chat._orchestrator = orch
    routes_chat._module_shared_redis = [shared_redis]

    await routes_chat.shutdown_orchestrator()

    orch.shutdown.assert_awaited_once()
    shared_redis.aclose.assert_awaited_once()
    assert routes_chat._orchestrator is None
    assert routes_chat._module_shared_redis == []


@pytest.mark.asyncio
async def test_shutdown_orchestrator_is_a_noop_when_never_built():
    routes_chat._orchestrator = None
    routes_chat._module_shared_redis = []

    await routes_chat.shutdown_orchestrator()  # must not raise

    assert routes_chat._orchestrator is None
