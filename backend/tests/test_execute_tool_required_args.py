"""Regression: ToolExecutor rejects external-MCP calls missing schema-required args.

Root cause (reproduced): the perceiver emitted ``query_freebusy({})`` when it was
offered the tool WITHOUT its input schema (e.g. degraded discovery while the
google-workspace server was flapping). ``query_freebusy`` requires time_min/time_max,
so ``{}`` hard-fails at the server. execute_tool is the central dispatch chokepoint;
it validates external calls against the AUTHORITATIVE persisted DB schema and rejects
missing-required-arg calls BEFORE the MCP round-trip, with a message the agent can act
on (agents self-correct on tool errors). Internal tools are exempt (their contextual
args are injected by the dispatcher, not supplied by the agent).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID

_FREEBUSY_SCHEMA = {
    "type": "object",
    "required": ["time_min", "time_max"],
    "properties": {
        "time_min": {"type": "string"},
        "time_max": {"type": "string"},
        "calendar_ids": {"type": "array"},
    },
}


def _make_tool_record(backend: str, *, server: str = "default", input_schema=None):
    tool = MagicMock()
    tool.backend = backend
    tool.server = server
    tool.enabled = True
    tool.input_schema = input_schema
    return tool


def _make_tool_executor():
    from src.orchestrator.tool_executor import ToolExecutor

    events = MagicMock()
    events.publish_event = AsyncMock()

    mock_db = AsyncMock()
    db_factory = MagicMock()
    db_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    db_factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return ToolExecutor(events, lambda: db_factory)


async def _run(tool, tool_input, mock_call_mcp):
    mock_registry = AsyncMock()
    mock_registry.get_tool = AsyncMock(return_value=tool)
    te = _make_tool_executor()
    with (
        patch("src.services.tool_registry.ToolRegistry", return_value=mock_registry),
        patch("src.connectors.mcp_bridge.call_mcp_tool", mock_call_mcp),
    ):
        return await te.execute_tool(
            tool_name="query_freebusy",
            tool_input=tool_input,
            user_id=TEST_USER_ID,
            workspace_id=TEST_WORKSPACE_ID,
        )


@pytest.mark.asyncio
async def test_missing_required_args_rejected_before_dispatch():
    """query_freebusy({}) → error naming the missing fields, no MCP round-trip."""
    mock_call_mcp = AsyncMock(return_value={"ok": True})
    tool = _make_tool_record("external_mcp", input_schema=_FREEBUSY_SCHEMA)

    result = await _run(tool, {}, mock_call_mcp)

    mock_call_mcp.assert_not_called()
    assert "error" in result
    assert "time_min" in result["error"] and "time_max" in result["error"]


@pytest.mark.asyncio
async def test_partial_required_args_rejected():
    """Only the ACTUALLY-missing required field is named."""
    mock_call_mcp = AsyncMock(return_value={"ok": True})
    tool = _make_tool_record("external_mcp", input_schema=_FREEBUSY_SCHEMA)

    result = await _run(tool, {"time_min": "2026-07-22T00:00:00Z"}, mock_call_mcp)

    mock_call_mcp.assert_not_called()
    assert "time_max" in result["error"]
    assert "time_min" not in result["error"]


@pytest.mark.asyncio
async def test_all_required_args_present_dispatches():
    """A well-formed call passes straight through to the MCP server."""
    mock_call_mcp = AsyncMock(return_value={"calendars": {}})
    tool = _make_tool_record("external_mcp", input_schema=_FREEBUSY_SCHEMA)

    result = await _run(
        tool,
        {"time_min": "2026-07-22T00:00:00Z", "time_max": "2026-07-22T23:59:59Z"},
        mock_call_mcp,
    )

    mock_call_mcp.assert_called_once()
    assert result == {"calendars": {}}


@pytest.mark.asyncio
async def test_no_required_in_schema_dispatches_with_empty_args():
    """A tool whose schema declares no required fields (e.g. get_events) still
    dispatches with {} — we only reject fields the schema explicitly requires."""
    mock_call_mcp = AsyncMock(return_value={"events": []})
    schema = {"type": "object", "properties": {"time_min": {"type": "string"}}}
    tool = _make_tool_record("external_mcp", input_schema=schema)

    result = await _run(tool, {}, mock_call_mcp)

    mock_call_mcp.assert_called_once()
    assert result == {"events": []}
