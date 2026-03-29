"""Tests for tool schema registry — verifies orphan tools are removed."""

from src.orchestrator.tool_schemas import TOOL_INPUT_MODELS, build_tool_definitions


class TestToolInputModels:
    def test_orphan_tools_not_present(self):
        """create_task, get_task, get_goals have no MCP implementation — must not be in registry."""
        orphans = {"create_task", "get_task", "get_goals"}
        present = orphans & set(TOOL_INPUT_MODELS.keys())
        assert present == set(), f"Orphan tools still in TOOL_INPUT_MODELS: {present}"

    def test_tool_count_is_16(self):
        """After removing 3 orphans and adding get_goal_memories, exactly 16 tools should remain."""
        assert len(TOOL_INPUT_MODELS) == 16, (
            f"Expected 16 tools, got {len(TOOL_INPUT_MODELS)}: {sorted(TOOL_INPUT_MODELS.keys())}"
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
        """Verify the 16 expected internal tools are all present."""
        expected = {
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
            "extract_preferences",
            "build_context",
            "verify_run",
            "report_governor_verdict",
            "get_goal_memories",
        }
        actual = set(TOOL_INPUT_MODELS.keys())
        missing = expected - actual
        extra = actual - expected
        assert not missing, f"Missing expected tools: {missing}"
        assert not extra, f"Unexpected extra tools: {extra}"
