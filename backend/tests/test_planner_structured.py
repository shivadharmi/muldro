"""Tests for PlannerOutput contract (Pydantic model from contracts.py)."""

from src.orchestrator.contracts import PlannerOutput


def _valid_plan_data(**overrides) -> dict:
    """Factory for valid plan data matching PlannerOutput schema."""
    data = {
        "decision": "create_task",
        "goal": "Send investor update email",
        "reasoning_summary": "User requested email draft",
        "priority": "high",
        "risk_level": "medium",
        "execution_mode": "approval_required",
        "tasks": [{"task_type": "draft_email", "input_data": {"to": "investor@co.com"}}],
    }
    data.update(overrides)
    return data


class TestPlannerOutputContract:
    """Tests for PlannerOutput Pydantic model."""

    def test_valid_minimal(self):
        output = PlannerOutput(decision="acknowledge")
        assert output.goal == ""
        assert output.tasks == []
        assert output.priority == "medium"

    def test_valid_full(self):
        output = PlannerOutput(
            decision="create_task",
            goal="Send email",
            reasoning_summary="User asked",
            priority="critical",
            risk_level="high",
            execution_mode="approval_required",
            tasks=[{"task_type": "send", "input_data": {"to": "a@b.com"}}],
        )
        assert len(output.tasks) == 1
        assert output.tasks[0].task_type == "send"

    def test_extra_fields_ignored(self):
        output = PlannerOutput(decision="ignore", extra_stuff="nope")
        assert not hasattr(output, "extra_stuff")

    def test_model_json_schema_has_required_fields(self):
        schema = PlannerOutput.model_json_schema()
        assert "decision" in str(schema)
        assert "properties" in schema

    def test_model_dump_roundtrip(self):
        data = _valid_plan_data()
        output = PlannerOutput.model_validate(data)
        dumped = output.model_dump()
        reparsed = PlannerOutput.model_validate(dumped)
        assert reparsed.decision == data["decision"]
        assert reparsed.goal == data["goal"]
