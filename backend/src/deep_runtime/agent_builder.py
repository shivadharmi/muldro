"""Scaffold wiring a Jarvis ``SubAgent`` onto a deepagents ``CompiledStateGraph``.

Phase-1 foundation: this wires ``create_deep_agent`` with the agent's model
(via ``build_chat_model``), tools, system prompt, and the capability-scope guard
(installed first when a ``db_factory`` is provided).

Fail-closed at construction: if the agent has a write-class capability in its
``capability_scope`` and no ``db_factory`` is supplied (so no scope guard can be
installed), ``build_deep_agent`` raises ``ValueError`` rather than silently
producing an unguarded agent. This prevents the ungated chat path from ever
running a write-capable agent without the safety net.

Additional Jarvis policy middlewares (Budget, ModelResilience, ContextPack,
TurnScope, …) are threaded in via ``extra_middleware`` in later phases.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from deepagents import create_deep_agent
from langgraph.graph.state import CompiledStateGraph

from src.deep_runtime.middleware.capability_scope import make_capability_scope_middleware
from src.deep_runtime.model_factory import build_chat_model
from src.orchestrator.agents import SubAgent
from src.services.capability_resolver import CapabilityResolver


async def _has_write_capability_in_scope(agent: SubAgent, workspace_id: str, db_factory) -> bool:
    scope = getattr(agent, "capability_scope", None)
    if not scope:
        return False
    if db_factory is None:
        return True  # cannot prove read-only -> fail closed
    try:
        async with db_factory() as db:
            resolver = CapabilityResolver(db, workspace_id=workspace_id or "")
            for capability in scope:
                if await resolver.is_write_capability(capability):
                    return True
        return False
    except Exception:
        return True  # cannot prove read-only -> fail closed


async def build_deep_agent(
    agent: SubAgent,
    tools: list[Any],
    *,
    workspace_id: str = "",
    db_factory=None,
    extra_middleware: Sequence[Any] = (),
    system_prompt: str | None = None,
    name: str | None = None,
) -> CompiledStateGraph:
    """Build a compiled deep agent for *agent*.

    Installs the capability-scope guard first (when ``db_factory`` is given),
    then appends ``extra_middleware``. Fail-closed at construction: raises
    ``ValueError`` if the agent has a write-class capability in scope but no
    scope guard would be installed (i.e. ``db_factory`` is None).

    Args:
        agent: The Jarvis sub-agent definition (drives model tier + thinking).
        tools: LangChain tools the agent may call (capability-resolved upstream).
        workspace_id: Tenant scope for registry lookups.
        db_factory: Async-context-manager factory yielding an ``AsyncSession``.
            Required for write-capable agents; omitting it causes a ``ValueError``
            when the agent has any write-class capability in scope.
        extra_middleware: Additional Jarvis policy middlewares to install after
            the scope guard (none in Phase 1).
        system_prompt: Override for the agent's role prompt; defaults to ``agent.prompt``.
        name: Override for the agent name; defaults to ``agent.name``.

    Returns:
        A LangGraph ``CompiledStateGraph`` ready for ``.ainvoke()``/``.astream()``.

    Raises:
        ValueError: If the agent has a write-class capability in scope but no
            capability_scope middleware would be installed (fail-closed).
    """
    middleware: list[Any] = []
    if db_factory is not None:
        middleware.append(
            make_capability_scope_middleware(
                agent=agent,
                workspace_id=workspace_id,
                db_factory=db_factory,
            )
        )
    middleware.extend(extra_middleware)

    has_scope_mw = any(
        getattr(mw, "name", None) == "capability_scope_guard"
        or type(mw).__name__ == "capability_scope_guard"
        for mw in middleware
    )
    if not has_scope_mw and await _has_write_capability_in_scope(agent, workspace_id, db_factory):
        raise ValueError(
            f"refusing to compile agent '{agent.name}': it has a write-class capability "
            "in scope but no capability_scope middleware would be installed (fail-closed). "
            "Pass db_factory so the scope guard is installed."
        )

    return create_deep_agent(
        model=build_chat_model(agent),
        tools=tools,
        system_prompt=system_prompt or agent.prompt,
        middleware=middleware,
        name=name or agent.name,
    )
