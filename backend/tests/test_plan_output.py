"""Tests for capability-based planning models: CapabilityGap, PlanStep, PlanOutput."""

import pytest
from pydantic import ValidationError

from src.contracts import (
    CapabilityGap,
    PlanOutput,
    PlanStep,
)

# -- CapabilityGap ---------------------------------------------------------------


class TestCapabilityGap:
    def test_valid(self):
        g = CapabilityGap(
            description="Cannot access Notion pages",
            resolution="connect Notion",
            workaround="Copy content manually into chat",
        )
        assert g.description == "Cannot access Notion pages"
        assert g.resolution == "connect Notion"
        assert g.workaround == "Copy content manually into chat"

    def test_no_workaround(self):
        g = CapabilityGap(
            description="No calendar integration",
            resolution="not currently possible",
        )
        assert g.workaround is None

    def test_extra_ignored(self):
        g = CapabilityGap(
            description="Missing Slack",
            resolution="connect Slack",
            unknown_field="boom",
        )
        assert not hasattr(g, "unknown_field")

    def test_missing_required_fields(self):
        with pytest.raises(ValidationError):
            CapabilityGap(description="Missing something")
        with pytest.raises(ValidationError):
            CapabilityGap(resolution="connect it")

    def test_model_dump_roundtrip(self):
        raw = {
            "description": "No GitHub access",
            "resolution": "connect GitHub",
            "workaround": "Paste PR links",
        }
        g = CapabilityGap.model_validate(raw)
        dumped = g.model_dump()
        assert dumped == raw


# -- PlanStep ---------------------------------------------------------------------


class TestPlanStep:
    def test_valid_muldro_step(self):
        s = PlanStep(
            step_id="s1",
            description="Search recent emails for invoice",
            actor="muldro",
            capability="email.search",
            input={"query": "invoice", "max_results": 10},
            depends_on=[],
            risk="low",
        )
        assert s.step_id == "s1"
        assert s.actor == "muldro"
        assert s.capability == "email.search"
        assert s.input["query"] == "invoice"
        assert s.risk == "low"

    def test_valid_user_step(self):
        s = PlanStep(
            step_id="s2",
            description="Confirm the recipient address",
            actor="user",
            capability="respond",
            user_context="Please confirm the email address before I send.",
        )
        assert s.actor == "user"
        assert s.user_context is not None

    def test_defaults(self):
        s = PlanStep(description="Think about it", capability="reason")
        assert s.step_id == ""
        assert s.actor == "muldro"
        assert s.input == {}
        assert s.depends_on == []
        assert s.risk == "none"
        assert s.user_context is None

    def test_with_dependencies(self):
        s = PlanStep(
            description="Summarize search results",
            capability="reason",
            depends_on=["s1", "s2"],
        )
        assert s.depends_on == ["s1", "s2"]

    def test_risk_levels(self):
        for level in ("none", "low", "medium", "high"):
            s = PlanStep(description="x", capability="y", risk=level)
            assert s.risk == level

    def test_invalid_risk_rejected(self):
        with pytest.raises(ValidationError):
            PlanStep(description="x", capability="y", risk="extreme")

    def test_invalid_actor_rejected(self):
        with pytest.raises(ValidationError):
            PlanStep(description="x", capability="y", actor="robot")

    def test_extra_ignored(self):
        s = PlanStep(
            description="do something",
            capability="reason",
            extra_field="should vanish",
        )
        assert not hasattr(s, "extra_field")

    def test_missing_description_rejected(self):
        with pytest.raises(ValidationError):
            PlanStep(capability="reason")

    def test_missing_capability_rejected(self):
        with pytest.raises(ValidationError):
            PlanStep(description="do something")


# -- PlanOutput -------------------------------------------------------------------


