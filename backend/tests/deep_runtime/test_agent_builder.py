"""Unit tests for deep_runtime.agent_builder.build_deep_agent.

No live API calls — only asserts that the scaffold wires create_deep_agent and
returns a LangGraph CompiledStateGraph. Construction of the compiled graph does
not contact the Anthropic API.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.graph.state import CompiledStateGraph

from src.deep_runtime.agent_builder import build_deep_agent
from src.orchestrator.agents import SubAgent, ThinkingConfig


@tool
def echo(text: str) -> str:
    """Echo the given text back."""
    return text


@tool
def send_email(to: str, subject: str, body: str) -> str:
    """Send an email."""
    return "sent"


def _agent() -> SubAgent:
    return SubAgent(
        name="planner",
        prompt="You are the planner.",
        model_tier="opus",
        capability_scope=set(),
        max_tokens=8192,
        temperature=0.3,
        thinking=ThinkingConfig(enabled=True, budget_tokens=8192),
    )


def _operator_agent(capability_scope: set[str]) -> SubAgent:
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
    """An async-context-manager factory that yields a sentinel DB object."""

    @asynccontextmanager
    async def _factory():
        yield SimpleNamespace(name="fake-db")

    return _factory


async def test_build_deep_agent_returns_compiled_state_graph():
    agent = await build_deep_agent(_agent(), tools=[echo])
    assert isinstance(agent, CompiledStateGraph)


async def test_build_deep_agent_accepts_name_and_system_prompt_overrides():
    agent = await build_deep_agent(
        _agent(),
        tools=[echo],
        system_prompt="custom prompt",
        name="custom-name",
    )
    assert isinstance(agent, CompiledStateGraph)


async def test_build_deep_agent_accepts_extra_middleware():
    # Empty extra middleware is the Phase-1 default; the scaffold must accept it.
    agent = await build_deep_agent(_agent(), tools=[echo], extra_middleware=())
    assert isinstance(agent, CompiledStateGraph)


async def test_build_deep_agent_installs_scope_guard_and_blocks_out_of_scope():
    """A built agent's installed capability_scope guard blocks an out-of-scope tool."""
    agent = _operator_agent({"calendar.read"})
    resolver = AsyncMock()
    resolver.is_write_capability = AsyncMock(return_value=False)
    registry = AsyncMock()
    registry.get_tool = AsyncMock(
        return_value=SimpleNamespace(capability="email.send", server="gmail")
    )
    with (
        patch("src.deep_runtime.agent_builder.CapabilityResolver", return_value=resolver),
        patch("src.deep_runtime.middleware.capability_scope.ToolRegistry", return_value=registry),
    ):
        compiled = await build_deep_agent(
            agent,
            tools=[send_email],
            workspace_id="ws_test",
            db_factory=_fake_db_factory(),
        )
        assert isinstance(compiled, CompiledStateGraph)
        from src.deep_runtime.middleware.capability_scope import make_capability_scope_middleware

        guard = make_capability_scope_middleware(
            agent=agent,
            workspace_id="ws_test",
            db_factory=_fake_db_factory(),
        )
        handler = AsyncMock()
        handler.return_value = ToolMessage(content="executed", tool_call_id="call_123")
        request = SimpleNamespace(tool_call={"name": "send_email", "args": {}, "id": "call_123"})
        result = await guard.awrap_tool_call(request, handler)
    handler.assert_not_awaited()
    assert result.status == "error"


async def test_build_deep_agent_refuses_write_agent_without_scope_middleware():
    """Builder refuses to compile a write-capable agent with no scope guard."""
    import pytest

    agent = _operator_agent({"email.send"})
    resolver = AsyncMock()
    resolver.is_write_capability = AsyncMock(return_value=True)
    with patch("src.deep_runtime.agent_builder.CapabilityResolver", return_value=resolver):
        with pytest.raises(ValueError, match="refusing to compile agent 'operator'"):
            await build_deep_agent(agent, tools=[send_email], db_factory=None)
