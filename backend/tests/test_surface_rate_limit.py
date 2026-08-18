"""Tests for surface push rate limiting."""

from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_surface_pusher(mock_bus):
    """Build a SurfacePusher whose EventPublisher returns ``mock_bus``."""
    from src.orchestrator.surface_pusher import SurfacePusher

    events = MagicMock()
    events.ensure_event_bus = AsyncMock(return_value=mock_bus)
    return SurfacePusher(events, lambda: None)


class TestSurfaceRateLimit:
    @pytest.mark.asyncio
    async def test_first_push_allowed(self):
        mock_redis = AsyncMock()
        mock_redis.incr = AsyncMock(return_value=1)
        mock_redis.expire = AsyncMock()

        mock_bus = MagicMock()
        mock_bus._redis = mock_redis
        sp = _make_surface_pusher(mock_bus)

        result = await sp.check_surface_rate("user_123", "workspace")
        assert result is True
        mock_redis.incr.assert_called_once_with("muldro:surface_rate:workspace:user_123")
        mock_redis.expire.assert_called_once_with("muldro:surface_rate:workspace:user_123", 60)

    @pytest.mark.asyncio
    async def test_sixth_workspace_push_blocked(self):
        mock_redis = AsyncMock()
        mock_redis.incr = AsyncMock(return_value=6)

        mock_bus = MagicMock()
        mock_bus._redis = mock_redis
        sp = _make_surface_pusher(mock_bus)

        result = await sp.check_surface_rate("user_123", "workspace")
        assert result is False

    @pytest.mark.asyncio
    async def test_insight_rate_limit_window(self):
        mock_redis = AsyncMock()
        mock_redis.incr = AsyncMock(return_value=1)
        mock_redis.expire = AsyncMock()

        mock_bus = MagicMock()
        mock_bus._redis = mock_redis
        sp = _make_surface_pusher(mock_bus)

        result = await sp.check_surface_rate("user_123", "insight")
        assert result is True
        mock_redis.expire.assert_called_once_with("muldro:surface_rate:insight:user_123", 1800)

    @pytest.mark.asyncio
    async def test_fourth_insight_push_blocked(self):
        mock_redis = AsyncMock()
        mock_redis.incr = AsyncMock(return_value=4)

        mock_bus = MagicMock()
        mock_bus._redis = mock_redis
        sp = _make_surface_pusher(mock_bus)

        result = await sp.check_surface_rate("user_123", "insight")
        assert result is False

    @pytest.mark.asyncio
    async def test_no_redis_allows_push(self):
        sp = _make_surface_pusher(None)

        result = await sp.check_surface_rate("user_123", "workspace")
        assert result is True
