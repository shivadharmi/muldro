"""Tests for orchestrator runtime contracts (Pydantic models)."""

import pytest
from pydantic import ValidationError

from src.orchestrator.contracts import (
    AgentEnvelope,
    AgentResult,
    DomainEvent,
    StepResult,
    StepState,
    ToolCallRequest,
    ToolCallResult,
)

# ── AgentEnvelope ────────────────────────────────────────────────────


class TestAgentEnvelope:
    def test_valid(self):
        e = AgentEnvelope(
            agent_name="planner",
            message="Plan this",
            tools_available=["search_memory"],
        )
        assert e.agent_name == "planner"
        assert len(e.tools_available) == 1

    def test_defaults(self):
        e = AgentEnvelope(agent_name="perceiver", message="hi")
        assert e.context == ""
        assert e.tools_available == []


# ── AgentResult ──────────────────────────────────────────────────────


class TestAgentResult:
    def test_valid(self):
        r = AgentResult(
            agent_name="perceiver",
            response_text="Found 3 results",
            tools_called=["search_memory"],
            tokens_used=150,
        )
        assert r.agent_name == "perceiver"
        assert r.tokens_used == 150

    def test_defaults(self):
        r = AgentResult(agent_name="persona")
        assert r.response_text is None
        assert r.tools_called == []
        assert r.tokens_used == 0


# ── StepResult ───────────────────────────────────────────────────────


class TestStepResult:
    def test_valid(self):
        s = StepResult(step_id="step_001", status="completed", duration_ms=42)
        assert s.status == "completed"

    def test_with_error(self):
        s = StepResult(step_id="step_002", status="failed", error="Timeout")
        assert s.error == "Timeout"


# ── ToolCallRequest ──────────────────────────────────────────────────


class TestToolCallRequest:
    def test_valid(self):
        t = ToolCallRequest(tool_name="search_memory", parameters={"query": "hi"})
        assert t.tool_name == "search_memory"
        assert not t.requires_approval

    def test_approval_flag(self):
        t = ToolCallRequest(tool_name="send_email", requires_approval=True)
        assert t.requires_approval


# ── ToolCallResult ───────────────────────────────────────────────────


class TestToolCallResult:
    def test_success(self):
        t = ToolCallResult(tool_name="search_memory", status="success", result={"data": []})
        assert t.status == "success"

    def test_error(self):
        t = ToolCallResult(tool_name="bad_tool", status="error", error="Not found")
        assert t.error == "Not found"

    def test_blocked(self):
        t = ToolCallResult(tool_name="send_email", status="blocked")
        assert t.status == "blocked"

    def test_invalid_status(self):
        with pytest.raises(ValidationError):
            ToolCallResult(tool_name="x", status="banana")


# ── DomainEvent ──────────────────────────────────────────────────────


class TestDomainEvent:
    def test_valid(self):
        e = DomainEvent(event_type="run.started", user_id="usr_1", payload={"run_id": "r1"})
        assert e.event_type == "run.started"
        assert e.timestamp is not None

    def test_with_trace_id(self):
        e = DomainEvent(event_type="step.failed", trace_id="tr_abc")
        assert e.trace_id == "tr_abc"

    def test_defaults(self):
        e = DomainEvent(event_type="test")
        assert e.user_id == ""
        assert e.payload == {}
        assert e.trace_id is None

    def test_serialization(self):
        e = DomainEvent(event_type="memory.created", user_id="u1", payload={"id": "m1"})
        d = e.model_dump()
        assert d["event_type"] == "memory.created"
        assert "timestamp" in d


class TestStepState:
    def test_new_fields_default_none(self):
        s = StepState(step_id="step_001", description="Search KB", status="pending")
        assert s.started_at is None
        assert s.completed_at is None
        assert s.timeout_seconds is None
        assert s.error is None
        assert s.retry_count is None

    def test_new_fields_populated(self):
        s = StepState(
            step_id="step_001",
            description="Search KB",
            status="executing",
            started_at="2026-04-13T10:00:00Z",
            timeout_seconds=60,
        )
        assert s.started_at == "2026-04-13T10:00:00Z"
        assert s.timeout_seconds == 60

    def test_extra_fields_ignored(self):
        s = StepState(
            step_id="step_001",
            description="x",
            status="completed",
            unknown_field="ignored",
        )
        assert not hasattr(s, "unknown_field")

    def test_completed_with_all_fields(self):
        s = StepState(
            step_id="step_001",
            description="Send email",
            status="failed",
            duration_ms=47000,
            started_at="2026-04-13T10:00:00Z",
            completed_at="2026-04-13T10:00:47Z",
            timeout_seconds=60,
            error={"message": "SMTP timeout", "code": "ETIMEDOUT"},
            retry_count=3,
        )
        assert s.error["message"] == "SMTP timeout"
        assert s.retry_count == 3
