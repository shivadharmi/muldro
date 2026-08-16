"""Single source of truth for the gateway's OpenConnector actions.

Each GatewayAction is the one place an action's policy (capability, risk,
approval) and its hand-typed input schema (OpenConnector exposes no
machine-readable schema — see infra/gateway/spike-findings-guide.md) are
declared. enforcement.py, warm_start.py, and catalog.py all DERIVE from this
table (allowlist, capability map, tool schemas, catalog seeds), so the three
never drift. This is the north-star verb->capability+risk policy table.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GatewayAction:
    action_id: str  # OC-native, dotted (sent to OpenConnector)
    capability: str  # Jarvis capability (email.read/search/list/send)
    risk: str
    requires_approval: bool
    input_schema: dict  # hand-typed; OC exposes no machine-readable schema


GMAIL_ACTIONS: tuple[GatewayAction, ...] = (
    GatewayAction(
        "gmail.get_profile",
        "email.read",
        "low",
        False,
        {
            "type": "object",
            "properties": {
                "userId": {
                    "type": "string",
                    "description": "Gmail user ID. Omit to use the connected mailbox.",
                },
            },
        },
    ),
    GatewayAction(
        "gmail.fetch_emails",
        "email.search",
        "low",
        False,
        {
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
    ),
    GatewayAction(
        "gmail.search_threads",
        "email.search",
        "low",
        False,
        {
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
    ),
    GatewayAction(
        "gmail.get_message",
        "email.read",
        "low",
        False,
        {
            "type": "object",
            "properties": {
                "messageId": {"type": "string", "description": "Gmail message ID."},
            },
            "required": ["messageId"],
        },
    ),
    GatewayAction(
        "gmail.list_threads",
        "email.list",
        "low",
        False,
        {
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
    ),
    GatewayAction(
        "gmail.list_labels",
        "email.list",
        "low",
        False,
        {
            "type": "object",
            "properties": {
                "userId": {
                    "type": "string",
                    "description": "Gmail user ID. Omit to use the connected mailbox.",
                },
            },
        },
    ),
    GatewayAction(
        "gmail.send_email",
        "email.send",
        "high",
        True,
        {
            "type": "object",
            "properties": {
                "recipientEmail": {
                    "type": "string",
                    "description": "Primary recipient email address.",
                },
                "to": {"type": "string", "description": "Primary recipient email address."},
                "extraRecipients": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Additional To recipients.",
                },
                "cc": {
                    "anyOf": [
                        {"type": "string"},
                        {"type": "array", "items": {"type": "string"}},
                    ],
                    "description": "Cc recipients.",
                },
                "bcc": {
                    "anyOf": [
                        {"type": "string"},
                        {"type": "array", "items": {"type": "string"}},
                    ],
                    "description": "Bcc recipients.",
                },
                "subject": {"type": "string", "description": "Email subject line."},
                "body": {"type": "string", "description": "Email body content."},
                "messageBody": {"type": "string", "description": "Reply or draft body content."},
                "isHtml": {"type": "boolean", "description": "Whether the body is HTML."},
                "fromEmail": {"type": "string", "description": "Verified Gmail send-as alias."},
            },
        },
    ),
)
