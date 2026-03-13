"""Tests for notification service — outbound messages to channels."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.notification_service import NotificationService
from tests.conftest import make_mock_settings


@pytest.fixture
def settings():
    s = make_mock_settings()
    s.slack_webhook_url = ""
    return s


@pytest.fixture
def mock_db():
    return MagicMock()


@pytest.mark.asyncio
async def test_slack_not_configured(settings, mock_db):
    """Should return not_configured when webhook URL is empty."""
    service = NotificationService(settings=settings, db=mock_db)
    result = await service.notify("usr_default", "Test", "Hello", channel="slack")

    assert result["delivered"] is False
    assert result["error"] == "not_configured"


@pytest.mark.asyncio
async def test_unsupported_channel(settings, mock_db):
    """Should return error for unsupported channels."""
    service = NotificationService(settings=settings, db=mock_db)
    result = await service.notify("usr_default", "Test", "Hello", channel="sms")

    assert result["delivered"] is False
    assert result["error"] == "unsupported_channel"


@pytest.mark.asyncio
@patch("src.services.notification_service.httpx.AsyncClient")
async def test_slack_send_success(mock_client_cls, mock_db):
    """Should send Slack notification successfully."""
    settings = make_mock_settings()
    settings.slack_webhook_url = "https://hooks.slack.com/test"

    mock_response = MagicMock()
    mock_response.status_code = 200

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client_cls.return_value = mock_client

    service = NotificationService(settings=settings, db=mock_db)
    result = await service.notify("usr_default", "Test", "Hello World", channel="slack")

    assert result["delivered"] is True
    assert result["channel"] == "slack"
    mock_client.post.assert_called_once()


@pytest.mark.asyncio
@patch("src.services.notification_service.httpx.AsyncClient")
async def test_slack_send_failure(mock_client_cls, mock_db):
    """Should handle Slack webhook failure gracefully."""
    settings = make_mock_settings()
    settings.slack_webhook_url = "https://hooks.slack.com/test"

    mock_response = MagicMock()
    mock_response.status_code = 500

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client_cls.return_value = mock_client

    service = NotificationService(settings=settings, db=mock_db)
    result = await service.notify("usr_default", "Test", "Hello World", channel="slack")

    assert result["delivered"] is False
    assert "500" in result["error"]


@pytest.mark.asyncio
async def test_notify_approval_needed(settings, mock_db):
    """Should format and send approval notification."""
    service = NotificationService(settings=settings, db=mock_db)
    result = await service.notify_approval_needed(
        "usr_default", "apr_001", "Send email to investor", "high"
    )

    # Not configured, but should return the expected shape
    assert result["delivered"] is False
    assert result["channel"] == "slack"


@pytest.mark.asyncio
async def test_notify_briefing_ready(settings, mock_db):
    """Should format and send briefing notification."""
    service = NotificationService(settings=settings, db=mock_db)
    result = await service.notify_briefing_ready("usr_default", "3 priorities, 1 follow-up")

    assert result["delivered"] is False
    assert result["channel"] == "slack"
