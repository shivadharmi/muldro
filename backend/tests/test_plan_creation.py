"""Tests for PlanOutput creation, extract_plan fallback, and step_id validation."""

from __future__ import annotations

import logging

import pytest

from src.orchestrator.contracts import PlanOutput, PlanStep
from src.orchestrator.intent_classifier import extract_plan


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
