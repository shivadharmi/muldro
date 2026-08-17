"""Gmail actions served through the OpenConnector gateway.

Action ids, parameter names, and input schemas are transcribed verbatim from a
live OpenConnector v1.3.5 catalog -- see infra/gateway/spike-findings-multiprovider.md.
OC's runtime ``get_action_guide`` exposes no machine-readable schema, so these
are declared here; ``tests/gateway_ground_truth.py`` asserts they still equal
what the catalog serves, and warm_start's live drift check warns when OC's
parameter names diverge from what is declared.
"""

from __future__ import annotations

from src.integrations.gateway_actions._types import GatewayAction, GatewayProvider

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
                }
            },
            "additionalProperties": False,
            "description": "The input payload for this action.",
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
                    "items": {"type": "string", "minLength": 1},
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
                    "default": "summary",
                },
                "maxResults": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 500,
                    "description": "Maximum number of results to return.",
                },
                "pageToken": {
                    "type": "string",
                    "description": "Opaque pagination token returned by Gmail.",
                },
            },
            "additionalProperties": False,
            "description": "The input payload for this action.",
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
                    "minimum": 1,
                    "maximum": 500,
                    "description": "Maximum number of results to return.",
                },
            },
            "additionalProperties": False,
            "required": ["query"],
            "description": "The input payload for this action.",
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
                "messageId": {"type": "string", "minLength": 1, "description": "Gmail message ID."}
            },
            "additionalProperties": False,
            "required": ["messageId"],
            "description": "The input payload for this action.",
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
                    "minimum": 1,
                    "maximum": 500,
                    "description": "Maximum number of results to return.",
                },
                "pageToken": {
                    "type": "string",
                    "description": "Opaque pagination token returned by Gmail.",
                },
            },
            "additionalProperties": False,
            "description": "The input payload for this action.",
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
                }
            },
            "additionalProperties": False,
            "description": "The input payload for this action.",
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
            "additionalProperties": False,
            "description": "The input payload for this action.",
        },
    ),
)

GMAIL = GatewayProvider(
    provider_id="gmail",
    server_name="google-workspace",
    actions=GMAIL_ACTIONS,
)
