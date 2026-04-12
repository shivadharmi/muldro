"""Tests for chat SSE plan event (replaces decision event)."""

from src.orchestrator.contracts import MessageMetadata, PlanOutput, PlanStep


class TestMessageMetadataUsePlanOutput:
    """MessageMetadata.decision is now PlanOutput type."""

    def test_metadata_accepts_plan_output(self):
        plan = PlanOutput(
            goal="Check email",
            steps=[
                PlanStep(
                    step_id="s1",
                    description="Read",
                    capability="email.search",
                )
            ],
        )
        meta = MessageMetadata(
            trace_id="trace_1",
            decision=plan,
            agent_steps=[],
        )
        assert isinstance(meta.decision, PlanOutput)
        dumped = meta.model_dump(mode="json")
        assert dumped["decision"]["goal"] == "Check email"
        assert dumped["decision"]["steps"][0]["capability"] == "email.search"

    def test_metadata_decision_none(self):
        meta = MessageMetadata(trace_id="trace_1")
        assert meta.decision is None

    def test_metadata_round_trip_serialization(self):
        plan = PlanOutput(
            goal="Draft email",
            reasoning="User wants to draft",
            priority="high",
            steps=[
                PlanStep(
                    step_id="s1",
                    description="Draft",
                    capability="email.draft",
                    risk="medium",
                ),
            ],
        )
        meta = MessageMetadata(trace_id="t1", decision=plan)
        dumped = meta.model_dump(mode="json")
        restored = MessageMetadata.model_validate(dumped)
        assert restored.decision is not None
        assert restored.decision.goal == "Draft email"
        assert restored.decision.steps[0].capability == "email.draft"
