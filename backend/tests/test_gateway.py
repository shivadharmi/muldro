"""Tests for MCP session pool, circuit breaker, and tool normalizer."""

from src.integrations.session_pool import UserMCPSessionPool
from src.integrations.tool_normalizer import ToolNameNormalizer, camel_to_snake, kebab_to_snake
from src.services.mcp_resilience import MCPCircuitBreaker


class TestCircuitBreaker:
    def test_initial_state(self):
        cb = MCPCircuitBreaker()
        assert cb.is_available("test-server")

    def test_record_success_resets(self):
        cb = MCPCircuitBreaker()
        cb.record_failure("test-server")
        cb.record_failure("test-server")
        cb.record_success("test-server")
        assert cb.is_available("test-server")

    def test_opens_after_threshold(self):
        cb = MCPCircuitBreaker()
        for _ in range(3):  # default threshold
            cb.record_failure("test-server")
        assert not cb.is_available("test-server")

    def test_stays_closed_below_threshold(self):
        cb = MCPCircuitBreaker()
        cb.record_failure("test-server")
        cb.record_failure("test-server")
        assert cb.is_available("test-server")


class TestToolNameNormalizer:
    def test_camel_to_snake(self):
        assert camel_to_snake("sendGmailDraft") == "send_gmail_draft"
        assert camel_to_snake("createJiraIssue") == "create_jira_issue"
        assert camel_to_snake("listCalendarEvents") == "list_calendar_events"

    def test_kebab_to_snake(self):
        assert kebab_to_snake("create-a-page") == "create_a_page"
        assert kebab_to_snake("query-data-source") == "query_data_source"

    def test_normalize_with_server_prefix(self):
        n = ToolNameNormalizer()
        result = n.normalize("google_workspace_sendGmailDraft", server_name="google-workspace")
        assert result == "send_gmail_draft"

    def test_normalize_kebab_with_prefix(self):
        n = ToolNameNormalizer()
        result = n.normalize("notion_create-a-page", server_name="notion")
        assert result == "create_a_page"

    def test_normalize_already_snake_case(self):
        n = ToolNameNormalizer()
        assert n.normalize("search_memory") == "search_memory"
        assert n.normalize("issue_write") == "issue_write"

    def test_static_aliases(self):
        n = ToolNameNormalizer(extra_aliases={"gmail_send_email": "gmail_send"})
        assert n.normalize("gmail_send_email") == "gmail_send"

    def test_register_server_tools(self):
        n = ToolNameNormalizer()
        tools = [
            {"name": "google_workspace_sendGmailDraft"},
            {"name": "google_workspace_listCalendarEvents"},
        ]
        mapping = n.register_server_tools("google-workspace", tools)
        assert mapping["send_gmail_draft"] == "google_workspace_sendGmailDraft"
        assert mapping["list_calendar_events"] == "google_workspace_listCalendarEvents"

    def test_bidirectional_lookup(self):
        n = ToolNameNormalizer()
        tools = [{"name": "github_create_pull_request"}]
        n.register_server_tools("github", tools)
        raw = n.to_raw("create_pull_request", "github")
        assert raw == "github_create_pull_request"


