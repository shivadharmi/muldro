"""Regression: the run-driving path must activate a TurnScope and close sessions on exit.

Step 10C P3 extracted ``execute_run``'s body into ``_execute_run_body`` (behind the
single-flight durable lease gate); the ``turn_scope(on_close=close_turn_sessions)``
activation that fences per-turn MCP sessions moved with it. Assert against the body — the
method that actually activates the scope — not the thin lease-gate wrapper.
"""

import inspect

from src.services import graph_executor as ge


def test_execute_run_uses_turn_scope():
    src = inspect.getsource(ge.GraphExecutor._execute_run_body)
    assert "turn_scope(" in src
    assert "close_turn_sessions" in src
