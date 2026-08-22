"""ToolExecutor — builds tool definitions and dispatches tool calls.

Extracted from ``MuldroOrchestrator`` (god-object decomposition, 2026-06-19).
Owns the registry-driven dispatch (internal FastMCP / external MCP / composite),
the agent-scoped tool list builder, and the in-process FastMCP client. Depends on
``EventPublisher`` (for tool.started/completed/failed events) and resolves the DB
session factory live via a provider.
"""

import inspect
import json
import logging
from functools import lru_cache

from pydantic import ValidationError

from src.models.tool_definitions import ToolBackend
from src.orchestrator.agents import SubAgent
from src.orchestrator.event_publisher import EventPublisher
from src.tools.schemas import build_tool_definitions

logger = logging.getLogger(__name__)

# Contextual args the dispatcher may inject into internal MCP tools. These are
# supplied by Muldro (from auth/turn context), never invented by the LLM, so the
# LLM-facing schemas in schemas.py deliberately omit them.
_CONTEXT_ARGS = ("user_id", "workspace_id")

# Pydantic v2 errors are verbose: a malformed nested payload yields one entry per bad
# node, and a tagged-union field adds a long `msg` naming every tag it would accept.
# Render only the first few and say how many were dropped — the agent needs the first
# offending field, not the census. (A `type` discriminator keeps an unknown tag to ONE
# error rather than one per union member; the cap is for breadth of errors, not for
# union fan-out.)
#
# The per-error cap is sized off a MEASUREMENT, because the longest message is also the
# most actionable one: the worst case measured was a single error whose `msg` ran 260
# chars enumerating the seventeen tags its union accepted. At the old 100-char cap the
# agent was told three of them and then cut mid-word, destroying the one thing that lets
# it repair the call. 280 = 260 measured + headroom.
# The overall cap survives that intact: envelope (42) + loc (~12) + 260 + trailer (61) =
# 375 for a single tag-list error, so 900 fits it whole with room for two neighbours,
# while still bounding a broadly-malformed payload to ~225 tokens.
_MAX_ARG_ERRORS_SHOWN = 3
_MAX_ARG_ERROR_MSG_CHARS = 280
_MAX_ARG_ERROR_CHARS = 900

# Pydantic's string-length errors state the LIMIT but not the SIZE of what was sent, so
# a model repairing one has to guess how much to cut. Measured live on 2026-08-20
# (gpt-5-mini, a 120-char `subtitle` bound): 123 -> 141 -> 128 -> 109 chars. Attempt two
# was WORSE than attempt one and the surface was lost one retry short of valid. Told
# "at most 120 characters (got 141)" the cut is arithmetic instead of a guess.
#
# The set is an ALLOWLIST chosen by inspecting real `exc.errors()` output, because a
# size is only signal where the message does not already carry it:
#   * `too_long` / `too_short` (list, dict, set) ALREADY say the count in their own
#     `msg` — "List should have at most 4 items after validation, not 5" — so annotating
#     them would state one number twice and read as two facts;
#   * `missing` has no value to measure;
#   * `union_tag_invalid` / `literal_error` already enumerate the admissible values, and
#     the unknown-component-tag message is the most actionable string in the system —
#     adding to it would only crowd it against the caps below;
#   * every `*_type` error is about the KIND of the value, which its size cannot fix;
#   * `bytes_too_long` / `bytes_too_short` are unreachable — a JSON tool call cannot
#     carry bytes, and no input model declares a bytes field.
#
# The caps above are unaffected and deliberately NOT retuned: the annotation is ~11
# chars and can only ever attach to a string-length `msg` (~45 chars), never to the
# 260-char tag list, so the "375 for a single tag-list error" arithmetic still holds.
_SIZED_ERROR_TYPES = frozenset({"string_too_long", "string_too_short"})


def _size_hint(err: dict) -> str:
    """Return e.g. ``" (got 141)"`` for a length-bound error, or ``""``.

    SECURITY: this deliberately renders only the SIZE of the offending value and NEVER
    the value itself. The payloads reaching this function are user content (email
    bodies, meeting notes, contact details) and the string it feeds is both returned
    into the model's context and written to ``logger.warning`` — echoing the value
    would put user content into the logs.
    """
    if err.get("type") not in _SIZED_ERROR_TYPES:
        return ""
    try:
        # `input` may be ANY object: one with no `__len__`, or one whose `__len__`
        # raises. A raise HERE escapes `_validate_tool_input`'s `except ValidationError`
        # block and kills the turn, so an unmeasurable value simply gets no annotation.
        return f" (got {len(err['input'])})"
    except Exception:
        return ""


