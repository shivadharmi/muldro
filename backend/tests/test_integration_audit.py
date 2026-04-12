"""Tests for integration audit logger."""

from unittest.mock import AsyncMock, MagicMock

from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID


class TestAuditLogger:
    async def test_log_tool_call(self):
        from src.integrations.audit_logger import IntegrationAuditLogger

        db = AsyncMock()
        db.add = MagicMock()
        db.flush = AsyncMock()

        logger = IntegrationAuditLogger(db, TEST_WORKSPACE_ID, TEST_USER_ID)
        await logger.log_tool_call(
            server_name="github",
            tool_name="create_issue",
            trust_tier="T1",
            tool_input={"title": "Bug fix", "body": "Details"},
            output_summary="Issue #42 created",
            status="success",
            latency_ms=150,
        )

        db.add.assert_called_once()
        db.flush.assert_called_once()

    async def test_log_tool_call_redacts_sensitive(self):
        from src.integrations.audit_logger import _redact_dict

        redacted = _redact_dict(
            {
                "query": "search term",
                "api_key": "sk-12345",
                "nested": {"token": "abc", "value": "ok"},
            }
        )

        assert redacted["query"] == "search term"
        assert redacted["api_key"] == "[REDACTED]"
        assert redacted["nested"]["token"] == "[REDACTED]"
        assert redacted["nested"]["value"] == "ok"

    async def test_log_action(self):
        from src.integrations.audit_logger import IntegrationAuditLogger

        db = AsyncMock()
        db.add = MagicMock()
        db.flush = AsyncMock()

        logger = IntegrationAuditLogger(db, TEST_WORKSPACE_ID, TEST_USER_ID)
        await logger.log_action(
            server_name="test-server",
            action="install",
            trust_tier="T2",
            details={"display_name": "Test"},
        )

        db.add.assert_called_once()
        db.flush.assert_called_once()

    def test_hash_input_deterministic(self):
        from src.integrations.audit_logger import _hash_input

        data = {"b": 2, "a": 1}
        h1 = _hash_input(data)
        h2 = _hash_input({"a": 1, "b": 2})
        assert h1 == h2

    def test_redact_truncates_long_strings(self):
        from src.integrations.audit_logger import _redact_dict

        result = _redact_dict({"long": "x" * 1000})
        assert result["long"].endswith("...[truncated]")
        assert len(result["long"]) < 600

    def test_redact_caps_list_length(self):
        from src.integrations.audit_logger import _redact_dict

        result = _redact_dict({"items": list(range(50))})
        assert len(result["items"]) == 20


class TestAgentAnalytics:
    async def test_empty_report(self):
        from src.services.agent_analytics import AgentAnalyticsService

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)

        svc = AgentAnalyticsService(db, TEST_WORKSPACE_ID)
        report = await svc.get_report()
        assert report.agents == []
        assert report.total_calls_24h == 0
        assert report.busiest_agent is None
