"""Integration tests for Spec 3A: Execution Events Backend."""

import json

from src.orchestrator.contracts import (
    ResultSummary,
    StepState,
    SurfaceUpdate,
)


class TestSurfaceUpdateFlow:
    """Verify surface_update message shape and phase transitions."""

    def test_plan_ready_to_completed_sequence(self):
        """A plan should transition: plan_ready -> executing -> completed."""
        phases = []

        steps = [
            StepState(step_id="s1", description="Search", status="pending"),
            StepState(step_id="s2", description="Analyze", status="pending"),
            StepState(step_id="s3", description="Draft", status="pending"),
        ]

        # Phase 1: plan_ready
        su1 = SurfaceUpdate(
            surface_id="surf_01",
            phase="plan_ready",
            steps=steps,
            progress="0/3 steps",
        )
        phases.append(su1.phase)

        # Phase 2: executing step 1
        steps_v2 = [
            StepState(step_id="s1", description="Search", status="executing"),
            StepState(step_id="s2", description="Analyze", status="pending"),
            StepState(step_id="s3", description="Draft", status="pending"),
        ]
        su2 = SurfaceUpdate(
            surface_id="surf_01",
            phase="executing",
            steps=steps_v2,
            current_step="s1",
            progress="0/3 steps",
        )
        phases.append(su2.phase)

        # Phase 3: completed
        su3 = SurfaceUpdate(
            surface_id="surf_01",
            phase="completed",
            results=ResultSummary(key_findings=["Found 3 emails"]),
            progress="3/3 steps",
        )
        phases.append(su3.phase)

        assert phases == ["plan_ready", "executing", "completed"]

    def test_all_updates_share_surface_id(self):
        """All updates for one execution must reference the same surface_id."""
        updates = [
            SurfaceUpdate(surface_id="surf_99", phase="plan_ready"),
            SurfaceUpdate(surface_id="surf_99", phase="executing", current_step="s1"),
            SurfaceUpdate(surface_id="surf_99", phase="completed"),
        ]
        ids = {u.surface_id for u in updates}
        assert len(ids) == 1

    def test_surface_update_json_shape_for_websocket(self):
        """Verify the JSON shape that gets published to Redis/WebSocket."""
        su = SurfaceUpdate(
            surface_id="surf_01",
            phase="executing",
            steps=[StepState(step_id="s1", description="x", status="running")],
        )
        msg = json.dumps({"type": "surface_update", **su.model_dump(mode="json")})
        parsed = json.loads(msg)

        assert parsed["type"] == "surface_update"
        assert "surface_id" in parsed
        assert "phase" in parsed
        assert "steps" in parsed
        assert isinstance(parsed["steps"], list)

    def test_approval_needed_includes_context(self):
        """approval_needed phase must include ApprovalContext."""
        from src.orchestrator.contracts import ApprovalContext

        su = SurfaceUpdate(
            surface_id="surf_01",
            phase="approval_needed",
            approval=ApprovalContext(
                approval_id="apr_01",
                step_description="Send email",
                risk_reasoning="External write",
                trust_context="First use",
                graduation_hint="9 more to auto",
            ),
        )
        data = su.model_dump(mode="json")
        assert data["approval"]["approval_id"] == "apr_01"
        assert data["approval"]["graduation_hint"] == "9 more to auto"


class TestInteractionLogReplacement:
    """Verify InteractionLog has correct shape (no state machine)."""

    def test_no_status_field(self):
        from src.models.interaction_log import InteractionLog

        log = InteractionLog(
            interaction_id="ilog_01",
            workspace_id="ws_01",
            user_id="usr_01",
            trace_id="trc_01",
        )
        assert not hasattr(log, "status") or "status" not in log.__table__.columns

    def test_has_audit_fields(self):
        from src.models.interaction_log import InteractionLog

        log = InteractionLog(
            interaction_id="ilog_02",
            workspace_id="ws_01",
            user_id="usr_01",
            trace_id="trc_02",
        )
        assert hasattr(log, "input_tokens")
        assert hasattr(log, "output_tokens")
        assert hasattr(log, "cost_usd")
        assert hasattr(log, "latency_ms")
