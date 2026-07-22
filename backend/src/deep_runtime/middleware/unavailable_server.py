"""Per-turn "MCP server is down for auth → stop retrying it this turn" breaker.

Re-homes the legacy ``agent_loop`` unavailable-server circuit breaker onto a
LangChain ``wrap_tool_call`` middleware hook. The behaviour is ported faithfully
from ``src.orchestrator.agent_loop``:

- **Detect** the structured ``auth_required`` envelope a tool can return (carrying
  the real ``server`` — primary key, C5 — and optionally ``provider``), plus the
  legacy ``{"error": "MCP bridge not initialized"}`` shape.
- **Mark** the offending server (and the provider inferred from it) in per-turn
  sets, and **append a terminal steer** to the tool result telling the model to
  stop retrying the integration this turn.
- **Short-circuit** any *later* tool whose server (or name-inferred provider) is
  already known-down: skip the real tool call entirely and return a cached
  ``auth_required`` result + steer, so the model burns one round, not one per tool.

Per-turn isolation is structural: ``make_unavailable_server_middleware`` builds
**fresh** ``unavailable_servers``/``unavailable_providers`` sets per call, closed
over by the hook. A new middleware instance (one per turn, per
``build_deep_agent``) therefore always starts with empty sets — the legacy
"reset every turn" invariant.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, wrap_tool_call
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage

from src.deep_runtime.builtins import DEEPAGENTS_BUILTIN_NAMES
from src.integrations.provider_map import provider_for_server
from src.services.tool_registry import ToolRegistry

# ── Terminal steer text (ported verbatim from agent_loop.py) ──────────────────
# Appended to a tool result whose MCP server is unavailable (auth_required /
# bridge-not-initialized). Tells the model to stop retrying the integration this
# turn and report back instead of burning more rounds.
_UNAVAILABLE_STEER = (
    " This integration needs re-authorization and cannot be used in this session. "
    "Do not retry its tools; tell the user to reconnect it."
)
_BRIDGE_NOT_INIT_STEER = (
    " This integration is unavailable in this session. "
    "Do not retry its tools; tell the user to reconnect it."
)


def _payload(result: ToolMessage) -> dict[str, Any] | None:
    """Best-effort parse of a ToolMessage's payload into a dict.

    ``handler(request)`` returns a ``ToolMessage`` whose ``content`` is the
    tool's serialized output — typically a JSON string, but defensively it may
    already be a dict (or a list of content blocks). Returns the parsed dict if
    one is recoverable, else ``None`` (no detection possible).
    """
    content = result.content
    if isinstance(content, dict):
        return content
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
        except (json.JSONDecodeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None
    if isinstance(content, list):
        # Structured content blocks: find the first dict-like text payload.
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    try:
                        parsed = json.loads(text)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if isinstance(parsed, dict):
                        return parsed
        return None
    return None


def _unavailable_provider(payload: dict[str, Any] | None) -> str | None:
    """Return the provider key to mark unavailable, or None (ports agent_loop)."""
    if not isinstance(payload, dict):
        return None
    if payload.get("error_code") == "auth_required":
        provider = payload.get("provider")
        if provider:
            return str(provider)
        server = payload.get("server")
        if server:
            return provider_for_server(str(server))
    return None


def _unavailable_server(payload: dict[str, Any] | None) -> str | None:
    """Return the MCP server name to mark unavailable, or None (ports agent_loop).

    Keyed off the structured ``auth_required`` envelope's ``server`` field — the
    C5 fix: a Google tool like ``search_messages`` carries no provider substring,
    so the envelope's server is the only reliable short-circuit key.
    """
    if not isinstance(payload, dict):
        return None
    if payload.get("error_code") == "auth_required":
        server = payload.get("server")
        if server:
            return str(server)
    return None


def make_unavailable_server_middleware(
    *,
    workspace_id: str,
    db_factory: Any,
    resolve_server: Callable[[str], str | None] | None = None,
) -> AgentMiddleware:
    """Build the per-turn unavailable-server breaker middleware.

    Args:
        workspace_id: Tenant scope for registry server resolution.
        db_factory: ``async with db_factory() as db:`` provider used to resolve a
            tool's registered server via ``ToolRegistry.get_tool(name).server``.
            May be ``None`` when ``resolve_server`` is injected (tests).
        resolve_server: Optional override that maps a tool name → its MCP server
            name (or ``None``). Takes precedence over the registry; lets tests and
            callers inject resolution without a DB.

    Returns:
        An ``AgentMiddleware`` whose ``awrap_tool_call`` hook short-circuits
        tools on known-down servers and marks newly-down servers + steers.

    Per-turn state lives in these two closure-local sets — a fresh middleware
    instance always starts empty, reproducing the legacy reset-every-turn
    behaviour.
    """
    unavailable_servers: set[str] = set()
    unavailable_providers: set[str] = set()

    async def _server_for(tool_name: str) -> str | None:
        """Resolve a tool's MCP server name (injected override > registry)."""
        if resolve_server is not None:
            return resolve_server(tool_name)
        if db_factory is None:
            return None
        async with db_factory() as db:
            registry = ToolRegistry(db, workspace_id=workspace_id or None)
            tool = await registry.get_tool(tool_name)
        if tool is None:
            return None
        return getattr(tool, "server", None)

    @wrap_tool_call
    async def unavailable_server_breaker(
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage]],
    ) -> ToolMessage:
        tool_call = request.tool_call
        tool_name = tool_call["name"]
        tool_call_id = tool_call["id"]

        # deepagents built-ins (write_todos, ls, task, …) are framework scaffolding — never MCP
        # tools, and their result may be a ``Command`` (not a ToolMessage) that ``_payload`` cannot
        # inspect. Skip exactly like every sibling wrap_tool_call middleware (governor_audit,
        # trust_gate, write_lock, dispatcher) per src/deep_runtime/builtins.py.
        if tool_name in DEEPAGENTS_BUILTIN_NAMES:
            return await handler(request)

        # ── Short-circuit (pre): server known-down OR name-inferred provider down ──
        # PRIMARY KEY = registered SERVER name (C5); FALLBACK = provider inferred
        # from the tool NAME (best-effort, catches name-embedded providers when
        # the server can't be resolved).
        tool_server = await _server_for(tool_name)
        tool_provider = provider_for_server(tool_name)
        server_down = tool_server is not None and tool_server in unavailable_servers
        provider_down = tool_provider in unavailable_providers
        if server_down or provider_down:
            down_label = tool_server if server_down else tool_provider
            cached = {
                "status": "error",
                "error_code": "auth_required",
                "error": (
                    f"Integration '{down_label}' is unavailable this "
                    f"session (needs re-authorization)." + _UNAVAILABLE_STEER
                ),
            }
            return ToolMessage(
                content=json.dumps(cached),
                tool_call_id=tool_call_id,
                status="error",
            )

        # ── Execute the real tool ──
        result = await handler(request)

        # ── Detect + mark + steer (post) ──
        payload = _payload(result)
        steer_suffix = ""
        unavail_provider = _unavailable_provider(payload)
        unavail_server = _unavailable_server(payload)
        if unavail_provider is not None or unavail_server is not None:
            # Track BOTH keys: server (primary, C5) + provider (name-based fallback).
            if unavail_server is not None:
                unavailable_servers.add(unavail_server)
            if unavail_provider is not None:
                unavailable_providers.add(unavail_provider)
            steer_suffix = _UNAVAILABLE_STEER
        elif isinstance(payload, dict) and payload.get("error") == "MCP bridge not initialized":
            # Legacy shape carries no provider/server — cannot key the
            # short-circuit set, but the terminal steer still stops the retry loop.
            steer_suffix = _BRIDGE_NOT_INIT_STEER

        if not steer_suffix:
            return result

        # Append the terminal steer to the tool result content. ToolMessage is
        # immutable-ish (pydantic) — build a new instance rather than mutate.
        content = result.content
        new_content = content + steer_suffix if isinstance(content, str) else content
        return result.model_copy(update={"content": new_content})

    return unavailable_server_breaker
