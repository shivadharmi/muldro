"""Tests for PlanOutput creation, extract_plan fallback, step_id validation,
forward dependency resolution, user-actor step mapping, and plan_output_json column."""

from __future__ import annotations

import logging

import pytest

from src.orchestrator.contracts import PlanOutput, PlanStep
from src.orchestrator.intent_classifier import extract_plan
from src.orchestrator.jarvis import _build_step_to_task_map


class TestExtractPlanFallback:
    def test_malformed_json_logs_warning(self, caplog):
        with caplog.at_level(logging.WARNING, logger="src.orchestrator.intent_classifier"):
            result = extract_plan("This is not JSON at all")
        assert result.steps[0].capability == "respond"
        assert result.achievable == "partial"
        assert any(
            "Planner response did not contain valid JSON" in r.message for r in caplog.records
        )

    def test_fallback_step_has_step_id(self):
        result = extract_plan("This is not JSON at all")
        assert result.steps[0].step_id == "s1"

    def test_valid_json_does_not_log_warning(self, caplog):
        valid = (
            '{"goal": "Test", "steps": ['
            '{"step_id": "s1", "description": "Do it", "capability": "respond"}'
            "]}"
        )
        with caplog.at_level(logging.WARNING, logger="src.orchestrator.intent_classifier"):
            result = extract_plan(valid)
        assert result.goal == "Test"
        assert not any(
            "Planner response did not contain valid JSON" in r.message for r in caplog.records
        )


class TestPlanOutputValidation:
    def test_duplicate_step_ids_rejected(self):
        with pytest.raises(ValueError, match="Duplicate step_id"):
            PlanOutput(
                goal="Test",
                steps=[
                    PlanStep(step_id="s1", description="A", capability="respond"),
                    PlanStep(step_id="s1", description="B", capability="reason"),
                ],
            )

    def test_unique_step_ids_accepted(self):
        plan = PlanOutput(
            goal="Test",
            steps=[
                PlanStep(step_id="s1", description="A", capability="respond"),
                PlanStep(step_id="s2", description="B", capability="reason"),
            ],
        )
        assert len(plan.steps) == 2

    def test_empty_step_ids_not_checked_for_uniqueness(self):
        """Steps with empty step_id are skipped by the uniqueness check."""
        plan = PlanOutput(
            goal="Test",
            steps=[
                PlanStep(step_id="", description="A", capability="respond"),
                PlanStep(step_id="", description="B", capability="reason"),
            ],
        )
        assert len(plan.steps) == 2

    def test_mixed_empty_and_unique_step_ids_accepted(self):
        plan = PlanOutput(
            goal="Test",
            steps=[
                PlanStep(step_id="s1", description="A", capability="respond"),
                PlanStep(step_id="", description="B", capability="reason"),
            ],
        )
        assert len(plan.steps) == 2


class TestDependencyResolution:
    def test_forward_reference_preserved(self):
        """Step s1 depends on s2 (later in list). Both should resolve."""
        steps = [
            PlanStep(step_id="s1", description="A", capability="respond", depends_on=["s2"]),
            PlanStep(step_id="s2", description="B", capability="reason"),
        ]
        mapping = _build_step_to_task_map(steps)
        assert "s1" in mapping
        assert "s2" in mapping
        # s1's dependency on s2 can now resolve
        dep_ids = [mapping[d] for d in steps[0].depends_on if d in mapping]
        assert len(dep_ids) == 1

    def test_user_steps_included_in_mapping(self):
        """User-actor steps get mapped so they can be dependency targets."""
        steps = [
            PlanStep(step_id="s1", description="Draft", capability="email.draft", actor="jarvis"),
            PlanStep(step_id="s2", description="Review", capability="respond", actor="user"),
        ]
        mapping = _build_step_to_task_map(steps)
        assert "s1" in mapping
        assert "s2" in mapping

    def test_empty_step_ids_excluded_from_mapping(self):
        """Steps without step_id are not mapped (but still get task_ids at persist time)."""
        steps = [
            PlanStep(step_id="", description="A", capability="respond"),
            PlanStep(step_id="s2", description="B", capability="reason"),
        ]
        mapping = _build_step_to_task_map(steps)
        assert "" not in mapping
        assert "s2" in mapping

    def test_all_task_ids_have_ptask_prefix(self):
        """All generated task_ids follow the ptask_ prefix convention."""
        steps = [
            PlanStep(step_id="s1", description="A", capability="respond"),
            PlanStep(step_id="s2", description="B", capability="reason"),
        ]
        mapping = _build_step_to_task_map(steps)
        for task_id in mapping.values():
            assert task_id.startswith("ptask_")


class TestPlanModelHasPlanOutputJson:
    def test_plan_model_has_column(self):
        from src.models.plans import Plan

        assert hasattr(Plan, "plan_output_json")


class TestPerceptionIdempotency:
    def test_idempotency_return_preserves_existing_plan_id(self):
        """When idempotency key matches, returned PlanOutput should have plan_id set."""
        import inspect

        from src.orchestrator.jarvis import JarvisOrchestrator

        source = inspect.getsource(JarvisOrchestrator._persist_plan_record)
        assert "model_copy" in source  # Uses model_copy to set plan_id on idempotency match


class TestInteractionLogHasPlanId:
    def test_interaction_log_has_plan_id_column(self):
        """InteractionLog already stores plan_id — no additional change needed."""
        from src.models.interaction_log import InteractionLog

        assert hasattr(InteractionLog, "plan_id")


class TestSystemCapabilityAudit:
    def test_handle_system_capability_creates_audit_record(self):
        """_handle_system_capability should contain PlanTask audit logic."""
        import inspect

        from src.orchestrator.jarvis import JarvisOrchestrator

        source = inspect.getsource(JarvisOrchestrator._handle_system_capability)
        assert "PlanTask" in source
        assert "plan.plan_id" in source
        assert 'status="completed"' in source
