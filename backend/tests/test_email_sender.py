"""Tests for EmailSender and email templates."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.email_sender import EmailSender
from src.services.email_templates import magic_link_email
from tests.conftest import make_mock_settings


class TestEmailSender:
    """Tests for EmailSender service."""

    @pytest.fixture
    def enabled_settings(self):
        return make_mock_settings(
            ses_enabled=True,
            ses_from_address="jarvis@example.com",
            ses_region="ap-south-1",
        )

    @pytest.fixture
    def disabled_settings(self):
        return make_mock_settings(
            ses_enabled=False,
            ses_from_address="jarvis@example.com",
            ses_region="ap-south-1",
        )

    async def test_send_success(self, enabled_settings):
        sender = EmailSender(enabled_settings)
        mock_ses = MagicMock()
        mock_ses.send_email.return_value = {"MessageId": "msg-123"}
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = mock_ses

        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            message_id = await sender.send(
                to="user@example.com",
                subject="Test Subject",
                body_html="<h1>Hello</h1>",
                body_text="Hello",
            )

        assert message_id == "msg-123"
        mock_boto3.client.assert_called_once_with("ses", region_name="ap-south-1")
        call_kwargs = mock_ses.send_email.call_args[1]
        assert call_kwargs["Source"] == "jarvis@example.com"
        assert call_kwargs["Destination"] == {"ToAddresses": ["user@example.com"]}
        assert call_kwargs["Message"]["Subject"]["Data"] == "Test Subject"

    async def test_send_disabled_raises(self, disabled_settings):
        sender = EmailSender(disabled_settings)
        with pytest.raises(RuntimeError, match="SES is not enabled"):
            await sender.send(to="user@example.com", subject="Test", body_text="hi")

    async def test_send_no_from_address_raises(self):
        settings = make_mock_settings(ses_enabled=True, ses_from_address="", ses_region="us-east-1")
        sender = EmailSender(settings)
        with pytest.raises(RuntimeError, match="from address not configured"):
            await sender.send(to="user@example.com", subject="Test", body_text="hi")

    async def test_ses_client_error_propagates(self, enabled_settings):
        from botocore.exceptions import ClientError

        sender = EmailSender(enabled_settings)
        mock_ses = MagicMock()
        mock_ses.send_email.side_effect = ClientError(
            {"Error": {"Code": "MessageRejected", "Message": "Email rejected"}},
            "SendEmail",
        )
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = mock_ses

        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            with pytest.raises(ClientError):
                await sender.send(to="user@example.com", subject="Test", body_text="hi")


class TestMagicLinkTemplate:
    """Tests for magic link email template."""

    def test_magic_link_template_contains_url(self):
        url = "https://app.jarvis.ai/login?token=abc123"
        html, text = magic_link_email(url, ttl_minutes=15)
        assert url in html
        assert url in text

    def test_magic_link_template_html_structure(self):
        url = "https://app.jarvis.ai/login?token=abc123"
        html, text = magic_link_email(url)
        assert "<!DOCTYPE html>" in html
        assert "Sign in" in html
        assert "15 minutes" in html
        assert "15 minutes" in text

    def test_magic_link_template_custom_ttl(self):
        html, text = magic_link_email("https://example.com", ttl_minutes=30)
        assert "30 minutes" in html
        assert "30 minutes" in text


class TestAuthRoutesSES:
    """Tests for SES integration in auth routes."""

    def test_production_mode_calls_email_sender(self):
        from fastapi.testclient import TestClient

        from src.api.app import app
        from src.config.settings import get_settings

        mock_settings = make_mock_settings(
            backend_token="secret-prod-token",
            ses_enabled=True,
            ses_from_address="jarvis@example.com",
            ses_region="ap-south-1",
            frontend_url="https://app.jarvis.ai",
            magic_link_ttl_minutes=15,
        )
        app.dependency_overrides[get_settings] = lambda: mock_settings

        mock_send = AsyncMock(return_value="msg-456")

        with patch("src.api.routes_auth_magic_link.AuthService") as mock_auth:
            mock_auth.return_value.send_magic_link = AsyncMock(return_value="test-token-xyz")

            with patch("src.services.email_sender.EmailSender.send", mock_send):
                client = TestClient(app, raise_server_exceptions=False)
                resp = client.post(
                    "/v1/auth/magic-link",
                    json={"email": "user@example.com"},
                )

                assert resp.status_code == 200
                data = resp.json()
                assert data["status"] == "sent"
                assert data["token"] is None  # Not returned in production mode

                mock_send.assert_called_once()
                call_kwargs = mock_send.call_args[1]
                assert call_kwargs["to"] == "user@example.com"
                assert call_kwargs["subject"] == "Sign in to Jarvis"
                assert "test-token-xyz" in call_kwargs["body_html"]

        app.dependency_overrides.pop(get_settings, None)

    def test_dev_mode_returns_token_directly(self):
        from fastapi.testclient import TestClient

        from src.api.app import app
        from src.config.settings import get_settings

        mock_settings = make_mock_settings(
            backend_token="",  # Dev mode: no backend token
            ses_enabled=False,
        )
        app.dependency_overrides[get_settings] = lambda: mock_settings

        with patch("src.api.routes_auth_magic_link.AuthService") as mock_auth:
            mock_auth.return_value.send_magic_link = AsyncMock(return_value="dev-token-abc")

            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/v1/auth/magic-link",
                json={"email": "dev@example.com"},
            )

            assert resp.status_code == 200
            data = resp.json()
            assert data["token"] == "dev-token-abc"

        app.dependency_overrides.pop(get_settings, None)

    def test_ses_failure_returns_500(self):
        from fastapi.testclient import TestClient

        from src.api.app import app
        from src.config.settings import get_settings

        mock_settings = make_mock_settings(
            backend_token="secret-prod-token",
            ses_enabled=True,
            ses_from_address="jarvis@example.com",
            ses_region="ap-south-1",
            frontend_url="https://app.jarvis.ai",
            magic_link_ttl_minutes=15,
        )
        app.dependency_overrides[get_settings] = lambda: mock_settings

        mock_send = AsyncMock(side_effect=RuntimeError("SES is not enabled"))

        with patch("src.api.routes_auth_magic_link.AuthService") as mock_auth:
            mock_auth.return_value.send_magic_link = AsyncMock(return_value="test-token")

            with patch("src.services.email_sender.EmailSender.send", mock_send):
                client = TestClient(app, raise_server_exceptions=False)
                resp = client.post(
                    "/v1/auth/magic-link",
                    json={"email": "user@example.com"},
                )

                assert resp.status_code == 500
                assert "Failed to send" in resp.json()["error"]["message"]

        app.dependency_overrides.pop(get_settings, None)
