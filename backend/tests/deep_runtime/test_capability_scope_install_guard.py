"""Regression lock for the build-time capability_scope fail-closed guard.

Step 10A (NEW-2): the guard already exists in ``build_deep_agent``
(``src/deep_runtime/agent_builder.py:104-124``) and is already partially covered
by ``tests/deep_runtime/test_agent_builder.py``. This file adds only the two
pieces of coverage that were genuinely missing:

1. A mutation-proving regression lock on the existing fail-closed raise (a write
   -capable agent with no ``db_factory`` must refuse to compile) — proven with
   teeth via a manual negative-control mutation (see task report, not committed
   here).
2. The guard-POSITION delta: a compiled write-capable agent's installed
   ``capability_scope_guard`` middleware must be OUTERMOST (index 0), ahead of
   any ``extra_middleware`` — not merely present. In langchain 1.3.10,
   ``langchain/agents/factory.py::_chain_tool_call_wrappers`` composes the
   ``wrap_tool_call`` stack with "first = outermost": the outermost wrapper
   runs first and, on denial, never calls the inner ``handler``, so no other
   middleware (and never the tool) executes. Position — not just presence — is
   the load-bearing security property.

This file adds NO new assertion to ``agent_builder.py`` and does not
re-implement write-classification as a pure helper; it only locks the existing
guard behavior with tests.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from langchain.agents.middleware import wrap_tool_call

from src.deep_runtime import agent_builder
from src.orchestrator.agents import SubAgent, ThinkingConfig


def _executor_agent(capability_scope: set[str]) -> SubAgent:
    return SubAgent(
        name="executor",
        prompt="You are the executor.",
        model_tier="sonnet",
        capability_scope=capability_scope,
        max_tokens=2048,
        temperature=0.3,
        thinking=ThinkingConfig(enabled=False, budget_tokens=0),
    )


def _fake_db_factory():
    """An async-context-manager factory that yields a sentinel DB object."""

    @asynccontextmanager
    async def _factory():
        yield SimpleNamespace(name="fake-db")

    return _factory


def _is_scope_guard(mw: object) -> bool:
    return (
        getattr(mw, "name", None) == "capability_scope_guard"
        or type(mw).__name__ == "capability_scope_guard"
    )


async def test_write_capable_agent_without_db_factory_refuses_to_compile():
    """Regression lock: a write-capable agent built with db_factory=None must
    refuse to compile (fail-closed) because no capability_scope guard can be
    installed. Locks the raise at agent_builder.py:119-124.
    """
    agent = _executor_agent({"email.send"})
    resolver = AsyncMock()
    resolver.is_write_capability = AsyncMock(return_value=True)
    with patch("src.deep_runtime.agent_builder.CapabilityResolver", return_value=resolver):
        with pytest.raises(ValueError, match="capability_scope"):
            await agent_builder.build_deep_agent(agent, tools=[], db_factory=None)


async def test_compiled_write_agent_installs_scope_guard_outermost():
    """Step-4 delta (option a): when a write-capable agent IS built with a
    db_factory (guard installs, construction does NOT raise), the
    capability_scope_guard middleware must be OUTERMOST — index 0, ahead of
    every other wrap_tool_call middleware — not merely present in the list.
    """

    @wrap_tool_call
    async def dummy_extra_guard(request, handler):
        return await handler(request)

    agent = _executor_agent({"email.send"})
    resolver = AsyncMock()
    resolver.is_write_capability = AsyncMock(return_value=True)
    with (
        patch("src.deep_runtime.agent_builder.CapabilityResolver", return_value=resolver),
        patch.object(
            agent_builder, "build_chat_model", new=AsyncMock(return_value=SimpleNamespace())
        ),
        patch.object(agent_builder, "create_deep_agent") as mock_create,
    ):
        await agent_builder.build_deep_agent(
            agent,
            tools=[],
            workspace_id="ws_test",
            db_factory=_fake_db_factory(),
            extra_middleware=[dummy_extra_guard],
        )

    middleware = mock_create.call_args.kwargs["middleware"]
    # >= 2 wrap_tool_call middlewares so "index 0" is a meaningful position
    # claim, not trivially true for a 1-element list.
    assert len(middleware) == 2
    # langchain 1.3.10 `_chain_tool_call_wrappers` docstring: "Compose wrappers
    # into middleware stack (first = outermost)" — index 0 runs first and, on
    # denial, short-circuits before any inner middleware or the tool runs.
    assert _is_scope_guard(middleware[0])
    assert not _is_scope_guard(middleware[1])
