"""Tests for HomeFeedService event descriptions."""

from src.models.ids import generate_id
from src.models.runtime_event import RuntimeEvent
from src.services.home_feed import _event_description


class TestEventDescription:
    def test_tool_call_started(self):
        event = RuntimeEvent(
            event_id=generate_id("revt"),
            workspace_id="ws_test",
            event_type="tool_call_started",
            payload={"tool_name": "gmail_send"},
        )
        desc = _event_description(event)
        assert "gmail_send" in desc

    def test_run_completed(self):
        event = RuntimeEvent(
            event_id=generate_id("revt"),
            workspace_id="ws_test",
            event_type="run_completed",
            payload={},
        )
        desc = _event_description(event)
        assert "completed" in desc.lower()

    def test_run_failed(self):
        event = RuntimeEvent(
            event_id=generate_id("revt"),
            workspace_id="ws_test",
            event_type="run_failed",
            payload={"error": "timeout"},
        )
        desc = _event_description(event)
        assert "timeout" in desc

    def test_agent_started(self):
        event = RuntimeEvent(
            event_id=generate_id("revt"),
            workspace_id="ws_test",
            event_type="agent_started",
            payload={"agent_name": "observer"},
        )
        desc = _event_description(event)
        assert "observer" in desc

    def test_unknown_event_type(self):
        event = RuntimeEvent(
            event_id=generate_id("revt"),
            workspace_id="ws_test",
            event_type="custom_event",
            payload={},
        )
        desc = _event_description(event)
        assert desc == "custom_event"

    def test_missing_payload(self):
        event = RuntimeEvent(
            event_id=generate_id("revt"),
            workspace_id="ws_test",
            event_type="route_selected",
            payload=None,
        )
        desc = _event_description(event)
        assert "Route selected" in desc
