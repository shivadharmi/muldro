"""Scaffold wiring a Jarvis ``SubAgent`` onto a deepagents ``CompiledStateGraph``.

Phase-1 foundation: this only wires ``create_deep_agent`` with the agent's model
(via ``build_chat_model``), tools, system prompt, and an (optional) extra
middleware list. The Jarvis policy middlewares (CapabilityScope, Budget,
ModelResilience, ContextPack, TurnScope, …) are built in the NEXT phase and
threaded in via ``extra_middleware``; this scaffold deliberately ships none.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from deepagents import create_deep_agent
from langgraph.graph.state import CompiledStateGraph

from src.deep_runtime.model_factory import build_chat_model
from src.orchestrator.agents import SubAgent


def build_deep_agent(
    agent: SubAgent,
    tools: list[Any],
    *,
    extra_middleware: Sequence[Any] = (),
    system_prompt: str | None = None,
    name: str | None = None,
) -> CompiledStateGraph:
    """Build a compiled deep agent for *agent*.

    Args:
        agent: The Jarvis sub-agent definition (drives model tier + thinking).
        tools: LangChain tools the agent may call (capability-resolved upstream).
        extra_middleware: Jarvis policy middlewares to install (none in Phase 1).
        system_prompt: Override for the agent's role prompt; defaults to ``agent.prompt``.
        name: Override for the agent name; defaults to ``agent.name``.

    Returns:
        A LangGraph ``CompiledStateGraph`` ready for ``.ainvoke()``/``.astream()``.
    """
    return create_deep_agent(
        model=build_chat_model(agent),
        tools=tools,
        system_prompt=system_prompt or agent.prompt,
        middleware=list(extra_middleware),
        name=name or agent.name,
    )
