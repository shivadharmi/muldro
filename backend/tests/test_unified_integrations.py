"""Tests for the unified integrations endpoint."""

from src.api.routes_integrations import UnifiedIntegrationResponse
from src.services.integration_status import coarsen_scopes, derive_slug


class TestDeriveSlug:
    def test_prefers_provider(self):
        assert derive_slug("google", "google-workspace") == "google"

    def test_falls_back_to_server_name(self):
        assert derive_slug(None, "playwright") == "playwright"

    def test_strips_descriptor_suffix(self):
        assert derive_slug(None, "google-workspace") == "google"

    def test_normalizes_underscore_and_case(self):
        assert derive_slug(None, "GitHub_Repo") == "github"

    def test_known_brand_slugs(self):
        assert derive_slug("github", "github") == "github"
        assert derive_slug("slack", "slack") == "slack"
        assert derive_slug("notion", "notion") == "notion"
        assert derive_slug("atlassian", "atlassian") == "atlassian"

    def test_empty_inputs(self):
        assert derive_slug(None, "") == ""


class TestCoarsenScopes:
    def test_read_and_write(self):
        assert coarsen_scopes(["email.read", "email.send"]) == ["read", "write"]

    def test_read_only(self):
        assert coarsen_scopes(["calendar.read", "calendar.list"]) == ["read"]

    def test_write_only(self):
        assert coarsen_scopes(["repo.merge_pr"]) == ["write"]

    def test_empty(self):
        assert coarsen_scopes([]) == []

    def test_deterministic_read_before_write(self):
        # write capability first, but read must still be ordered first
        assert coarsen_scopes(["messaging.post", "channels.search"]) == ["read", "write"]

    def test_unknown_capability_ignored(self):
        assert coarsen_scopes(["mystery.capability"]) == []


class TestUnifiedIntegrationResponse:
    def test_slug_and_access_scopes_present(self):
        resp = UnifiedIntegrationResponse(
            server_name="google-workspace",
            display_name="Google Workspace",
            provider="google",
            category="oauth",
            configured=True,
            connected=True,
            health_status="healthy",
            enabled=True,
            install_id="inst_x",
            scopes=["email.read", "email.send"],
            slug="google",
            access_scopes=["read", "write"],
        )
        assert resp.slug == "google"
        assert resp.access_scopes == ["read", "write"]

    def test_new_fields_default_safe(self):
        # Back-compat: existing callers that omit the new fields still construct.
        resp = UnifiedIntegrationResponse(
            server_name="slack",
            display_name="Slack",
            provider="slack",
            category="token",
            configured=True,
            connected=True,
            health_status="healthy",
            enabled=True,
            install_id="inst_y",
            scopes=["messaging.send"],
        )
        assert resp.slug == ""
        assert resp.access_scopes == []

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
