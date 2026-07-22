"""Step 6A.5: build_tool_shells produces StructuredTool schema shells (name + JSON args
schema) whose body RAISES — the jarvis_tool_dispatcher must intercept before it runs."""

import pytest
from langchain_core.tools import StructuredTool

from src.deep_runtime.tool_bridge import build_tool_shells


def test_shells_advertise_name_and_schema():
    defs = [
        {
            "name": "search",
            "description": "Search.",
            "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}},
        },
        {
            "name": "list_x",
            "description": "List.",
            "input_schema": {"type": "object"},
            "cache_control": {"type": "ephemeral"},
        },  # extra key ignored
    ]
    shells = build_tool_shells(defs)
    assert all(isinstance(s, StructuredTool) for s in shells)
    assert [s.name for s in shells] == ["search", "list_x"]
    assert shells[0].args_schema == defs[0]["input_schema"]


async def test_shell_body_raises_if_executed():
    shells = build_tool_shells(
        [{"name": "search", "description": "s", "input_schema": {"type": "object"}}]
    )
    with pytest.raises(AssertionError):
        await shells[0].ainvoke({})  # the dispatcher normally prevents this
