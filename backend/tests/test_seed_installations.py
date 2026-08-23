"""Tests for MCP server seed installations — verifies known bugs are fixed."""

from src.integrations.seed_installations import _DEFAULT_INSTALLATIONS


class TestSeedInstallations:
    def _get_seed(self, server_name: str) -> dict:
        return next(s for s in _DEFAULT_INSTALLATIONS if s["server_name"] == server_name)

    def test_google_workspace_http_transport(self):
        """Google Workspace seed uses streamable-http; gateway-routed, no static URL/local proc."""
        seed = self._get_seed("google-workspace")
        assert seed["transport"] == "streamable-http", (
            f"Expected streamable-http transport, got '{seed['transport']}'"
        )
        # URL is resolved at runtime by the OpenConnector gateway adapter —
        # no static remote_url and no uvx-managed local process.
        assert seed.get("remote_url") is None, (
            "Google Workspace is gateway-routed; remote_url should be None"
        )
        assert not seed.get("managed_local"), (
            "Google Workspace is gateway-routed, not managed_local "
            "(no uvx process; the OpenConnector adapter resolves the URL)"
        )
        assert seed.get("command") is None, "HTTP transport should not have a command"
        assert seed.get("args") is None, "HTTP transport should not have args"
        assert seed["auth_provider"] == "platform_jwt", (
            f"auth_provider must be 'platform_jwt' for gateway-routed servers, "
            f"got '{seed['auth_provider']}'"
        )
        assert seed["env_template"] == {}, (
            "HTTP service env vars live in LocalServerSpec, not in seed"
        )

    def test_slack_env_vars(self):
        """Slack seed must use SLACK_MCP_XOXP_TOKEN, not SLACK_BOT_TOKEN."""
        seed = self._get_seed("slack")
        env_keys = set(seed["env_template"].keys())
        assert "SLACK_BOT_TOKEN" not in env_keys, (
            "SLACK_BOT_TOKEN is wrong — use SLACK_MCP_XOXP_TOKEN"
        )
        assert "SLACK_MCP_XOXP_TOKEN" in env_keys, "Missing SLACK_MCP_XOXP_TOKEN"
        assert "SLACK_MCP_XOXB_TOKEN" in env_keys, "Missing SLACK_MCP_XOXB_TOKEN"


class TestNotionIsGatewayOnly:
    """Notion's tool names now come from the registry, not from a stdio server.

    The `API-*` names these tests pinned were the wire names of
    `@notionhq/notion-mcp-server`, retired with the gateway migration. Asserting
    they are ABSENT is the half of the old contract still worth keeping: a
    reappearing `API-*` seed would mean the stdio server was reinstated
    alongside the gateway, offering agents two names for one action.
    """

    def test_no_stdio_era_tool_names_remain(self):
        from src.tools.catalog import EXTERNAL_TOOL_SEEDS

        notion_names = {s.name for s in EXTERNAL_TOOL_SEEDS if s.server == "notion"}
        assert notion_names, "notion offers no tools at all"
        stale = {n for n in notion_names if n.startswith("API-")}
        assert not stale, f"stdio-era Notion tool names still seeded: {stale}"

    def test_tool_names_are_the_registry_action_names(self):
        from src.integrations.gateway_actions import PROVIDER_REGISTRY
        from src.integrations.gateway_naming import action_id_to_tool_name
        from src.tools.catalog import EXTERNAL_TOOL_SEEDS

        notion_names = {s.name for s in EXTERNAL_TOOL_SEEDS if s.server == "notion"}
        expected = {
            action_id_to_tool_name(a.action_id) for a in PROVIDER_REGISTRY["notion"].actions
        }
        assert notion_names == expected


class TestAuthProviderLabels:
    """Servers with OAuth callback routes must have OAuth-aware auth_provider."""

    def _get_seed(self, server_name: str) -> dict:
        return next(s for s in _DEFAULT_INSTALLATIONS if s["server_name"] == server_name)

    def test_github_auth_provider_is_platform_jwt(self):
        # GitHub is gateway-routed: Muldro authenticates to the OpenConnector
        # adapter via platform_jwt; the GitHub OAuth token itself lives in
        # OpenConnector, not OAuthManager.
        seed = self._get_seed("github")
        actual = seed["auth_provider"]
        assert actual == "platform_jwt", (
            f"GitHub is gateway-routed — auth_provider must be 'platform_jwt', got '{actual}'"
        )

    def test_notion_auth_provider_is_platform_jwt(self):
        # Notion is gateway-routed like GitHub: the Notion OAuth token lives in
        # OpenConnector, and Muldro authenticates to the adapter with a platform
        # JWT. It previously declared "notion" and authenticated an npx child
        # with NOTION_TOKEN out of the environment.
        seed = self._get_seed("notion")
        actual = seed["auth_provider"]
        assert actual == "platform_jwt", (
            f"Notion is gateway-routed — auth_provider must be 'platform_jwt', got '{actual}'"
        )
