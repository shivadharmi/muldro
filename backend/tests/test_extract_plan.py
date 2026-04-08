"""Tests for extract_plan() — parsing Planner responses into PlanOutput."""

from __future__ import annotations

import json

from src.orchestrator.contracts import PlanOutput
from src.orchestrator.intent_classifier import extract_plan


class TestExtractPlan:
    """Parse raw Planner JSON text into validated PlanOutput."""

    def test_valid_json_parses(self):
        raw = json.dumps(
            {
                "goal": "Check email",
                "steps": [
                    {
                        "description": "Search inbox",
                        "capability": "email.search",
                    }
                ],
            }
        )
        result = extract_plan(raw)
        assert isinstance(result, PlanOutput)
        assert result.goal == "Check email"
        assert len(result.steps) == 1
        assert result.steps[0].capability == "email.search"

    def test_json_in_code_fences(self):
        raw = '```json\n{"goal": "Hello", "steps": []}\n```'
        result = extract_plan(raw)
        assert result.goal == "Hello"

    def test_json_with_extra_text_before(self):
        raw = 'Here is the plan:\n{"goal": "Do thing", "steps": []}'
        result = extract_plan(raw)
        assert result.goal == "Do thing"

    def test_full_plan_output_fields(self):
        raw = json.dumps(
            {
                "goal": "Send email",
                "reasoning": "Need to draft first",
                "achievable": "full",
                "priority": "high",
                "steps": [
                    {
                        "step_id": "step_1",
                        "description": "Search emails",
                        "capability": "email.search",
                        "risk": "none",
                    },
                    {
                        "step_id": "step_2",
                        "description": "Draft reply",
                        "capability": "email.draft",
                        "depends_on": ["step_1"],
                        "risk": "medium",
                    },
                ],
                "success_criteria": "Email drafted",
                "capability_gaps": [],
                "requires_user_input": False,
            }
        )
        result = extract_plan(raw)
        assert result.priority == "high"
        assert result.achievable == "full"
        assert len(result.steps) == 2
        assert result.steps[1].depends_on == ["step_1"]
        assert result.steps[1].risk == "medium"
        assert result.success_criteria == "Email drafted"

    def test_partial_achievability_with_gaps(self):
        raw = json.dumps(
            {
                "goal": "Update Notion",
                "achievable": "partial",
                "steps": [],
                "capability_gaps": [
                    {
                        "description": "Notion not connected",
                        "resolution": "Connect Notion in Settings",
                        "workaround": "Share via Slack instead",
                    }
                ],
            }
        )
        result = extract_plan(raw)
        assert result.achievable == "partial"
        assert len(result.capability_gaps) == 1
        assert result.capability_gaps[0].resolution == "Connect Notion in Settings"

    def test_malformed_json_returns_fallback(self):
        result = extract_plan("This is not JSON at all")
        assert isinstance(result, PlanOutput)
        assert result.steps[0].capability == "respond"

    def test_empty_string_returns_fallback(self):
        result = extract_plan("")
        assert isinstance(result, PlanOutput)
        assert len(result.steps) == 1

    def test_missing_fields_get_defaults(self):
        raw = json.dumps({"goal": "Hello"})
        result = extract_plan(raw)
        assert result.goal == "Hello"
        assert result.steps == []
        assert result.priority == "medium"
        assert result.achievable == "full"

    def test_extra_fields_ignored(self):
        raw = json.dumps(
            {
                "goal": "Test",
                "steps": [],
                "unknown_field": "ignored",
            }
        )
        result = extract_plan(raw)
        assert result.goal == "Test"

    def test_json_with_irrelevant_json_before_plan(self):
        raw = 'Context note: {"a": 1}\n\nActual plan: {"goal": "Do X", "steps": []}'
        result = extract_plan(raw)
        assert result.goal == "Do X"

    def test_user_step_with_actor(self):
        raw = json.dumps(
            {
                "goal": "Review draft",
                "steps": [
                    {
                        "description": "Review the email draft",
                        "capability": "respond",
                        "actor": "user",
                        "user_context": "Check the tone",
                    }
                ],
                "requires_user_input": True,
            }
        )
        result = extract_plan(raw)
        assert result.steps[0].actor == "user"
        assert result.steps[0].user_context == "Check the tone"
        assert result.requires_user_input is True
