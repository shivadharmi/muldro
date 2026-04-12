"""Tests for SurfaceUpdate contract models."""

import json

from src.orchestrator.contracts import (
    ApprovalContext,
    ResultSummary,
    StepState,
    SurfaceUpdate,
)


class TestStepState:
    def test_minimal(self):
        s = StepState(step_id="step_01", description="Search emails", status="pending")
        assert s.step_id == "step_01"
        assert s.output_summary is None
        assert s.duration_ms is None

    def test_completed_with_output(self):
        s = StepState(
            step_id="step_02",
            description="Draft reply",
            status="completed",
            output_summary="Drafted 3 paragraphs",
            duration_ms=1200,
        )
        data = s.model_dump(mode="json")
        assert data["duration_ms"] == 1200
        assert data["output_summary"] == "Drafted 3 paragraphs"


class TestApprovalContext:
    def test_fields(self):
        a = ApprovalContext(
            approval_id="apr_01",
            step_description="Send email to client",
            risk_reasoning="External write to unknown recipient",
            trust_context="First time sending to this domain",
            graduation_hint="3 more approvals to auto-approve",
        )
        assert a.graduation_hint == "3 more approvals to auto-approve"

    def test_default_graduation_hint(self):
        a = ApprovalContext(
            approval_id="apr_02",
            step_description="x",
            risk_reasoning="y",
            trust_context="z",
        )
        assert a.graduation_hint == ""


class TestResultSummary:
    def test_defaults(self):
        r = ResultSummary()
        assert r.key_findings == []
        assert r.artifacts_created == []
        assert r.suggested_next == []

    def test_populated(self):
        r = ResultSummary(
            key_findings=["Found 3 relevant emails"],
            artifacts_created=["draft_reply_01"],
            suggested_next=["Review draft before sending"],
        )
        assert len(r.key_findings) == 1


class TestSurfaceUpdate:
    def test_plan_ready_phase(self):
        steps = [
            StepState(step_id="s1", description="Search", status="pending"),
            StepState(step_id="s2", description="Draft", status="pending"),
        ]
        su = SurfaceUpdate(
            surface_id="surf_abc",
            phase="plan_ready",
            steps=steps,
            progress="0/2 steps",
        )
        assert su.phase == "plan_ready"
        assert len(su.steps) == 2
        assert su.approval is None
        assert su.results is None

    def test_executing_phase(self):
        su = SurfaceUpdate(
            surface_id="surf_abc",
            phase="executing",
            steps=[StepState(step_id="s1", description="Search", status="executing")],
            current_step="s1",
        )
        assert su.current_step == "s1"

    def test_completed_with_results(self):
        su = SurfaceUpdate(
            surface_id="surf_abc",
            phase="completed",
            results=ResultSummary(key_findings=["Done"]),
        )
        assert su.results.key_findings == ["Done"]

    def test_approval_needed(self):
        su = SurfaceUpdate(
            surface_id="surf_abc",
            phase="approval_needed",
            approval=ApprovalContext(
                approval_id="apr_01",
                step_description="Send email",
                risk_reasoning="External write",
                trust_context="First use",
            ),
        )
        assert su.approval.approval_id == "apr_01"

    def test_json_roundtrip(self):
        su = SurfaceUpdate(
            surface_id="surf_abc",
            phase="executing",
            steps=[StepState(step_id="s1", description="x", status="executing")],
            current_step="s1",
            progress="1/3",
        )
        data = json.loads(su.model_dump_json())
        restored = SurfaceUpdate(**data)
        assert restored.surface_id == "surf_abc"
        assert restored.steps[0].step_id == "s1"

    def test_extra_fields_ignored(self):
        su = SurfaceUpdate(
            surface_id="surf_abc",
            phase="completed",
            unknown_field="ignored",
        )
        assert su.phase == "completed"
