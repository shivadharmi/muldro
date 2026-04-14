"""Tests for the unified integrations endpoint."""

from src.api.routes_integrations import UnifiedIntegrationResponse


class TestUnifiedIntegrationResponse:
    def test_local_category(self):
        resp = UnifiedIntegrationResponse(
            server_name="playwright",
            display_name="Playwright Browser",
            provider=None,
            category="local",
            configured=True,
            connected=True,
            health_status="healthy",
            enabled=True,
            install_id="inst_abc",
            scopes=[],
        )
        assert resp.category == "local"
        assert resp.configured is True
        assert resp.connected is True

    def test_oauth_category(self):
        resp = UnifiedIntegrationResponse(
            server_name="google-workspace",
            display_name="Google Workspace",
            provider="google",
            category="oauth",
            configured=True,
            connected=False,
            health_status="unknown",
            enabled=True,
            install_id="inst_def",
            scopes=["email.send", "calendar.list"],
        )
        assert resp.category == "oauth"
        assert resp.provider == "google"
        assert len(resp.scopes) == 2

    def test_token_category(self):
        resp = UnifiedIntegrationResponse(
            server_name="slack",
            display_name="Slack",
            provider="slack",
            category="token",
            configured=True,
            connected=True,
            health_status="healthy",
            enabled=True,
            install_id="inst_ghi",
            scopes=["messaging.send"],
        )
        assert resp.category == "token"
