"""Tests for Phase 2B: Planner structured output via Claude tool_use."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.orchestrator.contracts import PlannerOutput
from src.services.planner import DECISIONS, PLAN_SYSTEM_PROMPT, Planner
from tests.conftest import make_mock_settings


def _make_tool_use_response(plan_data: dict):
    """Create a mock Claude response with tool_use content block."""
    tool_block = SimpleNamespace(
        type="tool_use",
        name="submit_plan",
        input=plan_data,
    )
    response = MagicMock()
    response.content = [tool_block]
    return response


def _make_text_response(text: str):
    """Create a mock Claude response with text content block."""
    text_block = SimpleNamespace(type="text", text=text)
    response = MagicMock()
    response.content = [text_block]
    return response


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


def _make_planner(mock_client=None) -> Planner:
    """Create a Planner with mocked dependencies."""
    settings = make_mock_settings()
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()

    client = mock_client or MagicMock()
    with patch("src.services.planner.get_anthropic_client", return_value=client):
        planner = Planner(settings=settings, db=db)
    return planner


# ── Planner tool_use structured output ────────────────────────────────────────


class TestPlannerToolUse:
    """Tests for planner._call_claude tool_use path."""

    async def test_tool_use_extracts_valid_plan(self):
        """Tool_use response with valid schema is extracted and validated."""
        plan_data = _valid_plan_data()
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=_make_tool_use_response(plan_data))

        planner = _make_planner(mock_client)
        result = await planner._call_claude("Send investor update")

        assert result["decision"] == "create_task"
        assert result["goal"] == "Send investor update email"
        assert len(result["tasks"]) == 1

    async def test_tool_use_sends_correct_tool_schema(self):
        """Verify tool definition uses PlannerOutput.model_json_schema()."""
        plan_data = _valid_plan_data()
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=_make_tool_use_response(plan_data))

        planner = _make_planner(mock_client)
        await planner._call_claude("Test command")

        call_kwargs = mock_client.messages.create.call_args[1]
        assert "tools" in call_kwargs
        tool = call_kwargs["tools"][0]
        assert tool["name"] == "submit_plan"
        assert tool["input_schema"] == PlannerOutput.model_json_schema()

    async def test_tool_use_forces_tool_choice(self):
        """Verify tool_choice is set to force submit_plan."""
        plan_data = _valid_plan_data()
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=_make_tool_use_response(plan_data))

        planner = _make_planner(mock_client)
        await planner._call_claude("Test")

        call_kwargs = mock_client.messages.create.call_args[1]
        assert call_kwargs["tool_choice"] == {"type": "tool", "name": "submit_plan"}

    @pytest.mark.parametrize("decision", DECISIONS)
    async def test_each_decision_type_accepted(self, decision):
        """Each valid decision type is accepted by PlannerOutput."""
        plan_data = _valid_plan_data(decision=decision, tasks=[])
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=_make_tool_use_response(plan_data))

        planner = _make_planner(mock_client)
        result = await planner._call_claude("Test")

        assert result["decision"] == decision


class TestPlannerTextFallback:
    """Tests for planner._call_claude text fallback path."""

    async def test_text_fallback_on_tool_use_failure(self):
        """When tool_use fails, falls back to text-based JSON parsing."""
        plan_data = _valid_plan_data(decision="acknowledge", tasks=[])
        plan_json = json.dumps(plan_data)

        mock_client = MagicMock()
        # First call (tool_use) raises, second call (text) succeeds
        mock_client.messages.create = AsyncMock(
            side_effect=[
                RuntimeError("tool_use failed"),
                _make_text_response(plan_json),
            ]
        )

        planner = _make_planner(mock_client)
        result = await planner._call_claude("Simple greeting")

        assert result["decision"] == "acknowledge"
        assert mock_client.messages.create.call_count == 2

    async def test_text_fallback_strips_markdown_fences(self):
        """Text response wrapped in ```json ... ``` is properly extracted."""
        plan_data = _valid_plan_data(decision="ignore", tasks=[])
        wrapped = f"```json\n{json.dumps(plan_data)}\n```"

        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(
            side_effect=[
                RuntimeError("tool_use failed"),
                _make_text_response(wrapped),
            ]
        )

        planner = _make_planner(mock_client)
        result = await planner._call_claude("Low priority event")

        assert result["decision"] == "ignore"

    async def test_text_fallback_validates_with_pydantic(self):
        """Text fallback validates through PlannerOutput model."""
        plan_data = _valid_plan_data()
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(
            side_effect=[
                RuntimeError("tool_use failed"),
                _make_text_response(json.dumps(plan_data)),
            ]
        )

        planner = _make_planner(mock_client)
        result = await planner._call_claude("Draft email")

        # PlannerOutput defaults should be applied
        assert "decision" in result
        assert "goal" in result

    async def test_text_fallback_raw_dict_on_validation_error(self):
        """If PlannerOutput validation fails, raw dict is returned."""
        raw = {"decision": "unknown_decision", "goal": "test"}

        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(
            side_effect=[
                RuntimeError("tool_use failed"),
                _make_text_response(json.dumps(raw)),
            ]
        )

        planner = _make_planner(mock_client)
        result = await planner._call_claude("Test")

        # Should get back raw dict since "unknown_decision" is invalid
        assert result["decision"] == "unknown_decision"


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


class TestPlanSystemPrompt:
    """Tests for PLAN_SYSTEM_PROMPT content."""

    def test_contains_all_decisions(self):
        for decision in DECISIONS:
            assert decision in PLAN_SYSTEM_PROMPT

    def test_contains_json_schema_hint(self):
        assert "JSON" in PLAN_SYSTEM_PROMPT

    def test_contains_execution_modes(self):
        assert "auto_execute" in PLAN_SYSTEM_PROMPT
        assert "approval_required" in PLAN_SYSTEM_PROMPT
        assert "draft_only" in PLAN_SYSTEM_PROMPT
