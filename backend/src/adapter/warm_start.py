"""Warm-start the gateway adapter's named-action tool surface.

Registers one FastMCP tool per allowlisted OpenConnector action. Each tool
advertises a HAND-TYPED JSON Schema (transcribed from OpenConnector v1.3.5's
``get_action_guide`` "## Input Parameters" tables — OC exposes no
machine-readable per-action schema; see ``infra/gateway/spike-findings-guide.md``).
Each tool's handler forwards to the four-step-enforced ``handle_execute_action``.

Hybrid drift check: at warm-start we still call ``get_action_guide`` live and
compare OpenConnector's *current* parameter names to our hand-typed schema,
logging a warning when they diverge — a maintenance signal to update the schema.
The live call NEVER changes the served schema, so a guide-fetch failure or an
unparseable guide only skips the check; the tool still ships its hand-typed
schema (or an opaque schema if none is defined for the action).
"""

from __future__ import annotations

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
from src.models.database import get_session_factory

logger = logging.getLogger(__name__)

_OPAQUE_SCHEMA = {"type": "object", "additionalProperties": True}

GuideFetcher = Callable[[str], Awaitable[dict]]

# Hand-typed JSON Schemas transcribed verbatim from OpenConnector v1.3.5's
# get_action_guide "## Input Parameters" tables (infra/gateway/spike-findings-guide.md).
# OC emits no machine-readable schema, so these are the source of truth for what the
# agent sees. Keep the keys in sync with GMAIL_ACTION_ALLOWLIST in enforcement.py
# (a test enforces every allowlisted action has an entry here).
GMAIL_ACTION_SCHEMAS: dict[str, dict] = {
    "gmail.get_profile": {
        "type": "object",
        "properties": {
            "userId": {
                "type": "string",
                "description": "Gmail user ID. Omit to use the connected mailbox.",
            },
        },
    },
    "gmail.fetch_emails": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Gmail search query."},
            "labelIds": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Gmail label IDs.",
            },
            "includeSpamTrash": {
                "type": "boolean",
                "description": "Whether to include Spam and Trash.",
            },
            "detail": {
                "type": "string",
                "enum": ["ids", "summary", "full"],
                "description": "Message detail level.",
            },
            "maxResults": {
                "type": "integer",
                "description": "Maximum number of results to return.",
            },
            "pageToken": {
                "type": "string",
                "description": "Opaque pagination token returned by Gmail.",
            },
        },
    },
    "gmail.search_threads": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Gmail search query."},
            "maxResults": {
                "type": "integer",
                "description": "Maximum number of results to return.",
            },
        },
        "required": ["query"],
    },
    "gmail.get_message": {
        "type": "object",
        "properties": {
            "messageId": {"type": "string", "description": "Gmail message ID."},
        },
        "required": ["messageId"],
    },
    "gmail.list_threads": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Gmail search query."},
            "verbose": {"type": "boolean", "description": "Hydrate each thread."},
            "maxResults": {
                "type": "integer",
                "description": "Maximum number of results to return.",
            },
            "pageToken": {
                "type": "string",
                "description": "Opaque pagination token returned by Gmail.",
            },
        },
    },
    "gmail.list_labels": {
        "type": "object",
        "properties": {
            "userId": {
                "type": "string",
                "description": "Gmail user ID. Omit to use the connected mailbox.",
            },
        },
    },
    "gmail.send_email": {
        "type": "object",
        "properties": {
            "recipientEmail": {"type": "string", "description": "Primary recipient email address."},
            "to": {"type": "string", "description": "Primary recipient email address."},
            "extraRecipients": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Additional To recipients.",
            },
            "cc": {
                "anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}],
                "description": "Cc recipients.",
            },
            "bcc": {
                "anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}],
                "description": "Bcc recipients.",
            },
            "subject": {"type": "string", "description": "Email subject line."},
            "body": {"type": "string", "description": "Email body content."},
            "messageBody": {"type": "string", "description": "Reply or draft body content."},
            "isHtml": {"type": "boolean", "description": "Whether the body is HTML."},
            "fromEmail": {"type": "string", "description": "Verified Gmail send-as alias."},
        },
    },
}


def _describe(action_id: str) -> str:
    """Human-readable tool description for the agent's tool list."""
    verb = action_id.split(".", 1)[-1].replace("_", " ")
    return f"Gmail: {verb} (via the OpenConnector gateway)."


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
    """Register one named FastMCP tool per allowlisted action. Returns the count.

    Serves the hand-typed schema (opaque fallback if none is defined). Runs a
    best-effort live drift check via ``guide_fetcher`` that never affects the
    served schema.
    """
    count = 0
    for action_id in sorted(profile.action_allowlist):
        schema = GMAIL_ACTION_SCHEMAS.get(action_id)
        if schema is None:
            logger.warning("warm-start: no hand-typed schema for %s — serving opaque", action_id)
            schema = dict(_OPAQUE_SCHEMA)
        else:
            try:
                guide = await guide_fetcher(action_id)
                live = _param_names_from_guide(guide)
                declared = set(schema.get("properties", {}))
                if live and live != declared:
                    logger.warning(
                        "warm-start: %s parameter drift — OpenConnector=%s hand-typed=%s",
                        action_id,
                        sorted(live),
                        sorted(declared),
                    )
            except Exception:
                logger.warning(
                    "warm-start: drift check skipped for %s (guide fetch failed)", action_id
                )
            # Serve a deep copy so FastMCP can never mutate GMAIL_ACTION_SCHEMAS,
            # the module-level source of truth, through the served tool's schema.
            schema = copy.deepcopy(schema)
        adapter.add_tool(
            FunctionTool(
                name=action_id,
                description=_describe(action_id),
                parameters=schema,
                fn=_make_handler(action_id),
            )
        )
        count += 1
    return count
