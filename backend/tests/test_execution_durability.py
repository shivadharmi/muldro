"""Tests for execution durability: cancellation tokens and state transitions."""

import asyncio
import inspect

import pytest

from src.orchestrator.agent_loop import CancellationRequested, _check_cancellation
from src.services.execution_state import STEP_TRANSITIONS


class TestCancellationToken:
    def test_set_event_raises_cancellation(self):
        cancel_event = asyncio.Event()
        cancel_event.set()
        with pytest.raises(CancellationRequested):
            _check_cancellation(cancel_event)

    def test_unset_event_does_not_raise(self):
        cancel_event = asyncio.Event()
        _check_cancellation(cancel_event)  # should not raise

    def test_none_event_does_not_raise(self):
        _check_cancellation(None)  # should not raise

    def test_cancel_events_dict_on_graph_executor(self):
        from src.services.graph_executor import GraphExecutor

        # Verify the class has the cancel_events attribute in __init__
        assert hasattr(GraphExecutor, "__init__")
        source = inspect.getsource(GraphExecutor.__init__)
        assert "_cancel_events" in source

    def test_step_cancelled_transition_valid(self):
        assert "cancelled" in STEP_TRANSITIONS["running"]
        assert "cancelled" in STEP_TRANSITIONS  # terminal state exists
        assert STEP_TRANSITIONS["cancelled"] == set()  # terminal

    def test_agent_loop_accepts_cancel_event(self):
        from src.orchestrator.agent_loop import agent_loop

        sig = inspect.signature(agent_loop)
        assert "cancel_event" in sig.parameters

    def test_cancellation_requested_is_exception(self):
        assert issubclass(CancellationRequested, Exception)
        exc = CancellationRequested("test message")
        assert str(exc) == "test message"
