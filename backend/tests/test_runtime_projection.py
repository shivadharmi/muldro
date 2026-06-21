"""Tests for RuntimeEvent model and RuntimeProjectionService."""

from unittest.mock import MagicMock

import pytest

from src.models.ids import generate_id, validate_typed_id
from src.models.runtime_event import RuntimeEvent
from src.services.runtime_projection import RuntimeProjectionService


class TestRuntimeEvent:
    def test_generate_event_id(self):
        event_id = generate_id("revt")
        assert event_id.startswith("revt_")
        assert validate_typed_id(event_id, "revt")

    def test_create_runtime_event(self):
        event = RuntimeEvent(
            event_id=generate_id("revt"),
            workspace_id="ws_test",
            run_id="run_test123",
            step_id="step_test456",
            event_type="tool_call_started",
            payload={"tool_name": "gmail_send", "capability": "email.send"},
        )
        assert event.event_type == "tool_call_started"
        assert event.payload["tool_name"] == "gmail_send"
        assert event.run_id == "run_test123"

    def test_event_types(self):
        valid_types = [
            "route_selected",
            "agent_started",
            "tool_call_started",
            "tool_call_completed",
            "approval_requested",
            "artifact_created",
            "fallback_triggered",
            "run_completed",
            "run_failed",
        ]
        for event_type in valid_types:
            event = RuntimeEvent(
                event_id=generate_id("revt"),
                workspace_id="ws_test",
                event_type=event_type,
            )
            assert event.event_type == event_type

    def test_optional_fields(self):
        event = RuntimeEvent(
            event_id=generate_id("revt"),
            workspace_id="ws_test",
            event_type="run_completed",
        )
        assert event.run_id is None
        assert event.step_id is None
        assert event.payload is None


# ---------------------------------------------------------------------------
# get_active_agents — fakes for db.execute()
# ---------------------------------------------------------------------------


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeResult:
    """Supports both ``.all()`` (row tuples) and ``.scalars().all()``."""

    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def scalars(self):
        return _FakeScalars(self._rows)


def _tool(capability, requires_approval):
    t = MagicMock()
    t.capability = capability
    t.requires_approval = requires_approval
    return t


def _make_db(step_rows, tool_rows):
    """db whose execute() returns step rows first, then tool rows."""
    results = [_FakeResult(step_rows), _FakeResult(tool_rows)]
    call_index = 0

    async def fake_execute(_stmt, *args, **kwargs):
        nonlocal call_index
        result = results[call_index] if call_index < len(results) else _FakeResult([])
        call_index += 1
        return result

    db = MagicMock()
    db.execute = fake_execute
    return db


class TestGetActiveAgents:
    @pytest.mark.asyncio
    async def test_returns_distinct_resolved_agent_names(self):
        # Two running steps map to perceiver (read), one write step -> operator,
        # one respond step -> presenter. Duplicate read capability collapses.
        step_rows = [
            ({"capability": "email.search"},),
            ({"capability": "email.search"},),
            ({"capability": "email.send"},),
            ({"capability": "respond"},),
        ]
        tool_rows = [
            _tool("email.search", requires_approval=False),
            _tool("email.send", requires_approval=True),
        ]
        svc = RuntimeProjectionService(_make_db(step_rows, tool_rows), "ws_test")
        agents = await svc.get_active_agents()
        assert agents == ["operator", "perceiver", "presenter"]

    @pytest.mark.asyncio
    async def test_knowledge_capability_routes_to_librarian(self):
        step_rows = [({"capability": "knowledge.search"},)]
        # No tools needed for knowledge.* prefix routing.
        svc = RuntimeProjectionService(_make_db(step_rows, []), "ws_test")
        agents = await svc.get_active_agents()
        assert agents == ["librarian"]

    @pytest.mark.asyncio
    async def test_empty_when_nothing_running(self):
        svc = RuntimeProjectionService(_make_db([], []), "ws_test")
        agents = await svc.get_active_agents()
        assert agents == []

    @pytest.mark.asyncio
    async def test_unknown_capability_is_skipped(self):
        step_rows = [
            ({"capability": "mystery.cap"},),  # no matching tool -> unroutable
            ({},),  # no capability key
            ({"capability": ""},),  # empty
        ]
        svc = RuntimeProjectionService(_make_db(step_rows, []), "ws_test")
        agents = await svc.get_active_agents()
        assert agents == []
