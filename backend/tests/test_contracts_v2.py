"""Tests for Gap Closure v2 contracts: ExecutionPlan, PolicyDecision."""

import pytest
from pydantic import ValidationError

from src.orchestrator.contracts import (
    DomainEvent,
    ExecutionPlan,
    PlannerTask,
    PolicyDecision,
)

# ── ExecutionPlan ─────────────────────────────────────────────────────────


class TestExecutionPlan:
    def test_minimal_valid(self):
        ep = ExecutionPlan(plan_id="plan_001", goal="Send email")
        assert ep.plan_id == "plan_001"
        assert ep.goal == "Send email"
        assert ep.tasks == []
        assert ep.risk_level == "low"
        assert ep.execution_mode == "approval_required"
        assert ep.priority == "medium"

    def test_full_valid(self):
        ep = ExecutionPlan(
            plan_id="plan_002",
            goal="Draft investor update",
            tasks=[
                PlannerTask(task_type="draft_email", input_data={"to": "investor@co.com"}),
                PlannerTask(task_type="summarize", input_data={"text": "Q4 results"}),
            ],
            risk_level="high",
            execution_mode="approval_required",
            priority="critical",
            reasoning_summary="Investor needs update before board meeting",
        )
        assert len(ep.tasks) == 2
        assert ep.risk_level == "high"
        assert ep.priority == "critical"

    def test_invalid_risk_level(self):
        with pytest.raises(ValidationError):
            ExecutionPlan(plan_id="p", goal="g", risk_level="extreme")

    def test_invalid_execution_mode(self):
        with pytest.raises(ValidationError):
            ExecutionPlan(plan_id="p", goal="g", execution_mode="yolo")

    def test_invalid_priority(self):
        with pytest.raises(ValidationError):
            ExecutionPlan(plan_id="p", goal="g", priority="urgent")

    def test_extra_fields_ignored(self):
        ep = ExecutionPlan(plan_id="p", goal="g", unknown_field="should_be_ignored")
        assert not hasattr(ep, "unknown_field")

    def test_model_dump(self):
        ep = ExecutionPlan(plan_id="plan_003", goal="Test")
        d = ep.model_dump()
        assert d["plan_id"] == "plan_003"
        assert "tasks" in d
        assert isinstance(d["tasks"], list)

    def test_model_validate_from_dict(self):
        data = {
            "plan_id": "plan_004",
            "goal": "Research competitors",
            "tasks": [{"task_type": "fetch_info", "input_data": {"query": "competitors"}}],
            "risk_level": "none",
            "execution_mode": "auto_execute",
            "priority": "low",
            "reasoning_summary": "Low risk read-only operation",
        }
        ep = ExecutionPlan.model_validate(data)
        assert ep.plan_id == "plan_004"
        assert len(ep.tasks) == 1
        assert ep.tasks[0].task_type == "fetch_info"


# ── PolicyDecision ────────────────────────────────────────────────────────


class TestPolicyDecision:
    def test_auto_execute(self):
        pd = PolicyDecision(decision="auto_execute", execution_id="exec_001")
        assert pd.decision == "auto_execute"
        assert pd.execution_id == "exec_001"
        assert pd.approval_id is None

    def test_approval_required(self):
        pd = PolicyDecision(
            decision="approval_required",
            justification="External write requires approval",
            risk_level="high",
            approval_id="apr_001",
            execution_id="exec_002",
        )
        assert pd.decision == "approval_required"
        assert pd.approval_id == "apr_001"
        assert pd.risk_level == "high"

    def test_blocked(self):
        pd = PolicyDecision(
            decision="blocked",
            justification="Dangerous operation",
            risk_level="critical",
        )
        assert pd.decision == "blocked"
        assert pd.execution_id is None

    def test_invalid_decision(self):
        with pytest.raises(ValidationError):
            PolicyDecision(decision="maybe")

    def test_defaults(self):
        pd = PolicyDecision(decision="auto_execute")
        assert pd.justification == ""
        assert pd.risk_level == "low"
        assert pd.approval_id is None
        assert pd.execution_id is None

    def test_model_dump(self):
        pd = PolicyDecision(
            decision="approval_required",
            approval_id="apr_002",
            execution_id="exec_003",
        )
        d = pd.model_dump()
        assert d["decision"] == "approval_required"
        assert d["approval_id"] == "apr_002"

    def test_extra_fields_ignored(self):
        pd = PolicyDecision(decision="blocked", extra_field="ignored")
        assert not hasattr(pd, "extra_field")


# ── DomainEvent (existing, verify still works) ───────────────────────────


class TestDomainEvent:
    def test_step_skipped_event(self):
        evt = DomainEvent(
            event_type="step.skipped",
            user_id="usr_1",
            payload={"run_id": "run_001", "step_id": "step_001"},
        )
        assert evt.event_type == "step.skipped"
        assert evt.payload["step_id"] == "step_001"

    def test_memory_updated_event(self):
        evt = DomainEvent(
            event_type="memory.updated",
            user_id="usr_1",
            payload={"memory_id": "mem_old", "superseded_by": "mem_new"},
        )
        assert evt.event_type == "memory.updated"

    def test_notification_delivered_event(self):
        evt = DomainEvent(
            event_type="notification.delivered",
            user_id="usr_1",
            payload={"notification_id": "notif_001", "surface": "telegram"},
        )
        assert evt.event_type == "notification.delivered"

    def test_trigger_evaluated_no_match(self):
        evt = DomainEvent(
            event_type="trigger.evaluated",
            user_id="usr_1",
            payload={"trigger_id": "trig_001", "matched": False},
        )
        assert evt.payload["matched"] is False
