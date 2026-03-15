"""Tests for trace API routes."""

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from src.api.app import app

client = TestClient(app)


class TestTraceRoutes:
    """Test trace API endpoints."""

    @patch("src.api.routes_traces._get_trace_store")
    def test_list_traces_returns_trace_list_response(self, mock_get_store):
        """Test GET /v1/traces returns TraceListResponse."""
        mock_store = AsyncMock()
        mock_store.search_traces = AsyncMock(
            return_value=[
                {
                    "trace_id": "trace_001",
                    "trigger": "user_message",
                    "started_at": "2026-03-16T10:00:00Z",
                    "ended_at": "2026-03-16T10:00:05Z",
                    "spans": [],
                }
            ]
        )
        mock_get_store.return_value = mock_store

        resp = client.get("/v1/traces")
        assert resp.status_code == 200
        data = resp.json()
        assert "traces" in data
        assert "count" in data
        assert data["count"] == 1
        assert len(data["traces"]) == 1
        assert data["traces"][0]["trace_id"] == "trace_001"

    @patch("src.api.routes_traces._get_trace_store")
    def test_list_traces_with_filters(self, mock_get_store):
        """Test GET /v1/traces with filter parameters."""
        mock_store = AsyncMock()
        mock_store.search_traces = AsyncMock(return_value=[])
        mock_get_store.return_value = mock_store

        resp = client.get("/v1/traces?trigger=scheduled_briefing&agent_name=presenter&limit=10")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0

        # Verify search_traces was called with correct params
        mock_store.search_traces.assert_called_once()
        call_kwargs = mock_store.search_traces.call_args.kwargs
        assert call_kwargs["trigger"] == "scheduled_briefing"
        assert call_kwargs["agent_name"] == "presenter"
        assert call_kwargs["limit"] == 10

    @patch("src.api.routes_traces._get_trace_store")
    def test_agent_performance_returns_aggregates(self, mock_get_store):
        """Test GET /v1/traces/performance returns AgentPerformanceResponse."""
        mock_store = AsyncMock()
        mock_store.get_agent_performance = AsyncMock(
            return_value={
                "planner": {
                    "call_count": 5,
                    "total_duration_ms": 15000,
                    "avg_duration_ms": 3000,
                    "total_input_tokens": 5000,
                    "total_output_tokens": 1000,
                    "error_count": 0,
                },
                "presenter": {
                    "call_count": 3,
                    "total_duration_ms": 6000,
                    "avg_duration_ms": 2000,
                    "total_input_tokens": 3000,
                    "total_output_tokens": 500,
                    "error_count": 1,
                },
            }
        )
        mock_get_store.return_value = mock_store

        resp = client.get("/v1/traces/performance")
        assert resp.status_code == 200
        data = resp.json()
        assert "agents" in data
        assert "time_range_hours" in data
        assert data["time_range_hours"] == 24
        assert "planner" in data["agents"]
        assert "presenter" in data["agents"]
        assert data["agents"]["planner"]["call_count"] == 5
        assert data["agents"]["planner"]["avg_duration_ms"] == 3000

    @patch("src.api.routes_traces._get_trace_store")
    def test_agent_performance_custom_time_range(self, mock_get_store):
        """Test GET /v1/traces/performance with custom time range."""
        mock_store = AsyncMock()
        mock_store.get_agent_performance = AsyncMock(return_value={})
        mock_get_store.return_value = mock_store

        resp = client.get("/v1/traces/performance?time_range_hours=48")
        assert resp.status_code == 200
        data = resp.json()
        assert data["time_range_hours"] == 48

        # Verify get_agent_performance was called with correct time_range
        mock_store.get_agent_performance.assert_called_once()
        call_kwargs = mock_store.get_agent_performance.call_args.kwargs
        assert call_kwargs["time_range_hours"] == 48

    @patch("src.api.routes_traces._get_trace_store")
    def test_get_trace_by_id_success(self, mock_get_store):
        """Test GET /v1/traces/{trace_id} returns trace details."""
        mock_store = AsyncMock()
        mock_store.get_trace = AsyncMock(
            return_value={
                "trace_id": "trace_abc",
                "trigger": "user_message",
                "started_at": "2026-03-16T10:00:00Z",
                "ended_at": "2026-03-16T10:00:05Z",
                "duration_ms": 5000,
                "total_input_tokens": 1200,
                "total_output_tokens": 300,
                "spans": [
                    {
                        "span_id": "span_1",
                        "agent_name": "planner",
                        "started_at": "2026-03-16T10:00:00Z",
                    }
                ],
            }
        )
        mock_get_store.return_value = mock_store

        resp = client.get("/v1/traces/trace_abc")
        assert resp.status_code == 200
        data = resp.json()
        assert data["trace_id"] == "trace_abc"
        assert data["trigger"] == "user_message"
        assert data["duration_ms"] == 5000
        assert data["total_input_tokens"] == 1200
        assert len(data["spans"]) == 1

    @patch("src.api.routes_traces._get_trace_store")
    def test_get_trace_by_id_not_found(self, mock_get_store):
        """Test GET /v1/traces/{trace_id} returns 404 for missing traces."""
        mock_store = AsyncMock()
        mock_store.get_trace = AsyncMock(return_value=None)
        mock_get_store.return_value = mock_store

        resp = client.get("/v1/traces/trace_nonexistent")
        assert resp.status_code == 404
        data = resp.json()
        assert "detail" in data
        assert "not found" in data["detail"].lower()

    @patch("src.api.routes_traces._get_trace_store")
    def test_list_traces_empty_results(self, mock_get_store):
        """Test GET /v1/traces with no results."""
        mock_store = AsyncMock()
        mock_store.search_traces = AsyncMock(return_value=[])
        mock_get_store.return_value = mock_store

        resp = client.get("/v1/traces")
        assert resp.status_code == 200
        data = resp.json()
        assert data["traces"] == []
        assert data["count"] == 0
