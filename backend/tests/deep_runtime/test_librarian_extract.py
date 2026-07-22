"""Unit tests for deep_runtime.middleware.librarian_extract (Step 7B1 P3).

The middleware is an ``@after_model`` hook (async, invoked via ``aafter_model``) that
relocates the chat ``InteractionLearner`` extraction trigger into the deep turn. Three
load-bearing properties, each with test teeth:

* TERMINAL-round fires: on the final model round (the last AI message has NO tool_calls)
  it awaits the injected ``learn(user_message, agent_response)`` exactly once.
* INTERMEDIATE-round skips: when the last AI message HAS tool_calls (an intermediate
  round — ``@after_model`` fires per model round, spike 0.2), ``learn`` is NOT awaited.
* DORMANT skips: built with ``active=False`` (the live-path default so it never
  double-fires with the still-live ``InteractionLearner``), ``learn`` is NOT awaited.

Best-effort: an extraction failure must never break the turn (the hook always returns
``None`` and swallows the exception).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage

from src.deep_runtime.middleware.librarian_extract import make_librarian_extract_middleware

WORKSPACE_ID = "ws_test"
USER_ID = "u_test"


def _make(active: bool, learn: AsyncMock) -> AgentMiddleware:
    return make_librarian_extract_middleware(
        workspace_id=WORKSPACE_ID, user_id=USER_ID, learn=learn, active=active
    )


async def _run_hook(mw: AgentMiddleware, state: dict):
    """Invoke the middleware's async after_model hook with a dummy runtime."""
    return await mw.aafter_model(state, MagicMock())


def _terminal_state() -> dict:
    """A terminal round: last AI message carries the final text, NO tool_calls."""
    return {
        "messages": [
            HumanMessage(content="remember Bob works at Acme"),
            AIMessage(content="Noted — Bob @ Acme."),
        ]
    }


def test_factory_returns_agent_middleware():
    mw = _make(active=False, learn=AsyncMock())
    assert isinstance(mw, AgentMiddleware)


async def test_terminal_round_fires_learn_once():
    """TOOTH 1: terminal round (no tool_calls) → learn awaited once with (user, response)."""
    learn = AsyncMock()
    mw = _make(active=True, learn=learn)

    result = await _run_hook(mw, _terminal_state())

    assert result is None  # pure side effect
    learn.assert_awaited_once_with("remember Bob works at Acme", "Noted — Bob @ Acme.")


async def test_intermediate_round_does_not_fire():
    """TOOTH 2: last AI message HAS tool_calls → intermediate round → learn NOT awaited.

    ``@after_model`` fires per model round (spike 0.2); extraction must run only on the
    terminal round so it fires exactly ONCE per turn, never mid-turn.
    """
    learn = AsyncMock()
    mw = _make(active=True, learn=learn)
    state = {
        "messages": [
            HumanMessage(content="remember Bob works at Acme"),
            AIMessage(
                content="",
                tool_calls=[{"name": "store_memory", "args": {}, "id": "t1"}],
            ),
        ]
    }

    result = await _run_hook(mw, state)

    assert result is None
    learn.assert_not_awaited()


async def test_dormant_does_not_fire():
    """TOOTH 3: active=False (live-path default) → learn NOT awaited (no double-fire with
    the still-live InteractionLearner)."""
    learn = AsyncMock()
    mw = _make(active=False, learn=learn)

    result = await _run_hook(mw, _terminal_state())

    assert result is None
    learn.assert_not_awaited()


async def test_learn_exception_is_swallowed():
    """Best-effort: a failing ``learn`` never raises into the agent loop."""
    learn = AsyncMock(side_effect=RuntimeError("extraction boom"))
    mw = _make(active=True, learn=learn)

    result = await _run_hook(mw, _terminal_state())

    assert result is None
    learn.assert_awaited_once()


async def test_empty_messages_does_not_fire():
    """No messages → nothing to extract, learn NOT awaited, no crash."""
    learn = AsyncMock()
    mw = _make(active=True, learn=learn)

    result = await _run_hook(mw, {"messages": []})

    assert result is None
    learn.assert_not_awaited()


async def test_no_ai_message_does_not_fire():
    """A round with no AI message (only a human turn) → learn NOT awaited."""
    learn = AsyncMock()
    mw = _make(active=True, learn=learn)

    result = await _run_hook(mw, {"messages": [HumanMessage(content="hi")]})

    assert result is None
    learn.assert_not_awaited()
