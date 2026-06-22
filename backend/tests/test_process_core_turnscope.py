"""Regression: _process_core must activate a TurnScope and close sessions on exit."""

import inspect

from src.orchestrator import chat_processor as chat_mod


def test_process_core_uses_turn_scope():
    src = inspect.getsource(chat_mod.ChatProcessor._process_core)
    assert "turn_scope(" in src
    assert "close_turn_sessions" in src
