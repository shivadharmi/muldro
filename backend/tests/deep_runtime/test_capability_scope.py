"""Unit tests for deep_runtime.middleware.capability_scope.

CRITICAL-SECURITY middleware: the only safety net on the ungated chat path. It
ports the legacy ``_capability_in_scope`` enforcement from agent_loop.py into a
deepagents ``wrap_tool_call`` interceptor. Tests exercise the interceptor's
fail-closed behavior directly — no live Anthropic API, no real DB.

Per the Phase 0 lesson we use a *benign* tool name/capability (``multiply`` /
``math.multiply``) so the test proves the interceptor blocks, not that a model
self-refused (there is no model here, but the convention stays).
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import ToolMessage

from src.deep_runtime.middleware.capability_scope import (
    make_capability_scope_middleware,
)
from src.orchestrator.agents import SubAgent, ThinkingConfig

WORKSPACE_ID = "ws_test"


def _agent(capability_scope: set[str]) -> SubAgent:
    return SubAgent(
        name="operator",
        prompt="You are the operator.",
        model_tier="sonnet",
        capability_scope=capability_scope,
        max_tokens=2048,
        temperature=0.3,
        thinking=ThinkingConfig(enabled=False, budget_tokens=0),
    )


def _fake_db_factory():
    """An async-context-manager factory that yields a sentinel DB object.

    ToolRegistry is patched, so the yielded value is never really used; we only
    need ``async with db_factory() as db:`` to work.
    """

    @asynccontextmanager
    async def _factory():
        yield SimpleNamespace(name="fake-db")

    return _factory


def _request(tool_name: str):
    """Minimal ToolCallRequest stand-in: only ``.tool_call`` is read."""
    return SimpleNamespace(tool_call={"name": tool_name, "args": {}, "id": "call_123"})


def _hook(mw):
    """Extract the async wrap-tool-call hook bound on the middleware instance."""
    return mw.awrap_tool_call


@pytest.fixture
def handler():
    """An AsyncMock standing in for the downstream tool executor (``handler``)."""
    h = AsyncMock(name="handler")
    h.return_value = ToolMessage(content="executed", tool_call_id="call_123")
    return h


async def test_in_scope_tool_is_allowed(handler):
    """A tool whose capability is in the agent's scope runs via handler."""
    agent = _agent({"math.multiply"})
    mw = make_capability_scope_middleware(
        agent=agent, workspace_id=WORKSPACE_ID, db_factory=_fake_db_factory()
    )
    tool = SimpleNamespace(capability="math.multiply", server="math")

    registry = AsyncMock()
    registry.get_tool = AsyncMock(return_value=tool)
    with patch(
        "src.deep_runtime.middleware.capability_scope.ToolRegistry",
        return_value=registry,
    ):
        result = await _hook(mw)(_request("multiply"), handler)

    handler.assert_awaited_once()
    # The interceptor returns whatever handler returned, unchanged.
    assert result is handler.return_value
    registry.get_tool.assert_awaited_once_with("multiply")


async def test_out_of_scope_benign_tool_is_blocked(handler):
    """A benign tool whose capability is NOT in scope is blocked (handler not called)."""
    agent = _agent({"email.send"})  # scope present, but lacks math.multiply
    mw = make_capability_scope_middleware(
        agent=agent, workspace_id=WORKSPACE_ID, db_factory=_fake_db_factory()
    )
    tool = SimpleNamespace(capability="math.multiply", server="math")

    registry = AsyncMock()
    registry.get_tool = AsyncMock(return_value=tool)
    with patch(
        "src.deep_runtime.middleware.capability_scope.ToolRegistry",
        return_value=registry,
    ):
        result = await _hook(mw)(_request("multiply"), handler)

    handler.assert_not_awaited()
    assert isinstance(result, ToolMessage)
    assert result.tool_call_id == "call_123"
    assert result.status == "error"
    payload = json.loads(result.content)
    assert payload["error"] == (
        "Agent 'operator' is not permitted to call 'multiply' — capability is outside its scope."
    )


async def test_tool_capability_none_is_blocked(handler):
    """Fail-closed: a tool with capability=None is denied."""
    agent = _agent({"math.multiply"})
    mw = make_capability_scope_middleware(
        agent=agent, workspace_id=WORKSPACE_ID, db_factory=_fake_db_factory()
    )
    tool = SimpleNamespace(capability=None, server="math")

    registry = AsyncMock()
    registry.get_tool = AsyncMock(return_value=tool)
    with patch(
        "src.deep_runtime.middleware.capability_scope.ToolRegistry",
        return_value=registry,
    ):
        result = await _hook(mw)(_request("multiply"), handler)

    handler.assert_not_awaited()
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert json.loads(result.content)["error"].startswith(
        "Agent 'operator' is not permitted to call 'multiply'"
    )


async def test_unknown_tool_is_blocked(handler):
    """Fail-closed: an unknown tool (registry returns None) is denied."""
    agent = _agent({"math.multiply"})
    mw = make_capability_scope_middleware(
        agent=agent, workspace_id=WORKSPACE_ID, db_factory=_fake_db_factory()
    )

    registry = AsyncMock()
    registry.get_tool = AsyncMock(return_value=None)
    with patch(
        "src.deep_runtime.middleware.capability_scope.ToolRegistry",
        return_value=registry,
    ):
        result = await _hook(mw)(_request("multiply"), handler)

    handler.assert_not_awaited()
    assert isinstance(result, ToolMessage)
    assert result.status == "error"


async def test_empty_capability_scope_is_blocked(handler):
    """Fail-closed: an agent with an empty scope can call nothing.

    The registry must NOT even be consulted in this short-circuit path.
    """
    agent = _agent(set())
    mw = make_capability_scope_middleware(
        agent=agent, workspace_id=WORKSPACE_ID, db_factory=_fake_db_factory()
    )

    registry = AsyncMock()
    registry.get_tool = AsyncMock(return_value=SimpleNamespace(capability="x"))
    with patch(
        "src.deep_runtime.middleware.capability_scope.ToolRegistry",
        return_value=registry,
    ):
        result = await _hook(mw)(_request("multiply"), handler)

    handler.assert_not_awaited()
    registry.get_tool.assert_not_awaited()
    assert isinstance(result, ToolMessage)
    assert result.status == "error"


async def test_db_exception_is_blocked(handler):
    """Fail-closed: a registry lookup error DENIES the call (not propagate)."""
    agent = _agent({"math.multiply"})
    mw = make_capability_scope_middleware(
        agent=agent, workspace_id=WORKSPACE_ID, db_factory=_fake_db_factory()
    )
    registry = AsyncMock()
    registry.get_tool = AsyncMock(side_effect=RuntimeError("db down"))
    with patch(
        "src.deep_runtime.middleware.capability_scope.ToolRegistry",
        return_value=registry,
    ):
        result = await _hook(mw)(_request("multiply"), handler)
    handler.assert_not_awaited()
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
