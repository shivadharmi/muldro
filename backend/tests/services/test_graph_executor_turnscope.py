"""Regression: execute_run must activate a TurnScope and close sessions on exit."""

import inspect

from src.services import graph_executor as ge


def test_execute_run_uses_turn_scope():
    src = inspect.getsource(ge.GraphExecutor.execute_run)
    assert "turn_scope(" in src
    assert "close_turn_sessions" in src
