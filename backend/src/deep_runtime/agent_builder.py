"""Scaffold wiring a Muldro ``SubAgent`` onto a deepagents ``CompiledStateGraph``.

Phase-1 foundation: this wires ``create_deep_agent`` with the agent's model
(via ``build_chat_model``), tools, system prompt, and the capability-scope guard
(installed first when a ``db_factory`` is provided).

Fail-closed at construction: if the agent has a write-class capability in its
``capability_scope`` and no ``db_factory`` is supplied (so no scope guard can be
installed), ``build_deep_agent`` raises ``ValueError`` rather than silently
producing an unguarded agent. This prevents the ungated chat path from ever
running a write-capable agent without the safety net.

Additional Muldro policy middlewares (Budget, ModelResilience, ContextPack,
TurnScope, …) are threaded in via ``extra_middleware`` in later phases.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from deepagents import create_deep_agent
from langchain_core.messages import SystemMessage
from langgraph.graph.state import CompiledStateGraph

from src.deep_runtime.middleware.capability_scope import make_capability_scope_middleware
from src.deep_runtime.middleware.no_virtual_filesystem import (
    make_no_virtual_filesystem_middleware,
)
from src.deep_runtime.model_factory import build_chat_model
from src.orchestrator.agents import SubAgent
from src.services.capability_resolver import CapabilityResolver

logger = logging.getLogger(__name__)


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
        logger.warning(
            "[deep_runtime] %s — capability lookup failed at construction (fail-closed)",
            agent.name,
        )
        return True  # cannot prove read-only -> fail closed


async def build_deep_agent(
    agent: SubAgent,
    tools: list[Any],
    *,
    workspace_id: str = "",
    db_factory=None,
    extra_middleware: Sequence[Any] = (),
    subagents: Sequence[Any] = (),
    system_prompt: str | SystemMessage | None = None,
    name: str | None = None,
    checkpointer=None,
) -> CompiledStateGraph:
    """Build a compiled deep agent for *agent*.

    Installs the capability-scope guard first (when ``db_factory`` is given),
    then appends ``extra_middleware``. Fail-closed at construction: raises
    ``ValueError`` if the agent has a write-class capability in scope but no
    scope guard would be installed (i.e. ``db_factory`` is None).

    Args:
        agent: The Muldro sub-agent definition (drives model tier + thinking).
        tools: LangChain tools the agent may call (capability-resolved upstream).
        workspace_id: Tenant scope for registry lookups.
        db_factory: Async-context-manager factory yielding an ``AsyncSession``.
            Required for write-capable agents; omitting it causes a ``ValueError``
            when the agent has any write-class capability in scope.
        extra_middleware: Additional Muldro policy middlewares to install after
            the scope guard (none in Phase 1).
        subagents: Read-only Muldro delegates (``SubAgent``/``CompiledSubAgent`` dicts)
            registered on the lead so its built-in ``task`` tool can route to them.
            Empty by default → forwarded as ``None`` so the call is byte-identical to
            today when no delegates are wired. Note: the fail-closed write guard below
            inspects only the LEAD ``agent``, never these subagents.
        system_prompt: Override for the agent's role prompt; may be a plain string or a
            structured ``SystemMessage`` (e.g. from ``build_system_message``) to preserve
            per-block ``cache_control`` markers; defaults to ``agent.prompt``.
        name: Override for the agent name; defaults to ``agent.name``.
        checkpointer: LangGraph checkpointer forwarded to create_deep_agent so a
            later gate can raise interrupt(); None in 6A.

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
    # Suppress deepagents' virtual-filesystem tools from every model request. They are
    # auto-installed by required middleware we cannot drop, and Muldro has no filesystem
    # feature — so a model that "saves" something there loses it at end of thread while
    # reporting success. Installed for EVERY agent (chat lead and autonomous step alike):
    # none of them has a filesystem story, and this is the one choke point they share.
    middleware.append(make_no_virtual_filesystem_middleware())

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

    model = await build_chat_model(agent, workspace_id=workspace_id, db_factory=db_factory)
    effective_system_prompt = system_prompt or agent.prompt
    if db_factory is not None and system_prompt is not None:
        from src.deep_runtime.prompt_bridge import strip_cache_control
        from src.services.model_resolver import ModelResolver

        async with db_factory() as db:
            supports_cache = await ModelResolver(db).supports_prompt_cache(
                agent=agent.name, agent_tier=agent.model_tier, workspace_id=workspace_id or None
            )
        if not supports_cache:
            effective_system_prompt = strip_cache_control(system_prompt)

    return create_deep_agent(
        model=model,
        tools=tools,
        system_prompt=effective_system_prompt,
        middleware=middleware,
        subagents=subagents or None,
        name=name or agent.name,
        checkpointer=checkpointer,
    )
