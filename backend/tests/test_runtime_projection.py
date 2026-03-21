"""Tests for RuntimeEvent model and RuntimeProjectionService."""

from src.models.ids import generate_id, validate_typed_id
from src.models.runtime_event import RuntimeEvent


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
