"""Regression: _process_core must activate a TurnScope and close sessions on exit."""

import inspect

from src.orchestrator import jarvis as jarvis_mod


def test_process_core_uses_turn_scope():
    src = inspect.getsource(jarvis_mod.JarvisOrchestrator._process_core)
    assert "turn_scope(" in src
    assert "close_turn_sessions" in src
