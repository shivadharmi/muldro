"""Tests for PlanOutput contract (capability-based planning)."""

from src.contracts import PlanOutput, PlanStep


def _valid_plan_data(**overrides) -> dict:
    data = {
        "goal": "Send investor update email",
        "reasoning": "User requested email draft",
        "priority": "high",
        "achievable": "full",
        "steps": [
            {
                "step_id": "s1",
                "description": "Draft email",
                "capability": "email.draft",
                "risk": "medium",
            }
        ],
        "success_criteria": "Email drafted and ready for review",
    }
    data.update(overrides)
    return data


class TestPlanOutputContract:
    def test_valid_minimal(self):
        output = PlanOutput(goal="Test")
        assert output.goal == "Test"
        assert output.steps == []
        assert output.priority == "medium"
        assert output.achievable == "full"

    def test_valid_full(self):
        output = PlanOutput(**_valid_plan_data())
        assert len(output.steps) == 1
        assert output.steps[0].capability == "email.draft"
        assert output.priority == "high"

    def test_extra_fields_ignored(self):
        output = PlanOutput(goal="Test", extra_stuff="nope")
        assert not hasattr(output, "extra_stuff")

    def test_model_json_schema_has_required_fields(self):
        schema = PlanOutput.model_json_schema()
        assert "goal" in str(schema)
        assert "properties" in schema

    def test_model_dump_roundtrip(self):
        data = _valid_plan_data()
        output = PlanOutput.model_validate(data)
        dumped = output.model_dump()
        reparsed = PlanOutput.model_validate(dumped)
        assert reparsed.goal == data["goal"]
        assert len(reparsed.steps) == 1

    def test_plan_step_defaults(self):
        step = PlanStep(description="Read", capability="email.read")
        assert step.actor == "muldro"
        assert step.risk == "none"
        assert step.depends_on == []

    def test_not_achievable(self):
        output = PlanOutput(
            goal="Impossible task",
            achievable="not_achievable",
            capability_gaps=[{"description": "Missing X", "resolution": "Connect X"}],
        )
        assert output.achievable == "not_achievable"
        assert len(output.capability_gaps) == 1