def _render_validation_error(tool_name: str, exc: ValidationError) -> str:
    """Render a ValidationError as a short, actionable, agent-facing message.

    Follows the house style of the missing-required-args return below: say what is
    wrong AND what to do. This string is returned to the model as the tool result, so
    it is the agent's only chance to repair the call — name the field, keep the reason.
    """
    errors = exc.errors()
    parts = []
    for err in errors[:_MAX_ARG_ERRORS_SHOWN]:
        loc = ".".join(str(p) for p in err.get("loc", ())) or "(root)"
        msg = str(err.get("msg", "invalid"))[:_MAX_ARG_ERROR_MSG_CHARS]
        parts.append(f"{loc}: {msg}{_size_hint(err)}")
    rendered = "; ".join(parts)
    dropped = len(errors) - _MAX_ARG_ERRORS_SHOWN
    if dropped > 0:
        rendered += f" (+{dropped} more)"
    message = (
        f"Invalid argument(s) for '{tool_name}': {rendered}. "
        "Fix them against the tool's schema and call the tool again."
    )
    return message[:_MAX_ARG_ERROR_CHARS]


def _validate_tool_input(tool_name: str, tool_input: dict) -> str | None:
    """Parse agent-supplied tool args against the tool's Pydantic input model.

    Returns a rendered error string, or None when there is nothing to report.

    Keyed on the MODEL's existence, not on the tool's backend. That covers internal
    MCP tools plus the ``_special`` passthrough, and naturally skips external MCP and
    composite tools (which have no model) with no backend branching — and it cannot
    drift from the registry, because ``validate_registry()`` already fails startup if
    an internal tool is missing from TOOL_INPUT_MODELS.
    """
    from src.tools.schemas import TOOL_INPUT_MODELS

    model = TOOL_INPUT_MODELS.get(tool_name)
    if model is None:
        # No model → nothing to validate. External MCP tools are covered instead by
        # _missing_required_args against their persisted JSON Schema; composite tools
        # (web_search) are Muldro-internal and have neither.
        return None
    try:
        model.model_validate(tool_input)
    except ValidationError as exc:
        return _render_validation_error(tool_name, exc)
    except Exception:
        # Fail OPEN — and this matters MORE now that the parse rejects, not less. A
        # field_validator that raises something pydantic does not wrap (only
        # ValueError/AssertionError become ValidationError) would escape execute_tool
        # above its own try/except and kill the whole turn; failing open at worst lets
        # through a call that used to work, while failing closed would turn one broken
        # validator into a permanently blocked tool.
        logger.exception("[toolargs] %s validation raised; treating as valid", tool_name)
        return None
    return None


@lru_cache(maxsize=1)
def _internal_tool_context_args() -> dict[str, frozenset[str]]:
    """Map each internal tool name → the contextual args its impl actually accepts.

    Built once by introspecting the FastMCP impl functions in the internal servers.
    Injection is signature-aware: a tool only receives a contextual arg (user_id /
    workspace_id) if its implementation declares it. A tool that takes only one of
    the two therefore works without re-breaking validation, and no server needs a
    special case.
    """
    from src.tools import intelligence_server

    mapping: dict[str, frozenset[str]] = {}
    for module in (intelligence_server,):
        for name, obj in vars(module).items():
            if not inspect.iscoroutinefunction(obj):
                continue
            params = inspect.signature(obj).parameters
            accepted = frozenset(arg for arg in _CONTEXT_ARGS if arg in params)
            if accepted:
                mapping[name] = accepted
    return mapping


def _enrich_internal_input(tool_name: str, tool_input: dict, user_id: str, workspace_id: str):
    """Inject user_id/workspace_id into an internal tool's input, signature-aware.

    Only injects a contextual arg if (a) the tool's impl declares that parameter
    and (b) it is not already present in tool_input. Returns a new dict (no mutation).
    """
    accepted = _internal_tool_context_args().get(tool_name, frozenset())
    enriched = dict(tool_input)
    if "user_id" in accepted and "user_id" not in enriched:
        enriched["user_id"] = user_id
    if "workspace_id" in accepted and workspace_id and "workspace_id" not in enriched:
        enriched["workspace_id"] = workspace_id
    return enriched


