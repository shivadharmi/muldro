"""Tests for MCP server seed installations — verifies known bugs are fixed."""

from src.integrations.seed_installations import _DEFAULT_INSTALLATIONS


class TestSeedInstallations:
    def _get_seed(self, server_name: str) -> dict:
        return next(s for s in _DEFAULT_INSTALLATIONS if s["server_name"] == server_name)

    def test_google_workspace_executable(self):
        """Google Workspace seed must use google-workspace-worker, not google-workspace-mcp."""
        seed = self._get_seed("google-workspace")
        assert seed["args"] == ["google-workspace-worker"], (
            f"Wrong executable: {seed['args']} — should be ['google-workspace-worker']"
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


class TestNotionToolNames:
    def test_notion_tools_in_default_tools(self):
        """All 6 Notion MCP tools must have API- prefix in _DEFAULT_TOOLS."""
        from src.services.tool_registry import _DEFAULT_TOOLS

        notion_tools = [t for t in _DEFAULT_TOOLS if t.get("connector_type") == "notion"]
        notion_names = {t["name"] for t in notion_tools}
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
        """Notion tools must use API- prefixed names in TOOL_TO_CAPABILITY."""
        from src.integrations.capabilities import TOOL_TO_CAPABILITY

        expected = {
            "API-post-page": "doc.create",
            "API-patch-page": "doc.update",
            "API-retrieve-a-page": "doc.get",
            "API-query-data-source": "doc.query",
            "API-create-a-comment": "doc.comment",
            "API-patch-block-children": "doc.append",
        }
        for tool_name, expected_cap in expected.items():
            assert TOOL_TO_CAPABILITY.get(tool_name) == expected_cap, (
                f"{tool_name} should map to {expected_cap}, got {TOOL_TO_CAPABILITY.get(tool_name)}"
            )
        for old in (
            "create-a-page",
            "update-a-page",
            "retrieve-a-page",
            "query-data-source",
            "create-a-comment",
            "append-block-children",
        ):
            assert old not in TOOL_TO_CAPABILITY, f"Old name '{old}' still in TOOL_TO_CAPABILITY"


class TestLinearToolNames:
    def test_linear_no_wrong_aliases_in_defaults(self):
        from src.services.tool_registry import _DEFAULT_TOOLS

        names = {t["name"] for t in _DEFAULT_TOOLS}
        assert "linear_comment" not in names, "linear_comment is wrong — use linear_create_comment"
        assert "linear_list_issues" not in names, (
            "linear_list_issues is wrong — use linear_search_issues"
        )

    def test_linear_no_wrong_aliases_in_capabilities(self):
        from src.integrations.capabilities import TOOL_TO_CAPABILITY

        assert "linear_comment" not in TOOL_TO_CAPABILITY
        assert "linear_list_issues" not in TOOL_TO_CAPABILITY
