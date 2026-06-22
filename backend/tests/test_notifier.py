"""Tests for the notification coordinator service."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.notifier import Notifier
from src.services.surface_registry import SurfaceRegistry
from tests.conftest import TEST_USER_ID

_FULL_PRIORITY = {
    "urgency": 0.9,
    "goal_relevance": 0.9,
    "novelty": 0.9,
    "confidence": 0.9,
    "interruptibility": 0.9,
}


class TestNotifier:
    async def _make_notifier(
        self,
        surfaces: list[str] | None = None,
        ws_sender=None,
        redis=None,
    ) -> Notifier:
        registry = SurfaceRegistry(redis=None)
        for s in surfaces or []:
            await registry.register(TEST_USER_ID, s)

        return Notifier(
            surface_registry=registry,
            redis=redis,
            websocket_sender=ws_sender,
        )

    async def test_no_surfaces_returns_queued(self):
        notifier = await self._make_notifier(surfaces=[])
        result = await notifier.notify(
            TEST_USER_ID,
            "info_update",
            "Test",
            "Body",
            data=_FULL_PRIORITY,
        )
        assert result["status"] == "queued"
        assert result["surfaces"] == []

    async def test_approval_broadcast_delivers_to_web(self):
        ws_sender = AsyncMock(return_value={"status": "sent"})

        notifier = await self._make_notifier(
            surfaces=["web"],
            ws_sender=ws_sender,
        )

        result = await notifier.notify(
            TEST_USER_ID,
            "approval_request",
            "Send email to investor",
            "Draft reply to Series A follow-up",
            data={"approval_id": "apr_01", "risk_level": "high"},
        )

        assert result["status"] == "sent"
        assert "web" in result["surfaces"]
        ws_sender.assert_called_once()

    async def test_info_update_sent_to_preferred_only(self):
        ws_sender = AsyncMock(return_value={"status": "sent"})

        notifier = await self._make_notifier(
            surfaces=["web", "slack"],
            ws_sender=ws_sender,
        )

        result = await notifier.notify(
            TEST_USER_ID,
            "info_update",
            "Status update",
            "Things are fine",
            data=_FULL_PRIORITY,
        )

        assert result["status"] == "sent"
        # Preferred is "web"; info_update delivers to the preferred surface only,
        # so "slack" is never contacted.
        assert "web" in result["surfaces"]
        assert "slack" not in result["surfaces"]
        ws_sender.assert_called_once()

    async def test_info_update_web_only(self):
        ws_sender = AsyncMock(return_value={"status": "sent"})

        notifier = await self._make_notifier(
            surfaces=["web"],
            ws_sender=ws_sender,
        )

        result = await notifier.notify(
            TEST_USER_ID,
            "info_update",
            "Update",
            "Content",
            data=_FULL_PRIORITY,
        )

        assert result["status"] == "sent"
        assert "web" in result["surfaces"]
        ws_sender.assert_called_once()

    async def test_critical_alert_delivers(self):
        ws_sender = AsyncMock(return_value={"status": "sent"})

        notifier = await self._make_notifier(
            surfaces=["web"],
            ws_sender=ws_sender,
        )

        result = await notifier.notify(
            TEST_USER_ID,
            "critical_alert",
            "Budget exhausted",
            "Daily limit reached",
        )

        assert result["status"] == "sent"
        ws_sender.assert_called_once()

    async def test_dedup_marks_delivered(self):
        ws_sender = AsyncMock(return_value={"status": "sent"})

        notifier = await self._make_notifier(
            surfaces=["web"],
            ws_sender=ws_sender,
        )

        await notifier.notify(
            TEST_USER_ID,
            "info_update",
            "Test",
            "Body",
            data=_FULL_PRIORITY,
        )

        # Check that something was marked as delivered
        assert len(notifier._delivered) > 0

    async def test_on_action_taken_with_redis(self):
        redis = AsyncMock()
        registry = SurfaceRegistry(redis=None)
        await registry.register(TEST_USER_ID, "web")

        notifier = Notifier(
            surface_registry=registry,
            redis=redis,
        )

        await notifier.on_action_taken(TEST_USER_ID, "apr_01", "web")

        redis.publish.assert_called_once()
        call_args = redis.publish.call_args
        assert "surface_sync" in call_args[0][0]

    async def test_on_action_taken_without_redis(self):
        """Should not raise even without Redis."""
        notifier = await self._make_notifier(surfaces=["web"])
        await notifier.on_action_taken(TEST_USER_ID, "apr_01", "web")

    async def test_is_delivered_in_memory(self):
        ws_sender = AsyncMock(return_value={"status": "sent"})

        notifier = await self._make_notifier(
            surfaces=["web"],
            ws_sender=ws_sender,
        )

        # Before notification
        assert await notifier.is_delivered("notif_fake") is False

        # After notification to preferred surface (web is the only one)
        await notifier.notify(
            TEST_USER_ID,
            "info_update",
            "Test",
            "Body",
            data=_FULL_PRIORITY,
        )

        # At least one notification was marked delivered
        assert len(notifier._delivered) > 0

    async def test_delivery_error_handled_gracefully(self):
        ws_sender = AsyncMock(side_effect=Exception("Network error"))

        notifier = await self._make_notifier(
            surfaces=["web"],
            ws_sender=ws_sender,
        )

        result = await notifier.notify(
            TEST_USER_ID,
            "info_update",
            "Test",
            "Body",
            data=_FULL_PRIORITY,
        )

        # Should not raise, error is captured per surface — but sanitized:
        # the per-surface error carries the safe message + code, NOT str(exc).
        assert result["status"] == "sent"
        web_result = result["surfaces"]["web"]
        assert web_result["status"] == "error"
        assert web_result["error"] == "Something went wrong. Please try again."
        assert web_result["error_code"] == "internal_error"
        assert "Network error" not in web_result["error"]


class TestPriorityScoreDelivery:
    """Test that priority score drives delivery decisions."""

    @pytest.mark.asyncio
    async def test_low_priority_returns_silent(self):
        """Score < 0.3 → silent, no delivery."""
        from src.services.notifier import Notifier

        registry = AsyncMock()
        notifier = Notifier(surface_registry=registry)
        result = await notifier.notify(
            user_id="usr_test",
            notification_type="info_update",
            title="Low priority",
            body="Not important",
            data={
                "urgency": 0.1,
                "goal_relevance": 0.1,
                "novelty": 0.1,
                "confidence": 0.1,
                "interruptibility": 0.1,
            },
        )
        assert result["status"] == "silent"
        registry.get_active_surfaces.assert_not_called()

    @pytest.mark.asyncio
    async def test_medium_priority_held_for_briefing(self):
        """Score 0.3-0.6 → held for briefing."""
        from src.services.notifier import Notifier

        registry = AsyncMock()
        redis = AsyncMock()
        redis.incr.return_value = 1
        notifier = Notifier(surface_registry=registry, redis=redis)
        result = await notifier.notify(
            user_id="usr_test",
            notification_type="info_update",
            title="Medium priority",
            body="Somewhat important",
            data={
                "urgency": 0.5,
                "goal_relevance": 0.4,
                "novelty": 0.4,
                "confidence": 0.4,
                "interruptibility": 0.4,
            },
        )
        assert result["status"] == "held_for_briefing"

    @pytest.mark.asyncio
    async def test_high_priority_delivers_normally(self):
        """Score >= 0.6 → normal delivery."""
        from src.services.notifier import Notifier

        registry = AsyncMock()
        registry.get_active_surfaces.return_value = ["web"]
        registry.get_preferred_surface.return_value = "web"
        redis = MagicMock()
        pipe = AsyncMock()
        pipe.execute = AsyncMock(return_value=[1, True])
        redis.pipeline = MagicMock(return_value=pipe)
        redis.publish = AsyncMock()
        redis.set = AsyncMock()
        notifier = Notifier(surface_registry=registry, redis=redis)
        result = await notifier.notify(
            user_id="usr_test",
            notification_type="info_update",
            title="High priority",
            body="Very important",
            data={
                "urgency": 0.9,
                "goal_relevance": 0.9,
                "novelty": 0.9,
                "confidence": 0.9,
                "interruptibility": 0.9,
            },
        )
        assert result["status"] == "sent"

    @pytest.mark.asyncio
    async def test_rate_limit_uses_atomic_pipeline(self):
        """_check_rate_limit should use pipeline with both incr and expire."""
        from src.services.notifier import Notifier

        redis = MagicMock()
        pipe = AsyncMock()
        pipe.execute = AsyncMock(return_value=[1, True])
        redis.pipeline = MagicMock(return_value=pipe)

        registry = AsyncMock()
        notifier = Notifier(surface_registry=registry, redis=redis)
        result = await notifier._check_rate_limit("usr_test", "web")

        assert result is True
        redis.pipeline.assert_called_once()
        pipe.incr.assert_called_once_with("notifier:rate:usr_test:web")
        pipe.expire.assert_called_once_with("notifier:rate:usr_test:web", 3600)

    @pytest.mark.asyncio
    async def test_rate_limit_returns_false_over_limit(self):
        """Rate limit should deny when count exceeds surface limit."""
        from src.services.notifier import Notifier

        redis = MagicMock()
        pipe = AsyncMock()
        pipe.execute = AsyncMock(return_value=[6, True])  # email limit is 3
        redis.pipeline = MagicMock(return_value=pipe)

        registry = AsyncMock()
        notifier = Notifier(surface_registry=registry, redis=redis)
        result = await notifier._check_rate_limit("usr_test", "email")

        assert result is False

    @pytest.mark.asyncio
    async def test_rate_limit_expire_called_every_time(self):
        """expire should be called on every increment, not just first."""
        from src.services.notifier import Notifier

        redis = MagicMock()
        pipe = AsyncMock()
        pipe.execute = AsyncMock(return_value=[3, True])  # 3rd call
        redis.pipeline = MagicMock(return_value=pipe)

        registry = AsyncMock()
        notifier = Notifier(surface_registry=registry, redis=redis)
        await notifier._check_rate_limit("usr_test", "web")

        pipe.expire.assert_called_once_with("notifier:rate:usr_test:web", 3600)

    @pytest.mark.asyncio
    async def test_delivered_dict_eviction_at_10k(self):
        """_delivered dict should evict oldest entries when exceeding 10k."""
        from src.services.notifier import Notifier

        registry = AsyncMock()
        notifier = Notifier(surface_registry=registry)

        # Fill with 10001 entries
        for i in range(10_001):
            notifier._delivered[f"notif_{i}"] = {"web"}

        assert len(notifier._delivered) == 10_001

        # mark_delivered should trigger eviction
        await notifier._mark_delivered("notif_new", "web")

        # Should have evicted 1000, then added 1
        assert len(notifier._delivered) <= 9_002
        assert "notif_new" in notifier._delivered

    @pytest.mark.asyncio
    async def test_approval_request_bypasses_priority_filter(self):
        """approval_request and critical_alert always deliver."""
        from src.services.notifier import Notifier

        registry = AsyncMock()
        registry.get_active_surfaces.return_value = ["web"]
        ws_sender = AsyncMock(return_value={"status": "sent"})
        redis = MagicMock()
        pipe = AsyncMock()
        pipe.execute = AsyncMock(return_value=[1, True])
        redis.pipeline = MagicMock(return_value=pipe)
        redis.publish = AsyncMock()
        notifier = Notifier(surface_registry=registry, redis=redis, websocket_sender=ws_sender)
        result = await notifier.notify(
            user_id="usr_test",
            notification_type="approval_request",
            title="Approve deploy",
            body="Deploy to prod",
            data={"urgency": 0.1, "approval_id": "apr_123"},
        )
        assert result["status"] == "sent"
