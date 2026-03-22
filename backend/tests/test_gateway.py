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
        result = n.normalize(
            "google_workspace_sendGmailDraft", server_name="google-workspace"
        )
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


class TestUserMCPSessionPool:
    def test_empty_pool(self):
        pool = UserMCPSessionPool()
        assert not pool.is_pool_tool("some_tool")
        assert pool.get_server_for_tool("some_tool") is None
        assert pool.get_all_tools() == {}
        assert pool.get_health() == {}

    def test_register_server_config(self):
        pool = UserMCPSessionPool()
        pool.register_server_config("test-server", {"transport": "sse", "url": "http://test"})
        # Config registered but no sessions yet
        assert pool.get_health() == {}