def _missing_required_args(input_schema, tool_input: dict) -> list[str]:
    """Return the JSON-Schema ``required`` fields absent from ``tool_input``.

    Fail-open by design: a non-dict schema, a missing/malformed ``required`` list, or
    a ``required`` entry the schema doesn't actually describe in ``properties`` yields
    no violations — we only enforce fields the authoritative schema both requires and
    defines, so a degraded schema never blocks an otherwise-valid call.
    """
    if not isinstance(input_schema, dict):
        return []
    required = input_schema.get("required")
    if not isinstance(required, list):
        return []
    props = input_schema.get("properties")
    props = props if isinstance(props, dict) else {}
    return [f for f in required if isinstance(f, str) and f in props and f not in tool_input]


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

        # Composite web_search tool (one HTTPS GET to DuckDuckGo's HTML endpoint)
        tools.append(
            {
                "name": "web_search",
                "description": (
                    "Search the web using DuckDuckGo. "
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
            # that lack input_schema — e.g., external seeds before discovery).
            # Keyed by (server, name): a tool's identity is that pair, and
            # list_mcp_tools legitimately returns two rows with one name when
            # two servers each serve it. Keying by the bare name would keep
            # whichever discovery wrote last, while call dispatch resolves via
            # get_server_for_tool's lexicographically-first server — handing
            # the agent server Z's schema for a call that routes to server A.
            # The DB's tool_def.server and the pool's server_name are the same
            # namespace (mcp_bridge._resolve_server_from_registry feeds the
            # former straight in as the latter), so the pair matches up.
            mcp_schemas: dict[tuple[str, str], dict] = {}
            for mcp_tool in list_mcp_tools(workspace_id=workspace_id):
                mcp_schemas[(mcp_tool.get("server", ""), mcp_tool["name"])] = {
                    "description": mcp_tool.get("description", ""),
                    "input_schema": mcp_tool.get("input_schema", {}),
                }

            # Add external tools from DB registry, filtered by capability. workspace_scoped=True:
            # this builds the agent's ACTUAL callable tool set, so a workspace-specific
            # ToolDefinition from another tenant must never leak in — bound to this workspace +
            # the global catalog (mirrors the get_tool scoping used for internal tools above).
            all_db_tools = await registry.list_tools(enabled_only=True, workspace_scoped=True)

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
                and (td.server or "", td.name) not in mcp_schemas
            ]
            if in_scope_missing:
                from src.integrations.lazy_discovery import discover_missing_schemas

                discovered = await discover_missing_schemas(
                    in_scope_missing, workspace_id=workspace_id
                )
                if discovered:
                    all_db_tools = await registry.list_tools(
                        enabled_only=True, workspace_scoped=True
                    )
                    for mcp_tool in list_mcp_tools(workspace_id=workspace_id):
                        mcp_schemas[(mcp_tool.get("server", ""), mcp_tool["name"])] = {
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

                live = mcp_schemas.get((tool_def.server or "", tool_def.name))
                if live:
                    schema = live.get("input_schema")
                    live_desc = live.get("description")
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

    async def call_composite_tool(
        self, tool_name: str, tool_input: dict, user_id: str = "", workspace_id: str = ""
    ) -> dict:
        """Dispatch composite tools (multi-MCP orchestration)."""
        if tool_name == "web_search":
            from src.services.web_search import web_search

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

        The composed server mounts tools under namespaced prefixes — intelligence
        tools carry the "intelligence_" prefix. We map flat tool names (e.g. "search")
        to namespaced names (e.g. "intelligence_search").
        """
        from fastmcp import Client

        from src.tools.server import muldro_tools

        # Lazy-init: create and cache the in-process client
        if self._internal_client is None:
            self._internal_client_ctx = Client(muldro_tools)
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

        # Typed-argument parse against TOOL_INPUT_MODELS, which until now was consulted
        # only by startup registry validation and never at call time. It runs HERE — on
        # the agent-supplied input, above the SPECIAL early-return and above
        # _enrich_internal_input — because the context args (user_id / workspace_id) are
        # deliberately absent from the LLM-facing models: validating after injection
        # would flag every internal call. A failing parse REJECTS: the error goes back to
        # the model as the tool result (agents self-correct on tool errors), which is what
        # the prose constraints in prompts.py could not enforce on their own.
        arg_error = _validate_tool_input(tool_name, tool_input)
        if arg_error:
            logger.warning("[toolargs] %s rejected: %s", tool_name, arg_error)
            return {"error": arg_error, "error_code": "invalid_tool_args"}

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

        # Dispatch-time required-arg validation for external MCP tools. An agent
        # offered a tool with a degraded/propertyless schema (e.g. query_freebusy
        # during flaky google-workspace discovery) can call it with no args, which
        # then hard-fails at the server. Validate against the AUTHORITATIVE persisted
        # schema and reject BEFORE the round-trip with a message the agent can act on
        # (agents self-correct on tool errors). External-only: internal tools' context
        # args (user_id/workspace_id) are injected below, not supplied by the agent.
        if backend is ToolBackend.EXTERNAL_MCP:
            missing = _missing_required_args(tool.input_schema, tool_input)
            if missing:
                logger.warning(
                    "[mcp] %s rejected — missing required arg(s): %s",
                    tool_name,
                    ", ".join(missing),
                )
                return {
                    "error": (
                        f"Missing required argument(s) for '{tool_name}': "
                        f"{', '.join(missing)}. Supply them and call the tool again."
                    ),
                    "error_code": "missing_required_args",
                }

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
                    # Inject contextual args (user_id / workspace_id) signature-aware:
                    # each internal tool receives only the contextual args its impl
                    # actually declares, so a tool taking only one of the two is not
                    # handed the other. The LLM-facing schemas omit these fields, so
                    # the dispatcher supplies them here.
                    enriched_input = _enrich_internal_input(
                        tool_name, tool_input, user_id, workspace_id
                    )
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
                    # Composite tools are Muldro-internal, receive workspace_id
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
