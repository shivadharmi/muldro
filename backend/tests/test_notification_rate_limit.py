"""Tests for notification rate limiting in Notifier."""

from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_redis(count: int):
    """Return a MagicMock redis with pipeline returning (count, True)."""
    redis = MagicMock()
    pipe = AsyncMock()
    pipe.execute = AsyncMock(return_value=[count, True])
    redis.pipeline = MagicMock(return_value=pipe)
    redis.publish = AsyncMock()
    return redis, pipe


class TestRateLimiting:
    """Test per-surface rate caps."""

    @pytest.mark.asyncio
    async def test_first_notification_allowed(self):
        from src.services.notifier import Notifier

        redis, pipe = _make_redis(1)
        registry = AsyncMock()
        notifier = Notifier(surface_registry=registry, redis=redis)
        allowed = await notifier._check_rate_limit("user1", "telegram")
        assert allowed is True
        pipe.incr.assert_called_once_with("notifier:rate:user1:telegram")
        pipe.expire.assert_called_once_with("notifier:rate:user1:telegram", 3600)

    @pytest.mark.asyncio
    async def test_telegram_blocked_after_5(self):
        from src.services.notifier import Notifier

        redis, _ = _make_redis(6)
        registry = AsyncMock()
        notifier = Notifier(surface_registry=registry, redis=redis)
        allowed = await notifier._check_rate_limit("user1", "telegram")
        assert allowed is False

    @pytest.mark.asyncio
    async def test_web_allowed_at_15(self):
        from src.services.notifier import Notifier

        redis, _ = _make_redis(15)
        registry = AsyncMock()
        notifier = Notifier(surface_registry=registry, redis=redis)
        allowed = await notifier._check_rate_limit("user1", "web")
        assert allowed is True

    @pytest.mark.asyncio
    async def test_web_blocked_at_16(self):
        from src.services.notifier import Notifier

        redis, _ = _make_redis(16)
        registry = AsyncMock()
        notifier = Notifier(surface_registry=registry, redis=redis)
        allowed = await notifier._check_rate_limit("user1", "web")
        assert allowed is False

    @pytest.mark.asyncio
    async def test_email_blocked_after_3(self):
        from src.services.notifier import Notifier

        redis, _ = _make_redis(4)
        registry = AsyncMock()
        notifier = Notifier(surface_registry=registry, redis=redis)
        allowed = await notifier._check_rate_limit("user1", "email")
        assert allowed is False

    @pytest.mark.asyncio
    async def test_slack_blocked_after_8(self):
        from src.services.notifier import Notifier

        redis, _ = _make_redis(9)
        registry = AsyncMock()
        notifier = Notifier(surface_registry=registry, redis=redis)
        allowed = await notifier._check_rate_limit("user1", "slack")
        assert allowed is False

    @pytest.mark.asyncio
    async def test_unknown_surface_uses_default_10(self):
        from src.services.notifier import Notifier

        redis, _ = _make_redis(10)
        registry = AsyncMock()
        notifier = Notifier(surface_registry=registry, redis=redis)
        allowed = await notifier._check_rate_limit("user1", "sms")
        assert allowed is True

    @pytest.mark.asyncio
    async def test_expire_set_on_every_increment(self):
        """expire is refreshed on every call (via pipeline) to maintain the TTL window."""
        from src.services.notifier import Notifier

        redis, pipe = _make_redis(3)
        registry = AsyncMock()
        notifier = Notifier(surface_registry=registry, redis=redis)
        await notifier._check_rate_limit("user1", "telegram")
        pipe.expire.assert_called_once_with("notifier:rate:user1:telegram", 3600)

    @pytest.mark.asyncio
    async def test_rate_limit_no_redis_always_allows(self):
        from src.services.notifier import Notifier

        registry = AsyncMock()
        notifier = Notifier(surface_registry=registry, redis=None)
        allowed = await notifier._check_rate_limit("user1", "telegram")
        assert allowed is True


class TestRateLimitInDelivery:
    """Test that rate limiting is enforced during actual delivery."""

    @pytest.mark.asyncio
    async def test_rate_limited_notification_held_for_briefing(self):
        from src.services.notifier import Notifier

        registry = AsyncMock()
        registry.get_active_surfaces.return_value = ["telegram"]
        registry.get_preferred_surface.return_value = "telegram"
        redis, _ = _make_redis(6)  # over telegram limit of 5
        telegram = AsyncMock(return_value={"status": "sent"})
        notifier = Notifier(surface_registry=registry, redis=redis, telegram_sender=telegram)
        result = await notifier.notify(
            user_id="usr_test",
            notification_type="info_update",
            title="Rate limited",
            body="Too many",
            data={
                "urgency": 0.9,
                "goal_relevance": 0.9,
                "novelty": 0.9,
                "confidence": 0.9,
                "interruptibility": 0.9,
            },
        )
        assert result["status"] == "rate_limited"
        telegram.assert_not_called()
