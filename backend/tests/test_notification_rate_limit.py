"""Tests for notification rate limiting in Notifier."""

from unittest.mock import AsyncMock

import pytest


class TestRateLimiting:
    """Test per-surface rate caps."""

    @pytest.mark.asyncio
    async def test_first_notification_allowed(self):
        from src.services.notifier import Notifier

        redis = AsyncMock()
        redis.incr.return_value = 1
        registry = AsyncMock()
        notifier = Notifier(surface_registry=registry, redis=redis)
        allowed = await notifier._check_rate_limit("user1", "telegram")
        assert allowed is True
        redis.incr.assert_called_once_with("notifier:rate:user1:telegram")
        redis.expire.assert_called_once_with("notifier:rate:user1:telegram", 3600)

    @pytest.mark.asyncio
    async def test_telegram_blocked_after_5(self):
        from src.services.notifier import Notifier

        redis = AsyncMock()
        redis.incr.return_value = 6
        registry = AsyncMock()
        notifier = Notifier(surface_registry=registry, redis=redis)
        allowed = await notifier._check_rate_limit("user1", "telegram")
        assert allowed is False

    @pytest.mark.asyncio
    async def test_web_allowed_at_15(self):
        from src.services.notifier import Notifier

        redis = AsyncMock()
        redis.incr.return_value = 15
        registry = AsyncMock()
        notifier = Notifier(surface_registry=registry, redis=redis)
        allowed = await notifier._check_rate_limit("user1", "web")
        assert allowed is True

    @pytest.mark.asyncio
    async def test_web_blocked_at_16(self):
        from src.services.notifier import Notifier

        redis = AsyncMock()
        redis.incr.return_value = 16
        registry = AsyncMock()
        notifier = Notifier(surface_registry=registry, redis=redis)
        allowed = await notifier._check_rate_limit("user1", "web")
        assert allowed is False

    @pytest.mark.asyncio
    async def test_email_blocked_after_3(self):
        from src.services.notifier import Notifier

        redis = AsyncMock()
        redis.incr.return_value = 4
        registry = AsyncMock()
        notifier = Notifier(surface_registry=registry, redis=redis)
        allowed = await notifier._check_rate_limit("user1", "email")
        assert allowed is False

    @pytest.mark.asyncio
    async def test_slack_blocked_after_8(self):
        from src.services.notifier import Notifier

        redis = AsyncMock()
        redis.incr.return_value = 9
        registry = AsyncMock()
        notifier = Notifier(surface_registry=registry, redis=redis)
        allowed = await notifier._check_rate_limit("user1", "slack")
        assert allowed is False

    @pytest.mark.asyncio
    async def test_unknown_surface_uses_default_10(self):
        from src.services.notifier import Notifier

        redis = AsyncMock()
        redis.incr.return_value = 10
        registry = AsyncMock()
        notifier = Notifier(surface_registry=registry, redis=redis)
        allowed = await notifier._check_rate_limit("user1", "sms")
        assert allowed is True

    @pytest.mark.asyncio
    async def test_expire_only_set_on_first_increment(self):
        from src.services.notifier import Notifier

        redis = AsyncMock()
        redis.incr.return_value = 3
        registry = AsyncMock()
        notifier = Notifier(surface_registry=registry, redis=redis)
        await notifier._check_rate_limit("user1", "telegram")
        redis.expire.assert_not_called()

    @pytest.mark.asyncio
    async def test_rate_limit_no_redis_always_allows(self):
        from src.services.notifier import Notifier

        registry = AsyncMock()
        notifier = Notifier(surface_registry=registry, redis=None)
        allowed = await notifier._check_rate_limit("user1", "telegram")
        assert allowed is True
