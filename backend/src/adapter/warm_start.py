"""Warm-start the gateway adapter's named-action tool surface.

Registers one FastMCP tool per action in the ``GatewayProfile`` it is handed,
named with the agent-legal (underscore) form of the actionId (``gmail.get_profile``
-> ``gmail_get_profile`` via ``gateway_naming.action_id_to_tool_name``) — Anthropic
and OpenAI-compatible tool-calling APIs forbid dots in tool names. Each tool's
schema comes straight from that action's ``input_schema`` (hand-typed, transcribed
from a live OpenConnector admin capture — OC exposes no machine-readable
per-action schema at runtime; see ``infra/gateway/spike-findings-guide.md``), and
its handler stays bound to the DOTTED actionId, forwarding to the
four-step-enforced ``handle_execute_action``. So the LLM calls ``gmail_get_profile``
and the adapter calls OpenConnector with ``gmail.get_profile``.

Hybrid drift check: at warm-start we still call ``get_action_guide`` live and
compare OpenConnector's *current* parameter names to our hand-typed schema,
logging a warning when they diverge — a maintenance signal to update the schema.
The live call NEVER changes the served schema, so a guide-fetch failure or an
unparseable guide only skips the check; the tool still ships its hand-typed schema.
Because the check is advisory, the guide fetches run CONCURRENTLY and outside the
registration loop — ``openconnector_client`` builds a fresh Client + handshake per
call, so serial fetches would pay one connect/initialize/teardown per action (and,
with OpenConnector unreachable, one connection timeout per action) before
``adapter.run()`` is ever reached: a hang rather than a loud failure.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import re
from collections.abc import Awaitable, Callable

from fastmcp import FastMCP
from fastmcp.tools import FunctionTool

from src.adapter.enforcement import GatewayProfile
from src.adapter.http_context import bearer_token
from src.adapter.openconnector_client import get_action_guide
from src.adapter.server import handle_execute_action
from src.integrations.gateway_naming import action_id_to_tool_name
from src.models.database import get_session_factory

logger = logging.getLogger(__name__)

GuideFetcher = Callable[[str], Awaitable[dict]]


def _describe(display_name: str, action_id: str) -> str:
    """Human-readable tool description for the agent's tool list.

    ``display_name`` comes from the provider registry (``GatewayProvider``), never
    from a label table maintained here — a new provider must not silently degrade
    to its raw provider_id in the text the LLM reads to pick a tool.
    """
    verb = action_id.split(".", 1)[-1].replace("_", " ")
    return f"{display_name}: {verb} (via the OpenConnector gateway)."


def _guide_markdown(guide: object) -> str:
    """Best-effort: locate the guide's markdown body across known response shapes.

    OpenConnector returns the parameter table as markdown under ``data.markdown``.
    fastmcp's Client may surface the guide as a dict or a result object; this
    tries the documented paths and returns "" if none match (the drift check
    then no-ops — it is non-fatal by design).
    """
    if isinstance(guide, dict):
        for path in (
            ("data", "markdown"),
            ("markdown",),
            ("structuredContent", "data", "markdown"),
        ):
            node: object = guide
            for key in path:
                node = node.get(key) if isinstance(node, dict) else None
            if isinstance(node, str):
                return node
        return ""
    for attr in ("structured_content", "data"):
        val = getattr(guide, attr, None)
        if isinstance(val, dict):
            md = _guide_markdown(val)
            if md:
                return md
    return ""


def _param_names_from_guide(guide: object) -> set[str]:
    """Extract declared input-parameter NAMES from a guide's markdown table.

    Names only (not types) — enough to detect when OpenConnector adds or removes
    a parameter vs our hand-typed schema. Robust to the prose type cells that
    make full schema parsing brittle. Returns an empty set when no table is found.
    """
    markdown = _guide_markdown(guide)
    if not markdown:
        return set()
    names: set[str] = set()
    in_table = False
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("## Input Parameters"):
            in_table = True
            continue
        if in_table:
            if stripped.startswith("## "):  # next section ends the table
                break
            match = re.match(r"\|\s*`([^`]+)`\s*\|", stripped)
            if match:
                names.add(match.group(1))
    return names


def _make_handler(action_id: str) -> Callable[..., Awaitable[dict]]:
    """Build the named tool's handler: fixed actionId, caller-supplied input."""

    async def _handler(**kwargs: object) -> dict:
        token = bearer_token()
        args = {"actionId": action_id, "input": dict(kwargs)}
        async with get_session_factory()() as db:
            return await handle_execute_action(db, token=token, args=args)

    return _handler


async def register_gateway_tools(
    adapter: FastMCP,
    profile: GatewayProfile,
    *,
    guide_fetcher: GuideFetcher = get_action_guide,
) -> int:
    """Register one named FastMCP tool per action in ``profile``. Returns the count.

    Every action in a ``GatewayProfile`` carries its own hand-typed
    ``input_schema`` by construction, so no fallback is needed. Runs a
    best-effort live drift check via ``guide_fetcher`` that never affects the
    served schema; the fetches are gathered CONCURRENTLY before the loop, and a
    failed fetch only skips that action's check — registration still happens for
    every action.
    """
    actions = sorted(profile.actions, key=lambda a: a.action_id)
    guides = await asyncio.gather(
        *(guide_fetcher(a.action_id) for a in actions), return_exceptions=True
    )
    count = 0
    for action, guide in zip(actions, guides, strict=True):
        schema = copy.deepcopy(action.input_schema)
        try:
            if isinstance(guide, BaseException):
                raise guide
            live = _param_names_from_guide(guide)
            declared = set(schema.get("properties", {}))
            if live and live != declared:
                logger.warning(
                    "warm-start: %s parameter drift - OpenConnector=%s hand-typed=%s",
                    action.action_id,
                    sorted(live),
                    sorted(declared),
                )
        except Exception:
            logger.warning(
                "warm-start: drift check skipped for %s (guide fetch failed)", action.action_id
            )
        adapter.add_tool(
            FunctionTool(
                # Agent-legal name (dots -> underscores): Anthropic/OpenAI tool
                # names forbid dots. The handler below stays bound to the
                # DOTTED actionId, so the LLM calls e.g. gmail_get_profile and
                # the adapter forwards gmail.get_profile to OpenConnector.
                name=action_id_to_tool_name(action.action_id),
                description=_describe(profile.display_name, action.action_id),
                parameters=schema,
                fn=_make_handler(action.action_id),
            )
        )
        count += 1
    return count
