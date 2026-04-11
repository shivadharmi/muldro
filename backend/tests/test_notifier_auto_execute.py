"""Tests for auto_execute_notify notification type (Spec 2B-i).

Verifies that auto_execute_notify goes to preferred surface only,
not all active surfaces (unlike approval_request / critical_alert).
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.services.notifier import Notifier


@pytest.fixture
def mock_registry():
    registry = AsyncMock()
    registry.get_active_surfaces = AsyncMock(return_value=["web", "telegram"])
    registry.get_preferred_surface = AsyncMock(return_value="web")
    return registry


@pytest.fixture
def notifier(mock_registry):
    n = Notifier(surface_registry=mock_registry)
    return n


class TestAutoExecuteNotify:
    async def test_auto_notify_sends_to_preferred_surface_only(self, notifier, mock_registry):
        """auto_execute_notify is lower priority — preferred surface only."""
        deliver_mock = AsyncMock(return_value={"status": "published"})
        mark_mock = AsyncMock()

        with (
            patch.object(notifier, "_deliver", deliver_mock),
            patch.object(notifier, "_mark_delivered", mark_mock),
        ):
            result = await notifier.notify(
                user_id="usr_1",
                notification_type="auto_execute_notify",
                title="Auto-executed: email.send",
                body="Trusted capability, low risk",
                data={"run_id": "run_001", "step_id": "step_001"},
            )

        # Should go to preferred surface, NOT all surfaces
        deliver_mock.assert_called_once()
        call_args = deliver_mock.call_args
        # First positional arg is the surface name
        assert call_args[0][0] == "web"
        assert result["status"] == "sent"
        assert "web" in result["surfaces"]
        assert "telegram" not in result["surfaces"]

    async def test_auto_notify_not_sent_to_all_surfaces(self, notifier, mock_registry):
        """Unlike approval_request, auto_notify does NOT go to all surfaces."""
        deliver_mock = AsyncMock(return_value={"status": "published"})

        with (
            patch.object(notifier, "_deliver", deliver_mock),
            patch.object(notifier, "_mark_delivered", AsyncMock()),
        ):
            await notifier.notify(
                user_id="usr_1",
                notification_type="auto_execute_notify",
                title="Auto-executed: calendar.create",
                body="Trusted, notifying",
                data={},
            )

        # Should be exactly 1 delivery (preferred only), not 2 (all surfaces)
        assert deliver_mock.call_count == 1

    async def test_approval_request_goes_to_all_surfaces(self, notifier, mock_registry):
        """Contrast: approval_request should go to ALL surfaces."""
        deliver_mock = AsyncMock(return_value={"status": "published"})

        with patch.object(notifier, "_deliver", deliver_mock):
            await notifier.notify(
                user_id="usr_1",
                notification_type="approval_request",
                title="Approve: email.send",
                body="Needs approval",
                data={"approval_id": "apr_001"},
            )

        # Should be 2 deliveries (web + telegram)
        assert deliver_mock.call_count == 2
        surfaces_called = {call[0][0] for call in deliver_mock.call_args_list}
        assert surfaces_called == {"web", "telegram"}

    async def test_critical_alert_goes_to_all_surfaces(self, notifier, mock_registry):
        """critical_alert should also go to ALL surfaces."""
        deliver_mock = AsyncMock(return_value={"status": "published"})

        with patch.object(notifier, "_deliver", deliver_mock):
            await notifier.notify(
                user_id="usr_1",
                notification_type="critical_alert",
                title="System alert",
                body="Critical issue detected",
                data={},
            )

        assert deliver_mock.call_count == 2

    async def test_info_update_goes_to_preferred_surface_only(self, notifier, mock_registry):
        """info_update (and other non-critical types) go to preferred surface only."""
        deliver_mock = AsyncMock(return_value={"status": "published"})

        with (
            patch.object(notifier, "_deliver", deliver_mock),
            patch.object(notifier, "_mark_delivered", AsyncMock()),
        ):
            await notifier.notify(
                user_id="usr_1",
                notification_type="info_update",
                title="FYI",
                body="Some info",
                data={},
            )

        assert deliver_mock.call_count == 1
        assert deliver_mock.call_args[0][0] == "web"

    async def test_auto_notify_calls_mark_delivered(self, notifier, mock_registry):
        """auto_execute_notify should call _mark_delivered for dedup tracking."""
        deliver_mock = AsyncMock(return_value={"status": "published"})
        mark_mock = AsyncMock()

        with (
            patch.object(notifier, "_deliver", deliver_mock),
            patch.object(notifier, "_mark_delivered", mark_mock),
        ):
            await notifier.notify(
                user_id="usr_1",
                notification_type="auto_execute_notify",
                title="Auto-executed",
                body="Done",
                data={},
            )

        mark_mock.assert_called_once()
        # Mark delivered on the preferred surface
        assert mark_mock.call_args[0][1] == "web"

    async def test_auto_notify_no_preferred_surface(self, notifier, mock_registry):
        """When no preferred surface, auto_notify delivers nothing gracefully."""
        mock_registry.get_preferred_surface = AsyncMock(return_value=None)
        deliver_mock = AsyncMock(return_value={"status": "published"})

        with (
            patch.object(notifier, "_deliver", deliver_mock),
            patch.object(notifier, "_mark_delivered", AsyncMock()),
        ):
            result = await notifier.notify(
                user_id="usr_1",
                notification_type="auto_execute_notify",
                title="Auto-executed",
                body="Done",
                data={},
            )

        deliver_mock.assert_not_called()
        assert result["surfaces"] == {}
