"""Tests for orchestrator runtime contracts (Pydantic models)."""

import pytest
from pydantic import ValidationError

from src.orchestrator.contracts import (
    AgentEnvelope,
    AgentResult,
    DomainEvent,
    PlannerOutput,
    PlannerTask,
    StepResult,
    ToolCallRequest,
    ToolCallResult,
)

# ── PlannerTask ──────────────────────────────────────────────────────


class TestPlannerTask:
    def test_valid(self):
        t = PlannerTask(task_type="draft_email", input_data={"to": "a@b.com"})
        assert t.task_type == "draft_email"
        assert t.input_data["to"] == "a@b.com"

    def test_defaults(self):
        t = PlannerTask(task_type="fetch")
        assert t.input_data == {}

    def test_extra_ignored(self):
        t = PlannerTask(task_type="x", input_data={}, unknown_field="boom")
        assert not hasattr(t, "unknown_field")


# ── PlannerOutput ────────────────────────────────────────────────────


class TestPlannerOutput:
    def test_valid_acknowledge(self):
        p = PlannerOutput(decision="acknowledge", goal="ok")
        assert p.decision == "acknowledge"
        assert p.tasks == []

    def test_valid_create_task(self):
        p = PlannerOutput(
            decision="create_task",
            goal="Draft an email",
            reasoning_summary="User asked to draft",
            priority="high",
            risk_level="medium",
            execution_mode="approval_required",
            tasks=[{"task_type": "draft_email", "input_data": {"to": "a@b.com"}}],
        )
        assert p.decision == "create_task"
        assert len(p.tasks) == 1
        assert isinstance(p.tasks[0], PlannerTask)

    def test_watcher_create_decision(self):
        p = PlannerOutput(decision="watcher_create", goal="Watch for emails from CEO")
        assert p.decision == "watcher_create"

    def test_goal_update_decision(self):
        p = PlannerOutput(decision="goal_update", goal="Update Q2 revenue target")
        assert p.decision == "goal_update"

    def test_invalid_decision_rejected(self):
        with pytest.raises(ValidationError):
            PlannerOutput(decision="fly_to_moon")

    def test_missing_decision_rejected(self):
        with pytest.raises(ValidationError):
            PlannerOutput(goal="no decision")

    def test_defaults(self):
        p = PlannerOutput(decision="ignore")
        assert p.priority == "medium"
        assert p.risk_level == "low"
        assert p.execution_mode == "approval_required"
        assert p.goal == ""

    def test_extra_fields_ignored(self):
        p = PlannerOutput(
            decision="acknowledge",
            goal="ok",
            some_random_field="value",
        )
        assert not hasattr(p, "some_random_field")

    def test_model_dump_roundtrip(self):
        raw = {
            "decision": "create_task",
            "goal": "Send report",
            "reasoning_summary": "User wants a report",
            "priority": "high",
            "risk_level": "medium",
            "execution_mode": "auto_execute",
            "tasks": [{"task_type": "summarize", "input_data": {"text": "..."}}],
        }
        p = PlannerOutput.model_validate(raw)
        dumped = p.model_dump()
        assert dumped["decision"] == "create_task"
        assert len(dumped["tasks"]) == 1

    def test_invalid_priority_rejected(self):
        with pytest.raises(ValidationError):
            PlannerOutput(decision="acknowledge", priority="ultra")

    def test_invalid_risk_level_rejected(self):
        with pytest.raises(ValidationError):
            PlannerOutput(decision="acknowledge", risk_level="extreme")

    def test_all_decisions(self):
        decisions = [
            "acknowledge",
            "answer_directly",
            "create_task",
            "draft_reply",
            "search_memory",
            "add_to_brief",
            "ignore",
            "watcher_create",
            "goal_update",
        ]
        for d in decisions:
            p = PlannerOutput(decision=d)
            assert p.decision == d


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
        e = AgentEnvelope(agent_name="observer", message="hi")
        assert e.context == ""
        assert e.tools_available == []


# ── AgentResult ──────────────────────────────────────────────────────


class TestAgentResult:
    def test_valid(self):
        r = AgentResult(
            agent_name="researcher",
            response_text="Found 3 results",
            tools_called=["search_memory"],
            tokens_used=150,
        )
        assert r.agent_name == "researcher"
        assert r.tokens_used == 150

    def test_defaults(self):
        r = AgentResult(agent_name="persona")
        assert r.response_text == ""
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
