"""Tests for Phase 8 — Observability & Evals.

Tests trace DB persistence, aggregate metrics, runs API,
eval harness, and metrics service wiring.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.conftest import TEST_USER_ID

# ── Trace Model ──────────────────────────────────────────────


class TestTraceModel:
    def test_trace_model_fields(self):
        from src.models.traces import Trace

        assert Trace.__tablename__ == "traces"
        cols = {c.name for c in Trace.__table__.columns}
        assert "trace_id" in cols
        assert "user_id" in cols
        assert "trigger" in cols
        assert "status" in cols
        assert "duration_ms" in cols
        assert "total_input_tokens" in cols
        assert "total_output_tokens" in cols
        assert "total_cost_usd" in cols
        assert "span_count" in cols
        assert "error_count" in cols
        assert "agents_invoked" in cols
        assert "tools_called" in cols
        assert "context_summary" in cols
        assert "final_result" in cols
        assert "memory_writes" in cols
        assert "spans_json" in cols

    def test_model_call_fields(self):
        from src.models.traces import ModelCall

        assert ModelCall.__tablename__ == "model_calls"
        cols = {c.name for c in ModelCall.__table__.columns}
        assert "call_id" in cols
        assert "trace_id" in cols
        assert "agent_name" in cols
        assert "model" in cols
        assert "input_tokens" in cols
        assert "output_tokens" in cols
        assert "cost_usd" in cols
        assert "duration_ms" in cols
        assert "decision" in cols
        assert "error" in cols


# ── TraceStore In-Memory ─────────────────────────────────────


class TestTraceStoreInMemory:
    @pytest.mark.asyncio
    async def test_store_and_retrieve(self):
        from src.services.trace_store import TraceStore

        store = TraceStore()
        trace_dict = {
            "trace_id": "trace_test1",
            "trigger": "user_message",
            "started_at": "2026-03-17T10:00:00+00:00",
            "ended_at": "2026-03-17T10:00:01+00:00",
            "duration_ms": 1000,
            "total_input_tokens": 500,
            "total_output_tokens": 200,
            "spans": [
                {
                    "span_id": "span_1",
                    "agent_name": "planner",
                    "duration_ms": 500,
                    "input_tokens": 300,
                    "output_tokens": 100,
                }
            ],
        }
        tid = await store.store_trace(trace_dict, user_id=TEST_USER_ID)
        assert tid == "trace_test1"

        retrieved = await store.get_trace("trace_test1")
        assert retrieved is not None
        assert retrieved["trigger"] == "user_message"

    @pytest.mark.asyncio
    async def test_search_by_trigger(self):
        from src.services.trace_store import TraceStore

        store = TraceStore()
        now = datetime.now(timezone.utc).isoformat()
        await store.store_trace(
            {"trace_id": "t1", "trigger": "user_message", "started_at": now, "spans": []},
            user_id=TEST_USER_ID,
        )
        await store.store_trace(
            {"trace_id": "t2", "trigger": "scheduled", "started_at": now, "spans": []},
            user_id=TEST_USER_ID,
        )

        results = await store.search_traces(trigger="user_message")
        assert len(results) == 1
        assert results[0]["trace_id"] == "t1"

    @pytest.mark.asyncio
    async def test_agent_performance_aggregation(self):
        from src.services.trace_store import TraceStore

        store = TraceStore()
        now = datetime.now(timezone.utc).isoformat()
        await store.store_trace(
            {
                "trace_id": "t1",
                "trigger": "test",
                "started_at": now,
                "spans": [
                    {
                        "agent_name": "planner",
                        "duration_ms": 100,
                        "input_tokens": 50,
                        "output_tokens": 20,
                    },
                    {
                        "agent_name": "planner",
                        "duration_ms": 200,
                        "input_tokens": 80,
                        "output_tokens": 30,
                        "error": "timeout",
                    },
                ],
            },
            user_id=TEST_USER_ID,
        )

        perf = await store.get_agent_performance()
        assert "planner" in perf
        assert perf["planner"]["call_count"] == 2
        assert perf["planner"]["error_count"] == 1
        assert perf["planner"]["avg_duration_ms"] == 150

    @pytest.mark.asyncio
    async def test_aggregate_metrics_fallback(self):
        from src.services.trace_store import TraceStore

        store = TraceStore()
        now = datetime.now(timezone.utc).isoformat()
        await store.store_trace(
            {
                "trace_id": "t1",
                "trigger": "test",
                "started_at": now,
                "ended_at": now,
                "duration_ms": 500,
                "spans": [],
            },
            user_id=TEST_USER_ID,
        )
        await store.store_trace(
            {
                "trace_id": "t2",
                "trigger": "test",
                "started_at": now,
                "spans": [],
            },
            user_id=TEST_USER_ID,
        )

        metrics = await store.get_aggregate_metrics()
        assert metrics["total_traces"] == 2
        assert metrics["completed"] == 1
        assert metrics["failed"] == 1
        assert metrics["success_rate"] == 0.5

    @pytest.mark.asyncio
    async def test_get_nonexistent_trace(self):
        from src.services.trace_store import TraceStore

        store = TraceStore()
        result = await store.get_trace("nonexistent")
        assert result is None


# ── TraceStore DB Persistence ────────────────────────────────


class TestTraceStoreDB:
    @pytest.mark.asyncio
    async def test_store_to_db_creates_trace_and_calls(self):
        """DB store should create Trace + ModelCall records."""
        from src.services.trace_store import TraceStore

        mock_db = MagicMock()
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)

        db_factory = MagicMock(return_value=mock_db)

        store = TraceStore(db_factory=db_factory)
        trace_dict = {
            "trace_id": "trace_db1",
            "trigger": "user_message",
            "started_at": "2026-03-17T10:00:00+00:00",
            "ended_at": "2026-03-17T10:00:01+00:00",
            "duration_ms": 1000,
            "total_input_tokens": 500,
            "total_output_tokens": 200,
            "spans": [
                {
                    "span_id": "span_1",
                    "agent_name": "planner",
                    "model": "claude-sonnet-4-20250514",
                    "duration_ms": 500,
                    "input_tokens": 300,
                    "output_tokens": 100,
                },
                {
                    "span_id": "span_2",
                    "agent_name": "governor",
                    "model": "claude-sonnet-4-20250514",
                    "duration_ms": 200,
                    "input_tokens": 100,
                    "output_tokens": 50,
                },
            ],
        }

        tid = await store.store_trace(trace_dict, user_id="usr_1")
        assert tid == "trace_db1"

        # Should add 1 Trace + 2 ModelCalls = 3 adds
        assert mock_db.add.call_count == 3
        mock_db.commit.assert_called_once()


# ── Metrics Service ──────────────────────────────────────────


class TestMetricsService:
    def test_record_event_ingested(self):
        from src.services.metrics_service import MetricsService

        MetricsService.record_event_ingested("gmail", "email.received")

    def test_record_execution_completed(self):
        from src.services.metrics_service import MetricsService

        MetricsService.record_execution_completed("completed")

    def test_record_agent_call(self):
        from src.services.metrics_service import MetricsService

        MetricsService.record_agent_call("planner", "claude-sonnet-4-20250514", 1500.0)

    def test_record_tool_call(self):
        from src.services.metrics_service import MetricsService

        MetricsService.record_tool_call("search_memory", "success")

    def test_record_notification_sent(self):
        from src.services.metrics_service import MetricsService

        MetricsService.record_notification_sent("info_update", "telegram")

    def test_record_trigger_fired(self):
        from src.services.metrics_service import MetricsService

        MetricsService.record_trigger_fired("notify")

    def test_record_memory_write(self):
        from src.services.metrics_service import MetricsService

        MetricsService.record_memory_write("fact")

    def test_generate_metrics_returns_bytes(self):
        from src.services.metrics_service import MetricsService

        data = MetricsService.generate_metrics()
        assert isinstance(data, bytes)
        assert b"jarvis_" in data

    def test_set_active_runs(self):
        from src.services.metrics_service import MetricsService

        MetricsService.set_active_runs(5)

    def test_set_pending_approvals(self):
        from src.services.metrics_service import MetricsService

        MetricsService.set_pending_approvals(3)


# ── Eval Harness ─────────────────────────────────────────────


class TestEvalHarness:
    def test_load_all_datasets(self):
        from tests.eval.eval_runner import AVAILABLE_SUITES, load_dataset

        for suite in AVAILABLE_SUITES:
            cases = load_dataset(suite)
            assert len(cases) > 0, f"Suite '{suite}' has no cases"

    def test_run_inbox_triage_suite(self):
        from tests.eval.eval_runner import run_suite

        result = run_suite("inbox_triage")
        assert result.total > 0
        assert result.avg_score > 0.0

    def test_run_all_suites_pass(self):
        from tests.eval.eval_runner import AVAILABLE_SUITES, run_suite

        for suite in AVAILABLE_SUITES:
            result = run_suite(suite)
            assert result.total > 0, f"Suite '{suite}' empty"
            assert result.failed == 0, f"Suite '{suite}' has {result.failed} failures"

    def test_eval_case_structure(self):
        from tests.eval.eval_runner import EvalCase, evaluate_case

        case = EvalCase(
            case_id="test_1",
            suite="test",
            input_data={"emails": [], "groups": []},
            expected={"required_fields": ["emails", "groups"]},
        )
        result = evaluate_case(case)
        assert result.passed is True
        assert result.score == 1.0


# ── trace_to_dict Helper ─────────────────────────────────────


class TestTraceToDict:
    def test_trace_to_dict(self):
        from src.services.trace_store import _trace_to_dict

        trace = MagicMock()
        trace.trace_id = "trace_1"
        trace.user_id = "usr_1"
        trace.trigger = "user_message"
        trace.status = "completed"
        trace.started_at = datetime(2026, 3, 17, 10, 0, 0, tzinfo=timezone.utc)
        trace.ended_at = datetime(2026, 3, 17, 10, 0, 1, tzinfo=timezone.utc)
        trace.duration_ms = 1000
        trace.total_input_tokens = 500
        trace.total_output_tokens = 200
        trace.total_cache_creation_tokens = 100
        trace.total_cache_read_tokens = 50
        trace.total_thinking_tokens = 80
        trace.total_cost_usd = 0.01
        trace.span_count = 2
        trace.error_count = 0
        trace.agents_invoked = ["planner", "governor"]
        trace.tools_called = ["search_memory"]
        trace.context_summary = "Test context"
        trace.final_result = "Done"
        trace.memory_writes = 1
        trace.approval_ids = None
        trace.spans_json = [{"agent_name": "planner"}]
        trace.metadata_json = None

        result = _trace_to_dict(trace)
        assert result["trace_id"] == "trace_1"
        assert result["status"] == "completed"
        assert result["duration_ms"] == 1000
        assert result["agents_invoked"] == ["planner", "governor"]
        assert len(result["spans"]) == 1
        assert result["total_cache_creation_tokens"] == 100
        assert result["total_cache_read_tokens"] == 50
        assert result["total_thinking_tokens"] == 80
        assert result["total_cost_usd"] == 0.01


# ── Runs API Response Models ─────────────────────────────────


class TestRunsModels:
    def test_run_response_model(self):
        from src.api.routes_runs import RunResponse

        r = RunResponse(
            run_id="run_1",
            plan_id="plan_1",
            user_id="usr_1",
            status="completed",
        )
        assert r.run_id == "run_1"
        assert r.step_count == 0

    def test_step_response_model(self):
        from src.api.routes_runs import StepResponse

        s = StepResponse(
            step_id="step_1",
            task_id="task_1",
            status="completed",
        )
        assert s.step_id == "step_1"

    def test_artifact_response_model(self):
        from src.api.routes_runs import ArtifactResponse

        a = ArtifactResponse(
            artifact_id="art_1",
            artifact_type="document",
        )
        assert a.artifact_id == "art_1"


# ── Dashboard Enhancement ────────────────────────────────────


class TestDashboardEnhancement:
    def test_health_dashboard_includes_traces_and_runs(self):
        from src.api.routes_health import HealthDashboardResponse

        resp = HealthDashboardResponse(
            budget={},
            queues={},
            observations={},
            agents={},
            traces={"total_traces": 10, "success_rate": 0.9},
            runs={"total_runs_today": 5, "success_rate": 0.8},
        )
        assert resp.traces["total_traces"] == 10
        assert resp.runs["total_runs_today"] == 5

    def test_health_dashboard_default_empty(self):
        from src.api.routes_health import HealthDashboardResponse

        resp = HealthDashboardResponse(
            budget={},
            queues={},
            observations={},
            agents={},
        )
        assert resp.traces == {}
        assert resp.runs == {}


# ── Trace API Models ─────────────────────────────────────────


class TestTraceAPIModels:
    def test_aggregate_metrics_response(self):
        from src.api.routes_traces import AggregateMetricsResponse

        resp = AggregateMetricsResponse(
            total_traces=100,
            completed=90,
            failed=10,
            success_rate=0.9,
            failure_rate=0.1,
            avg_duration_ms=1500,
        )
        assert resp.success_rate == 0.9

    def test_trace_list_response(self):
        from src.api.routes_traces import TraceListResponse, TraceSummary

        summary = TraceSummary(trace_id="t1", trigger="user_message")
        resp = TraceListResponse(traces=[summary], count=1)
        assert resp.count == 1
        assert resp.traces[0].trace_id == "t1"
