"""Tests for tool schema registry — verifies orphan tools are removed."""

import pytest
from pydantic import ValidationError

from src.tools.schemas import (
    TOOL_INPUT_MODELS,
    ScheduleReminderInput,
    SetInstructionStepInput,
    build_tool_definitions,
)


class TestScheduleReminderCronValidation:
    """cron_expr is validated in the Pydantic model, so every model_validate
    (tool path + capability path) rejects an LLM-supplied garbage cron before it
    can be persisted and crash the scheduler."""

    def test_accepts_valid_cron(self):
        spec = ScheduleReminderInput.model_validate(
            {"title": "standup", "cron_expr": "0 9 * * 1-5"}
        )
        assert spec.cron_expr == "0 9 * * 1-5"

    def test_accepts_empty_cron(self):
        # Empty = no recurrence; a reminder need not carry a cron.
        spec = ScheduleReminderInput.model_validate({"title": "ping me"})
        assert spec.cron_expr == ""

    def test_rejects_malformed_cron(self):
        # The exact production failure mode: not 5/6/7 columns.
        with pytest.raises(ValidationError):
            ScheduleReminderInput.model_validate({"title": "bad", "cron_expr": "not a valid cron"})


class TestSetInstructionScheduleConfigValidation:
    """set_instruction's schedule_config is a typed ScheduleConfig, so its cron
    is validated structurally too — no raw agent dict reaches the scheduler."""

    def test_accepts_valid_schedule_config(self):
        spec = SetInstructionStepInput.model_validate(
            {
                "instruction_text": "daily digest",
                "instruction_type": "schedule",
                "schedule_config": {"type": "recurring", "cron_expr": "0 8 * * *"},
            }
        )
        assert spec.schedule_config is not None
        assert spec.schedule_config.cron_expr == "0 8 * * *"
        # Defaults fill in for omitted keys.
        assert spec.schedule_config.action_type == "custom_agent_task"

    def test_accepts_absent_schedule_config(self):
        spec = SetInstructionStepInput.model_validate(
            {"instruction_text": "x", "instruction_type": "preference"}
        )
        assert spec.schedule_config is None

    def test_rejects_malformed_cron_in_schedule_config(self):
        # Previously a raw dict passed through untouched; now it's rejected.
        with pytest.raises(ValidationError):
            SetInstructionStepInput.model_validate(
                {
                    "instruction_text": "x",
                    "instruction_type": "schedule",
                    "schedule_config": {"cron_expr": "every so often"},
                }
            )


class TestToolInputModels:
    def test_orphan_tools_not_present(self):
        """create_task, get_task, get_goals have no MCP implementation — must not be in registry."""
        orphans = {"create_task", "get_task", "get_goals"}
        present = orphans & set(TOOL_INPUT_MODELS.keys())
        assert present == set(), f"Orphan tools still in TOOL_INPUT_MODELS: {present}"

    def test_tool_count_is_29(self):
        """TOOL_INPUT_MODELS holds exactly 29 internal tools (25 + 4 P2.5a system.* tools)."""
        assert len(TOOL_INPUT_MODELS) == 29, (
            f"Expected 29 tools, got {len(TOOL_INPUT_MODELS)}: {sorted(TOOL_INPUT_MODELS.keys())}"
        )

    def test_all_models_have_docstrings(self):
        """Every tool model must have a docstring (used as Claude tool description)."""
        for name, model_cls in TOOL_INPUT_MODELS.items():
            assert model_cls.__doc__, f"Tool '{name}' model {model_cls.__name__} has no docstring"

    def test_build_tool_definitions_returns_correct_count(self):
        """build_tool_definitions() should return one definition per TOOL_INPUT_MODELS entry."""
        defs = build_tool_definitions()
        assert len(defs) == len(TOOL_INPUT_MODELS)

    def test_build_tool_definitions_structure(self):
        """Each tool definition must have name, description, and input_schema."""
        defs = build_tool_definitions()
        for tool_def in defs:
            assert "name" in tool_def, "Missing 'name' in tool definition"
            assert "description" in tool_def, f"Missing 'description' for {tool_def.get('name')}"
            assert "input_schema" in tool_def, f"Missing 'input_schema' for {tool_def.get('name')}"
            assert tool_def["input_schema"]["type"] == "object", (
                f"input_schema for {tool_def['name']} must be type 'object'"
            )

    def test_expected_tools_present(self):
        """Verify the 29 expected internal tools are all present."""
        expected = {
            "set_goal",
            "set_instruction",
            "schedule_reminder",
            "add_to_brief",
            "ingest_event",
            "search",
            "evaluate_policy",
            "get_briefing",
            "get_observation_cursor",
            "update_observation_cursor",
            "report_observation",
            "approve_action",
            "update_execution",
            "update_entity",
            "get_active_plans",
            "get_plan_details",
            "extract_preferences",
            "build_context",
            "verify_run",
            "store_memory",
            "store_preference",
            "discover_capabilities",
            "report_governor_verdict",
            "get_goal_memories",
            "push_ui_update",
            "get_entity",
            "query_facts",
            "traverse",
            "get_provenance",
        }
        actual = set(TOOL_INPUT_MODELS.keys())
        missing = expected - actual
        extra = actual - expected
        assert not missing, f"Missing expected tools: {missing}"
        assert not extra, f"Unexpected extra tools: {extra}"