class TestPlanOutput:
    def test_valid_full_plan(self):
        p = PlanOutput(
            goal="Send weekly investor update",
            reasoning="User requested weekly update email to investors",
            achievable="full",
            priority="high",
            steps=[
                PlanStep(
                    step_id="s1",
                    description="Search for latest metrics",
                    capability="data.query",
                    input={"query": "weekly KPIs"},
                ),
                PlanStep(
                    step_id="s2",
                    description="Draft email body",
                    capability="email.draft",
                    depends_on=["s1"],
                    risk="low",
                ),
                PlanStep(
                    step_id="s3",
                    description="Send email",
                    capability="email.send",
                    depends_on=["s2"],
                    risk="high",
                ),
            ],
            success_criteria="Investor update email sent with latest KPIs",
        )
        assert p.goal == "Send weekly investor update"
        assert len(p.steps) == 3
        assert p.steps[1].depends_on == ["s1"]
        assert p.steps[2].risk == "high"
        assert p.achievable == "full"

    def test_partial_achievability_with_gaps(self):
        p = PlanOutput(
            goal="Sync Notion tasks to calendar",
            achievable="partial",
            capability_gaps=[
                CapabilityGap(
                    description="No Notion integration",
                    resolution="connect Notion",
                    workaround="Paste task list manually",
                ),
            ],
            steps=[
                PlanStep(
                    description="Add events to calendar from pasted list",
                    capability="calendar.create",
                ),
            ],
        )
        assert p.achievable == "partial"
        assert len(p.capability_gaps) == 1
        assert p.capability_gaps[0].resolution == "connect Notion"

    def test_not_achievable(self):
        p = PlanOutput(
            goal="Teleport to Mars",
            achievable="not_achievable",
            capability_gaps=[
                CapabilityGap(
                    description="Teleportation not possible",
                    resolution="not currently possible",
                ),
            ],
        )
        assert p.achievable == "not_achievable"
        assert len(p.capability_gaps) == 1
        assert p.steps == []

    def test_defaults(self):
        p = PlanOutput(goal="Test defaults")
        assert p.reasoning == ""
        assert p.achievable == "full"
        assert p.priority == "medium"
        assert p.steps == []
        assert p.success_criteria == ""
        assert p.capability_gaps == []
        assert p.plan_id is None
        assert p.requires_user_input is False

    def test_requires_user_input_flag(self):
        p = PlanOutput(
            goal="Confirm before sending",
            requires_user_input=True,
            steps=[
                PlanStep(
                    description="Ask user to confirm recipient",
                    actor="user",
                    capability="respond",
                    user_context="Who should I send this to?",
                ),
            ],
        )
        assert p.requires_user_input is True
        assert p.steps[0].actor == "user"

    def test_priority_levels(self):
        for level in ("low", "medium", "high", "critical"):
            p = PlanOutput(goal="g", priority=level)
            assert p.priority == level

    def test_invalid_priority_rejected(self):
        with pytest.raises(ValidationError):
            PlanOutput(goal="g", priority="ultra")

    def test_invalid_achievable_rejected(self):
        with pytest.raises(ValidationError):
            PlanOutput(goal="g", achievable="maybe")

    def test_extra_ignored(self):
        p = PlanOutput(goal="g", unknown_field="should vanish")
        assert not hasattr(p, "unknown_field")

    def test_missing_goal_rejected(self):
        with pytest.raises(ValidationError):
            PlanOutput(reasoning="no goal provided")

    def test_with_plan_id(self):
        p = PlanOutput(goal="Tracked plan", plan_id="plan_abc123")
        assert p.plan_id == "plan_abc123"

    def test_model_dump_roundtrip(self):
        raw = {
            "goal": "Roundtrip test",
            "reasoning": "Testing serialization",
            "achievable": "full",
            "priority": "low",
            "steps": [
                {
                    "step_id": "s1",
                    "description": "Do thing",
                    "actor": "muldro",
                    "capability": "reason",
                    "input": {},
                    "depends_on": [],
                    "risk": "none",
                    "user_context": None,
                },
            ],
            "success_criteria": "Thing done",
            "capability_gaps": [],
            "plan_id": None,
            "requires_user_input": False,
        }
        p = PlanOutput.model_validate(raw)
        dumped = p.model_dump()
        assert dumped["goal"] == "Roundtrip test"
        assert len(dumped["steps"]) == 1
        assert dumped["steps"][0]["capability"] == "reason"

    def test_model_validate_from_claude_json(self):
        """Simulated Claude response with extra fields that should be ignored."""
        claude_response = {
            "goal": "Book a flight to NYC",
            "reasoning": "User wants to travel",
            "achievable": "partial",
            "priority": "high",
            "steps": [
                {
                    "step_id": "s1",
                    "description": "Search flights",
                    "capability": "travel.search",
                    "input": {"origin": "SFO", "destination": "JFK"},
                    "extra_field": "claude added this",
                },
            ],
            "capability_gaps": [
                {
                    "description": "No booking integration",
                    "resolution": "connect travel provider",
                    "extra_field": "also ignored",
                },
            ],
            "extra_field": "top-level extra",
            "requires_user_input": True,
        }
        p = PlanOutput.model_validate(claude_response)
        assert p.goal == "Book a flight to NYC"
        assert p.achievable == "partial"
        assert len(p.steps) == 1
        assert p.steps[0].capability == "travel.search"
        assert not hasattr(p.steps[0], "extra_field")
        assert len(p.capability_gaps) == 1
        assert not hasattr(p.capability_gaps[0], "extra_field")
        assert not hasattr(p, "extra_field")
