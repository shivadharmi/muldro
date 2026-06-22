"""Unit tests for deep_runtime.agent_builder.build_deep_agent.

No live API calls — only asserts that the scaffold wires create_deep_agent and
returns a LangGraph CompiledStateGraph. Construction of the compiled graph does
not contact the Anthropic API.
"""

from __future__ import annotations

from langchain_core.tools import tool
from langgraph.graph.state import CompiledStateGraph

from src.deep_runtime.agent_builder import build_deep_agent
from src.orchestrator.agents import SubAgent, ThinkingConfig


@tool
def echo(text: str) -> str:
    """Echo the given text back."""
    return text


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


def test_build_deep_agent_returns_compiled_state_graph():
    agent = build_deep_agent(_agent(), tools=[echo])
    assert isinstance(agent, CompiledStateGraph)


def test_build_deep_agent_accepts_name_and_system_prompt_overrides():
    agent = build_deep_agent(
        _agent(),
        tools=[echo],
        system_prompt="custom prompt",
        name="custom-name",
    )
    assert isinstance(agent, CompiledStateGraph)


def test_build_deep_agent_accepts_extra_middleware():
    # Empty extra middleware is the Phase-1 default; the scaffold must accept it.
    agent = build_deep_agent(_agent(), tools=[echo], extra_middleware=())
    assert isinstance(agent, CompiledStateGraph)
