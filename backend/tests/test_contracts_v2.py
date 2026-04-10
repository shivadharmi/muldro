"""Tests for Gap Closure v2 contracts: PolicyDecision, DomainEvent."""

import pytest
from pydantic import ValidationError

from src.orchestrator.contracts import (
    DomainEvent,
    PolicyDecision,
)

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
