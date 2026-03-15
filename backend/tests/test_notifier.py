"""Tests for the notification coordinator service."""

from unittest.mock import AsyncMock

from src.services.notifier import Notifier
from src.services.surface_registry import SurfaceRegistry


class TestNotifier:
    async def _make_notifier(
        self,
        surfaces: list[str] | None = None,
        telegram_sender=None,
        ws_sender=None,
        redis=None,
    ) -> Notifier:
        registry = SurfaceRegistry(redis=None)
        for s in surfaces or []:
            await registry.register("usr_default", s)

        return Notifier(
            surface_registry=registry,
            redis=redis,
            telegram_sender=telegram_sender,
            websocket_sender=ws_sender,
        )

    async def test_no_surfaces_returns_queued(self):
        notifier = await self._make_notifier(surfaces=[])
        result = await notifier.notify("usr_default", "info_update", "Test", "Body")
        assert result["status"] == "queued"
        assert result["surfaces"] == []

    async def test_approval_sent_to_all_surfaces(self):
        tg_sender = AsyncMock(return_value={"status": "sent", "message_id": 1})
        ws_sender = AsyncMock(return_value={"status": "sent"})

        notifier = await self._make_notifier(
            surfaces=["telegram", "web"],
            telegram_sender=tg_sender,
            ws_sender=ws_sender,
        )

        result = await notifier.notify(
            "usr_default",
            "approval_request",
            "Send email to investor",
            "Draft reply to Series A follow-up",
            data={"approval_id": "apr_01", "risk_level": "high"},
        )

        assert result["status"] == "sent"
        assert "telegram" in result["surfaces"]
        assert "web" in result["surfaces"]
        tg_sender.assert_called_once()
        ws_sender.assert_called_once()

    async def test_info_update_sent_to_preferred_only(self):
        tg_sender = AsyncMock(return_value={"status": "sent", "message_id": 1})

        notifier = await self._make_notifier(
            surfaces=["telegram", "web"],
            telegram_sender=tg_sender,
        )

        result = await notifier.notify(
            "usr_default", "info_update", "Status update", "Things are fine"
        )

        assert result["status"] == "sent"
        # Preferred is "web" (less intrusive), but no ws_sender so falls back
        # to Redis publish in _deliver_web (which returns skipped without redis)
        # The point: telegram is NOT called for info_update when web is preferred
        assert "web" in result["surfaces"]
        tg_sender.assert_not_called()

    async def test_info_update_telegram_only(self):
        tg_sender = AsyncMock(return_value={"status": "sent", "message_id": 1})

        notifier = await self._make_notifier(
            surfaces=["telegram"],
            telegram_sender=tg_sender,
        )

        result = await notifier.notify("usr_default", "info_update", "Update", "Content")

        assert result["status"] == "sent"
        assert "telegram" in result["surfaces"]
        tg_sender.assert_called_once()

    async def test_critical_alert_sent_to_all(self):
        tg_sender = AsyncMock(return_value={"status": "sent", "message_id": 1})

        notifier = await self._make_notifier(
            surfaces=["telegram"],
            telegram_sender=tg_sender,
        )

        result = await notifier.notify(
            "usr_default",
            "critical_alert",
            "Budget exhausted",
            "Daily limit reached",
        )

        assert result["status"] == "sent"
        tg_sender.assert_called_once()

    async def test_telegram_approval_format(self):
        """Verify approval notifications include inline keyboard markup."""
        captured = {}

        async def capture_send(**kwargs):
            captured.update(kwargs)
            return {"status": "sent", "message_id": 1}

        notifier = await self._make_notifier(
            surfaces=["telegram"],
            telegram_sender=capture_send,
        )

        await notifier.notify(
            "usr_default",
            "approval_request",
            "Send email",
            "To investor@fund.com",
            data={"approval_id": "apr_99", "risk_level": "high"},
        )

        assert "Approval Required" in captured["text"]
        assert "apr_99" in captured.get("reply_markup", "")

    async def test_dedup_marks_delivered(self):
        tg_sender = AsyncMock(return_value={"status": "sent", "message_id": 1})

        notifier = await self._make_notifier(
            surfaces=["telegram"],
            telegram_sender=tg_sender,
        )

        await notifier.notify("usr_default", "info_update", "Test", "Body")

        # Check that something was marked as delivered
        assert len(notifier._delivered) > 0

    async def test_on_action_taken_with_redis(self):
        redis = AsyncMock()
        registry = SurfaceRegistry(redis=None)
        await registry.register("usr_default", "telegram")

        notifier = Notifier(
            surface_registry=registry,
            redis=redis,
        )

        await notifier.on_action_taken("usr_default", "apr_01", "telegram")

        redis.publish.assert_called_once()
        call_args = redis.publish.call_args
        assert "surface_sync" in call_args[0][0]

    async def test_on_action_taken_without_redis(self):
        """Should not raise even without Redis."""
        notifier = await self._make_notifier(surfaces=["telegram"])
        await notifier.on_action_taken("usr_default", "apr_01", "telegram")

    async def test_is_delivered_in_memory(self):
        tg_sender = AsyncMock(return_value={"status": "sent", "message_id": 1})

        notifier = await self._make_notifier(
            surfaces=["telegram"],
            telegram_sender=tg_sender,
        )

        # Before notification
        assert await notifier.is_delivered("notif_fake") is False

        # After notification to preferred surface (telegram is the only one)
        await notifier.notify("usr_default", "info_update", "Test", "Body")

        # At least one notification was marked delivered
        assert len(notifier._delivered) > 0

    async def test_delivery_error_handled_gracefully(self):
        tg_sender = AsyncMock(side_effect=Exception("Network error"))

        notifier = await self._make_notifier(
            surfaces=["telegram"],
            telegram_sender=tg_sender,
        )

        result = await notifier.notify("usr_default", "info_update", "Test", "Body")

        # Should not raise, error is captured per surface
        assert result["status"] == "sent"
        assert "error" in result["surfaces"]["telegram"]["error"]
