"""Tests for MCP server seed installations — verifies known bugs are fixed."""

from src.integrations.seed_installations import _DEFAULT_INSTALLATIONS


class TestSeedInstallations:
    def _get_seed(self, server_name: str) -> dict:
        return next(s for s in _DEFAULT_INSTALLATIONS if s["server_name"] == server_name)

    def test_google_workspace_http_transport(self):
        """Google Workspace seed must use streamable-http with remote_url."""
        seed = self._get_seed("google-workspace")
        assert seed["transport"] == "streamable-http", (
            f"Expected streamable-http transport, got '{seed['transport']}'"
        )
        assert seed.get("remote_url"), "Google Workspace must have a remote_url for HTTP transport"
        assert seed.get("command") is None, "HTTP transport should not have a command"
        assert seed.get("args") is None, "HTTP transport should not have args"
        assert seed["auth_provider"] == "google", (
            f"auth_provider must be 'google' for OAuth, got '{seed['auth_provider']}'"
        )
        assert seed["env_template"] == {}, "HTTP service env vars live on Docker, not in seed"

    def test_slack_env_vars(self):
        """Slack seed must use SLACK_MCP_XOXP_TOKEN, not SLACK_BOT_TOKEN."""
        seed = self._get_seed("slack")
        env_keys = set(seed["env_template"].keys())
        assert "SLACK_BOT_TOKEN" not in env_keys, (
            "SLACK_BOT_TOKEN is wrong — use SLACK_MCP_XOXP_TOKEN"
        )
        assert "SLACK_MCP_XOXP_TOKEN" in env_keys, "Missing SLACK_MCP_XOXP_TOKEN"
        assert "SLACK_MCP_XOXB_TOKEN" in env_keys, "Missing SLACK_MCP_XOXB_TOKEN"


class TestNotionToolNames:
    def test_notion_tools_in_catalog(self):
        """All Notion MCP tools must have API- prefix in catalog."""
        from src.tools.catalog import EXTERNAL_TOOL_SEEDS

        notion_seeds = [s for s in EXTERNAL_TOOL_SEEDS if s.server == "notion"]
        notion_names = {s.name for s in notion_seeds}
        expected_api_names = {
            "API-post-page",
            "API-patch-page",
            "API-retrieve-a-page",
            "API-query-data-source",
            "API-create-a-comment",
            "API-patch-block-children",
        }
        for name in expected_api_names:
            assert name in notion_names, f"Missing Notion tool: {name}"
        wrong_names = {
            "create-a-page",
            "update-a-page",
            "retrieve-a-page",
            "query-data-source",
            "create-a-comment",
            "append-block-children",
        }
        present_wrong = wrong_names & notion_names
        assert not present_wrong, f"Wrong Notion names still present: {present_wrong}"

    def test_notion_capability_mappings(self):
        """Notion tools must have correct capabilities in catalog."""
        from src.tools.catalog import EXTERNAL_TOOL_SEEDS

        seed_by_name = {s.name: s.capability for s in EXTERNAL_TOOL_SEEDS}

        expected = {
            "API-post-page": "doc.create",
            "API-patch-page": "doc.update",
            "API-retrieve-a-page": "doc.get",
            "API-query-data-source": "doc.query",
            "API-create-a-comment": "doc.comment",
            "API-patch-block-children": "doc.append",
        }
        for tool_name, expected_cap in expected.items():
            assert seed_by_name.get(tool_name) == expected_cap, (
                f"{tool_name} should map to {expected_cap}, got {seed_by_name.get(tool_name)}"
            )


class TestLinearToolNames:
    def test_linear_no_wrong_aliases_in_catalog(self):
        from src.tools.catalog import EXTERNAL_TOOL_SEEDS

        names = {s.name for s in EXTERNAL_TOOL_SEEDS}
        assert "linear_comment" not in names, "linear_comment is wrong — use linear_create_comment"
        assert "linear_list_issues" not in names, (
            "linear_list_issues is wrong — use linear_search_issues"
        )

    def test_linear_no_wrong_aliases_in_capabilities(self):
        """Linear wrong aliases should not exist in any catalog entry."""
        from src.tools.catalog import EXTERNAL_TOOL_SEEDS

        names = {s.name for s in EXTERNAL_TOOL_SEEDS}
        assert "linear_comment" not in names
        assert "linear_list_issues" not in names


class TestAuthProviderLabels:
    """Servers with OAuth callback routes must have OAuth-aware auth_provider."""

    def _get_seed(self, server_name: str) -> dict:
        return next(s for s in _DEFAULT_INSTALLATIONS if s["server_name"] == server_name)

    def test_github_auth_provider_is_oauth(self):
        seed = self._get_seed("github")
        actual = seed["auth_provider"]
        assert actual == "github", (
            f"GitHub uses OAuth flow — auth_provider must be 'github', got '{actual}'"
        )

    def test_linear_auth_provider_is_oauth(self):
        seed = self._get_seed("linear")
        actual = seed["auth_provider"]
        assert actual == "linear", (
            f"Linear uses OAuth flow — auth_provider must be 'linear', got '{actual}'"
        )

    def test_notion_auth_provider_is_oauth(self):
        seed = self._get_seed("notion")
        actual = seed["auth_provider"]
        assert actual == "notion", (
            f"Notion uses OAuth flow — auth_provider must be 'notion', got '{actual}'"
        )
