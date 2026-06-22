"""The orchestrator reuses the shared EventBus/Redis instead of opening its own.

Regression for a polish item: build_shared() now provides a process-wide Redis
client + EventBus in the container's extras; _ensure_event_bus() should reuse
them rather than opening a second Redis connection per orchestrator.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.orchestrator.services import ServiceContainer
from src.services.event_bus import EventBus
from tests.conftest import make_mock_settings


def _make_orchestrator(services):
    from src.orchestrator.jarvis import JarvisOrchestrator

    with patch("src.orchestrator.jarvis.get_anthropic_client", return_value=MagicMock()):
        return JarvisOrchestrator(
            settings=make_mock_settings(),
            db_factory=MagicMock(),
            services=services,
        )


@pytest.mark.asyncio
async def test_ensure_event_bus_reuses_shared_bus():
    shared_redis = AsyncMock()
    shared_bus = EventBus(shared_redis)
    orch = _make_orchestrator(
        ServiceContainer(extras={"event_bus": shared_bus, "redis": shared_redis})
    )

    bus = await orch._ensure_event_bus()

    assert bus is shared_bus
    assert orch._events.event_bus_redis is shared_redis


@pytest.mark.asyncio
async def test_ensure_event_bus_falls_back_when_no_shared_bus():
    """With no shared bus in the container, it still lazily builds its own."""
    orch = _make_orchestrator(ServiceContainer())

    with patch("redis.asyncio.from_url", return_value=AsyncMock()) as from_url:
        bus = await orch._ensure_event_bus()

    assert isinstance(bus, EventBus)
    from_url.assert_called_once()
