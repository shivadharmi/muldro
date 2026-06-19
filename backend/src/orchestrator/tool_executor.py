"""ToolExecutor — builds tool definitions and dispatches tool calls.

Extracted from ``JarvisOrchestrator`` (god-object decomposition, 2026-06-19).
Owns the registry-driven dispatch (internal FastMCP / external MCP / composite),
the agent-scoped tool list builder, and the in-process FastMCP client. Depends on
``EventPublisher`` (for tool.started/completed/failed events) and resolves the DB
session factory live via a provider.
"""

import json
import logging

from src.models.tool_definitions import ToolBackend
from src.orchestrator.agents import SubAgent
from src.orchestrator.event_publisher import EventPublisher
from src.tools.schemas import build_tool_definitions

logger = logging.getLogger(__name__)


class ToolExecutor:
    """Tool definition building and registry-driven tool dispatch."""

    def __init__(self, events: EventPublisher, db_factory_provider):
        self._events = events
        self._db_factory_provider = db_factory_provider
        self._tools = self.build_tool_definitions()
        # Cached in-process MCP client for internal tools (lazy-init).
        self._internal_client = None
        self._internal_client_ctx = None

    @property
    def _db_factory(self):
        """Resolve the current DB session factory live via the provider."""
        return self._db_factory_provider()

    def build_tool_definitions(self) -> list[dict]:
        """Build workspace-independent tool definitions (internal + native connectors).

        MCP tools are workspace-scoped and merged at call time via
        get_tools_for_agent(workspace_id=...).
        """
        tools = self._build_internal_tool_definitions()

        # Composite web_search tool (uses Playwright MCP internally)
        tools.append(
            {
                "name": "web_search",
                "description": (
                    "Search the web using DuckDuckGo via a headless browser. "
                    "Returns structured results with titles, URLs, and snippets. "
                    "Use this when you need to find information on the web."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query string",
                        },
                        "num_results": {
                            "type": "integer",
                            "description": "Max results to return (default 10, max 20)",
                            "default": 10,
                        },
                    },
                    "required": ["query"],
                },
            }
        )

        return tools

    def _build_internal_tool_definitions(self) -> list[dict]:
        """Build Claude tool definitions from Pydantic models in tool_schemas."""
        return build_tool_definitions()

    @staticmethod
    def internal_tool_names() -> set[str]:
        """Return the set of internal (non-MCP) tool names."""
        from src.tools.schemas import TOOL_INPUT_MODELS

        return set(TOOL_INPUT_MODELS.keys())

    async def get_tools_for_agent(self, agent: SubAgent, workspace_id: str = "") -> list[dict]:
        """Build tool list from DB registry, filtered by agent capability scope.

        Internal tools come from build_tool_definitions() (Pydantic schemas).
        External tools come from ToolDefinition DB records (seeded + discovered).
        Session pool metadata enriches schema/description when DB records lack them.
        """
        from src.connectors.mcp_bridge import list_mcp_tools
        from src.services.tool_registry import ToolRegistry

        scope = agent.capability_scope
        if not scope:
            return []

        # Start with internal tools, filtered by capability
        tools: list[dict] = []
        internal_names: set[str] = set()

        async with self._db_factory() as db:
            registry = ToolRegistry(db, workspace_id=workspace_id or None)

            for t in self._tools:
                tool_def = await registry.get_tool(t["name"])
                if tool_def and tool_def.capability and tool_def.capability in scope:
                    tools.append(t)
                    internal_names.add(t["name"])

            # Build schema lookup from session pool (enriches DB records
            # that lack input_schema — e.g., external seeds before discovery)
            mcp_schemas: dict[str, dict] = {}
            for mcp_tool in list_mcp_tools(workspace_id=workspace_id):
                mcp_schemas[mcp_tool["name"]] = {
                    "description": mcp_tool.get("description", ""),
                    "input_schema": mcp_tool.get("input_schema", {}),
                }

            # Add external tools from DB registry, filtered by capability
            all_db_tools = await registry.list_tools(enabled_only=True)

            # Lazy "discover-once": if any in-scope external tool lacks a
            # persisted schema and has no live session schema yet, run a single
            # discovery pass for its server, then re-read the registry so the
            # freshly persisted schemas are visible this same build.
            in_scope_missing = [
                td
                for td in all_db_tools
                if td.name not in internal_names
                and td.capability
                and td.capability in scope
                and not td.input_schema
                and td.name not in mcp_schemas
            ]
            if in_scope_missing:
                from src.integrations.lazy_discovery import discover_missing_schemas

                discovered = await discover_missing_schemas(
                    in_scope_missing, workspace_id=workspace_id
                )
                if discovered:
                    all_db_tools = await registry.list_tools(enabled_only=True)
                    for mcp_tool in list_mcp_tools(workspace_id=workspace_id):
                        mcp_schemas[mcp_tool["name"]] = {
                            "description": mcp_tool.get("description", ""),
                            "input_schema": mcp_tool.get("input_schema", {}),
                        }

            for tool_def in all_db_tools:
                if tool_def.name in internal_names:
                    continue
                if not tool_def.capability or tool_def.capability not in scope:
                    continue

                # Live MCP schemas take priority for external tools — the
                # MCP server is the source of truth (e.g., OAuth 2.1 mode
                # strips user_google_email from schemas at runtime).
                # Fallback to DB schema. Skip tools with no schema from any
                # real source — presenting tools with empty schemas causes
                # agents to call them without required params.
                schema = None
                description = tool_def.description or tool_def.name

                if tool_def.name in mcp_schemas:
                    schema = mcp_schemas[tool_def.name].get("input_schema")
                    live_desc = mcp_schemas[tool_def.name].get("description")
                    if live_desc:
                        description = live_desc

                if not schema:
                    schema = tool_def.input_schema

                if not schema:
                    logger.debug(
                        "Skipping tool %s — no schema from MCP or DB yet",
                        tool_def.name,
                    )
                    continue

                tools.append(
                    {
                        "name": tool_def.name,
                        "description": description,
                        "input_schema": schema,
                    }
                )

        return tools

    def apply_cache_control_to_tools(self, tools: list[dict]) -> list[dict]:
        """Mark the last tool definition with cache_control for tool caching."""
        if not tools:
            return tools
        tools = [dict(t) for t in tools]
        tools[-1] = {**tools[-1], "cache_control": {"type": "ephemeral"}}
        return tools

    async def call_composite_tool(
        self, tool_name: str, tool_input: dict, user_id: str = "", workspace_id: str = ""
    ) -> dict:
        """Dispatch composite tools (multi-MCP orchestration)."""
        if tool_name == "web_search":
            from src.browser.web_search import web_search

            return await web_search(
                query=tool_input.get("query", ""),
                num_results=tool_input.get("num_results", 10),
                user_id=user_id,
                workspace_id=workspace_id,
            )
        return {"error": f"Unknown composite tool: {tool_name}"}

    async def call_internal_tool(
        self, tool_name: str, tool_input: dict, server_prefix: str
    ) -> dict:
        """Call an internal tool via in-process FastMCP Client (MCP protocol).

        The composed server mounts tools under namespaced prefixes:
        - intelligence tools: "intelligence_" prefix
        - communication tools: "communication_" prefix
        We map flat tool names (e.g. "search", "push_ui_update") to namespaced names
        (e.g. "intelligence_search", "communication_push_ui_update").
        """
        from fastmcp import Client

        from src.tools.server import jarvis_tools

        # Lazy-init: create and cache the in-process client
        if self._internal_client is None:
            self._internal_client_ctx = Client(jarvis_tools)
            self._internal_client = await self._internal_client_ctx.__aenter__()

        # Map flat name to namespaced name (server-specific prefix)
        namespaced = f"{server_prefix}_{tool_name}"
        logger.info("[mcp:internal] calling %s (ns: %s)", tool_name, namespaced)
        result = await self._internal_client.call_tool(namespaced, tool_input)

        # Extract result from CallToolResult
        if result.is_error:
            error_text = result.data if hasattr(result, "data") else str(result)
            logger.warning("[mcp:internal] %s ERROR: %s", tool_name, str(error_text)[:200])
            return {"status": "error", "error": error_text}
        logger.info("[mcp:internal] %s OK", tool_name)

        # Parse structured content if available
        if hasattr(result, "structured_content") and result.structured_content:
            return result.structured_content.get("result", result.structured_content)

        # Fallback: parse text content as JSON
        text = result.data if hasattr(result, "data") else str(result)
        if isinstance(text, str):
            try:
                return json.loads(text)
            except (json.JSONDecodeError, TypeError):
                return {"status": "ok", "result": text}
        return {"status": "ok", "result": text}

    async def execute_tool(
        self, tool_name: str, tool_input: dict, user_id: str, workspace_id: str = ""
    ) -> dict:
        """Registry-driven dispatch: one lookup, one match on backend."""
        from src.services.tool_registry import ToolRegistry

        async with self._db_factory() as db:
            registry = ToolRegistry(db, workspace_id=workspace_id or None)
            tool = await registry.get_tool(tool_name)

        if not tool:
            logger.warning("[mcp] tool not found in registry: %s", tool_name)
            return {"error": f"Unknown tool: {tool_name}"}
        if not tool.enabled:
            logger.warning("[mcp] tool disabled: %s", tool_name)
            return {"error": f"Tool '{tool_name}' is disabled", "blocked": True}

        # Resolve the stored backend string to the typed dispatch discriminator.
        # An unrecognized value (e.g. a future or garbled backend) coerces to None
        # and falls through to the match's default arm rather than raising.
        try:
            backend = ToolBackend(tool.backend)
        except ValueError:
            backend = None

        # "special" backend (report_governor_verdict) is inline-dispatched: input is
        # passed through as-is with no MCP call and, by design, no tool.started/completed
        # events — it carries the governor's structured verdict, not a side-effecting call.
        if backend is ToolBackend.SPECIAL:
            return tool_input

        logger.info(
            "[mcp] dispatch %s via %s/%s",
            tool_name,
            tool.backend,
            tool.server or "default",
        )
        await self._events.publish_event(
            "tool.started", user_id, {"tool": tool_name}, workspace_id=workspace_id
        )

        try:
            match backend:
                case ToolBackend.INTERNAL_MCP:
                    # Intelligence server tools are workspace-scoped and need
                    # user_id/workspace_id for DB queries. Communication server
                    # tools are stateless delivery tools — injecting these fields
                    # causes Pydantic validation errors on their strict schemas.
                    if tool.server == "intelligence":
                        if workspace_id and "workspace_id" not in tool_input:
                            tool_input = {**tool_input, "workspace_id": workspace_id}
                        enriched_input = {**tool_input, "user_id": user_id}
                    else:
                        enriched_input = tool_input
                    result = await self.call_internal_tool(
                        tool_name,
                        enriched_input,
                        server_prefix=tool.server,
                    )
                case ToolBackend.EXTERNAL_MCP:
                    # External MCP servers do not accept workspace_id in tool input —
                    # it is passed as a keyword arg for session routing only.
                    from src.connectors.mcp_bridge import call_mcp_tool

                    result = await call_mcp_tool(
                        tool_name,
                        tool_input,
                        user_id=user_id,
                        workspace_id=workspace_id,
                    )
                case ToolBackend.COMPOSITE:
                    # Composite tools are Jarvis-internal, receive workspace_id
                    if workspace_id and "workspace_id" not in tool_input:
                        tool_input = {**tool_input, "workspace_id": workspace_id}
                    result = await self.call_composite_tool(
                        tool_name,
                        tool_input,
                        user_id=user_id,
                        workspace_id=workspace_id,
                    )
                case _:
                    result = {"error": f"Unknown backend '{tool.backend}' for tool '{tool_name}'"}

            await self._events.publish_event(
                "tool.completed", user_id, {"tool": tool_name}, workspace_id=workspace_id
            )
            return result
        except Exception as e:
            logger.warning("[mcp] %s FAILED: %s", tool_name, e)
            await self._events.publish_event(
                "tool.failed",
                user_id,
                {"tool": tool_name, "error": str(e)[:200]},
                workspace_id=workspace_id,
            )
            # Tool-result error is persisted to message metadata + streamed to the
            # browser — keep it generic. Full detail is logged above and in the
            # (secret-redacted) trace; the agent still learns the tool failed.
            return {"error": f"Tool '{tool_name}' failed.", "error_code": "tool_error"}