class TestStdioAuthInjection:
    def test_inject_github_token(self):
        """Should inject GITHUB_PERSONAL_ACCESS_TOKEN for github server."""
        from src.integrations.session_pool import _inject_stdio_auth

        config: dict = {"transport": "stdio", "command": "docker", "env": {}}
        _inject_stdio_auth(config, "github", "ghp_test123")
        assert config["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"] == "ghp_test123"

    def test_inject_slack_token(self):
        """Should inject SLACK_BOT_TOKEN for slack server."""
        from src.integrations.session_pool import _inject_stdio_auth

        config: dict = {"transport": "stdio", "command": "npx"}
        _inject_stdio_auth(config, "slack", "xoxb-test")
        assert config["env"]["SLACK_BOT_TOKEN"] == "xoxb-test"

    def test_inject_linear_token(self):
        """Should inject LINEAR_ACCESS_TOKEN for linear server."""
        from src.integrations.session_pool import _inject_stdio_auth

        config: dict = {"transport": "stdio", "command": "npx"}
        _inject_stdio_auth(config, "linear", "lin_test")
        assert config["env"]["LINEAR_ACCESS_TOKEN"] == "lin_test"

    def test_inject_notion_token(self):
        """Should inject NOTION_TOKEN for notion server."""
        from src.integrations.session_pool import _inject_stdio_auth

        config: dict = {"transport": "stdio", "command": "npx"}
        _inject_stdio_auth(config, "notion", "secret_test")
        assert config["env"]["NOTION_TOKEN"] == "secret_test"

    def test_inject_unknown_server_noop(self):
        """Unknown servers should not have auth injected and not crash."""
        from src.integrations.session_pool import _inject_stdio_auth

        config: dict = {"transport": "stdio", "command": "npx"}
        _inject_stdio_auth(config, "custom-server", "tok_123")
        assert "env" not in config or "tok_123" not in config.get("env", {}).values()

    def test_inject_google_workspace_excluded(self):
        """Google Workspace uses file-based auth — should not inject raw token."""
        from src.integrations.session_pool import _inject_stdio_auth

        config: dict = {"transport": "stdio", "command": "uvx", "env": {}}
        _inject_stdio_auth(config, "google-workspace", "ya29.token")
        # Token should NOT be in env — google uses file-based auth
        assert "ya29.token" not in config["env"].values()

    def test_inject_preserves_existing_env(self):
        """Injecting a token should not clobber existing env vars."""
        from src.integrations.session_pool import _inject_stdio_auth

        config: dict = {"transport": "stdio", "env": {"EXISTING_VAR": "keep_me"}}
        _inject_stdio_auth(config, "github", "ghp_new")
        assert config["env"]["EXISTING_VAR"] == "keep_me"
        assert config["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"] == "ghp_new"


class TestUserMCPSessionPool:
    def test_empty_pool(self):
        pool = UserMCPSessionPool()
        assert not pool.is_pool_tool("some_tool")
        assert pool.get_server_for_tool("some_tool") is None
        assert pool.get_all_tools() == {}
        assert pool.get_health() == {}

    def test_register_server_config(self):
        pool = UserMCPSessionPool()
        pool.register_server_config(
            "test-server",
            {"transport": "sse", "url": "http://test"},
            workspace_id="ws_a",
        )
        # Config registered but no sessions yet
        assert pool.get_health() == {}

    def test_workspace_isolation_configs(self):
        """Two workspaces can register same server_name with different configs."""
        pool = UserMCPSessionPool()
        pool.register_server_config(
            "github",
            {"transport": "sse", "url": "http://a"},
            workspace_id="ws_a",
        )
        pool.register_server_config(
            "github",
            {"transport": "sse", "url": "http://b"},
            workspace_id="ws_b",
        )
        assert pool._server_configs[("ws_a", "github")]["url"] == "http://a"
        assert pool._server_configs[("ws_b", "github")]["url"] == "http://b"

    def test_is_pool_tool_with_workspace(self):
        """is_pool_tool should scope to workspace when provided."""
        pool = UserMCPSessionPool()
        pool._server_tools[("ws_a", "github")] = {"create_issue": "github_create_issue"}
        pool._server_tools[("ws_b", "slack")] = {"send_message": "slack_send_message"}

        assert pool.is_pool_tool("create_issue", workspace_id="ws_a")
        assert not pool.is_pool_tool("send_message", workspace_id="ws_a")
        assert pool.is_pool_tool("send_message", workspace_id="ws_b")
        # Without workspace_id, scans all
        assert pool.is_pool_tool("create_issue")
        assert pool.is_pool_tool("send_message")

    def test_get_server_for_tool_with_workspace(self):
        """get_server_for_tool should return correct server scoped by workspace."""
        pool = UserMCPSessionPool()
        pool._server_tools[("ws_a", "github")] = {"create_issue": "github_create_issue"}
        pool._server_tools[("ws_b", "linear")] = {"create_issue": "linear_create_issue"}

        assert pool.get_server_for_tool("create_issue", workspace_id="ws_a") == "github"
        assert pool.get_server_for_tool("create_issue", workspace_id="ws_b") == "linear"

    def test_get_all_tools_with_workspace(self):
        """get_all_tools should filter by workspace when provided."""
        pool = UserMCPSessionPool()
        pool._server_tools[("ws_a", "github")] = {"create_issue": "github_create_issue"}
        pool._server_tools[("ws_b", "slack")] = {"send_message": "slack_send_message"}

        ws_a_tools = pool.get_all_tools(workspace_id="ws_a")
        assert "create_issue" in ws_a_tools
        assert "send_message" not in ws_a_tools

        all_tools = pool.get_all_tools()
        assert "create_issue" in all_tools
        assert "send_message" in all_tools
