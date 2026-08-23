"""Jira actions served through the OpenConnector gateway.

Action ids, parameter names, and input schemas are transcribed verbatim from a
live OpenConnector v1.3.5 catalog (`GET /api/actions`, ``service == "jira"`` —
7 actions).

**Jira only. Confluence CANNOT be gateway-backed, and this was verified live
rather than assumed.** OpenConnector models Atlassian as two independent
services, and their credentials differ:

    jira.execution.requiredAuthTypes       == ["oauth2", "custom_credential"]
    confluence.execution.requiredAuthTypes == ["api_key"]

``GET /api/oauth/configs`` lists github, gmail, googlecalendar, jira, notion and
slack — no confluence — and registering one anyway answers
``400 unsupported_auth_type: confluence does not support oauth2``.

That matters beyond Confluence: ``register_gateway_oauth_configs`` runs at
startup and RAISES on any provider it cannot register, so adding Confluence to
PROVIDER_REGISTRY would not have degraded gracefully — it would have stopped the
backend from booting. Any future provider added here must be checked against
that endpoint first; the registry is not the place to express an intention.

Serving Confluence would mean teaching the gateway path an api_key credential
flow, which is a separate piece of work from this migration.

Atlassian therefore remains ONE Muldro installation with ONE OC provider today.
The fan-out machinery is unchanged and already correct for a second one
(``gmail`` + ``googlecalendar`` share a Google client the same way), so adding
Confluence later is a registry entry plus that credential flow.

**No perception source, deliberately, and for a different reason than GitHub's.**
GitHub's poll is deferred because the catalog has no action that can serve it.
The action Jira would need DOES exist and is more expressive than any other
provider's — ``jira.search_issues`` takes JQL, so ``updated >= ...`` is a
first-class server-side filter rather than a client-side watermark. Two things
are missing instead, and neither is code:

  1. **Which signal belongs in the feed has not been decided.** "Issues assigned
     to me", "issues I am mentioned in" and "anything that moved in my projects"
     are three different products, and the third floods a founder's feed. The
     equivalent choice for GitHub (notifications, not issues) determined that
     connector's entire shape, so guessing here would be the expensive kind of
     wrong.
  2. **There is nothing to verify against.** Atlassian has never been connected
     on this deployment, so a connector written now could not be run once
     against real rows — and the two most recent perception defects both passed
     their unit tests and failed on live data.

Also note ``jira.search_issues`` paginates under a NESTED
``pagination.nextCursor``, where ``GatewayConnector._walk_pages`` reads a
top-level key. Adding the poll therefore means teaching the shared walk about
nested cursors — a change to every connector's substrate, which should be made
alongside a connector that can actually exercise it.

Scope note: the native Atlassian connect path requests ``write:jira-work`` and
``manage:jira-project``. The actions below need far less than that, and the
mismatch predates this module — recorded here because the gateway is where the
scope is now granted.
"""

from __future__ import annotations

from src.integrations.gateway_actions._types import GatewayAction, GatewayProvider

# Atlassian Document Format. Both write actions accept EITHER a plain-text
# convenience field or a full ADF document; the shape is declared once.
_ADF_DOC: dict = {
    "type": "object",
    "properties": {
        "type": {"const": "doc", "type": "string"},
        "version": {"type": "integer"},
        "content": {"type": "array", "items": {"description": "ADF top-level node."}},
    },
    "additionalProperties": True,
    "required": ["type", "version", "content"],
    "description": "Atlassian Document Format document.",
}

_EXPAND: dict = {
    "type": "array",
    "items": {"type": "string", "minLength": 1},
    "minItems": 1,
    "description": "Additional Jira expand tokens.",
}

_INCLUDE_FIELDS: dict = {
    "type": "array",
    "items": {"type": "string", "minLength": 1},
    "minItems": 1,
    "description": "Additional Jira issue fields.",
}

JIRA_ACTIONS: tuple[GatewayAction, ...] = (
    GatewayAction(
        "jira.search_issues",
        "workflow.search",
        "low",
        False,
        {
            "type": "object",
            "properties": {
                "jql": {"type": "string", "minLength": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "cursor": {"type": "string", "minLength": 1},
                "includeFields": _INCLUDE_FIELDS,
                "expand": _EXPAND,
            },
            "additionalProperties": False,
            "required": ["jql"],
        },
    ),
    GatewayAction(
        "jira.get_issue",
        "workflow.get",
        "low",
        False,
        {
            "type": "object",
            "properties": {
                "issueIdOrKey": {"type": "string", "minLength": 1},
                "includeFields": _INCLUDE_FIELDS,
                "expand": _EXPAND,
            },
            "additionalProperties": False,
            "required": ["issueIdOrKey"],
        },
    ),
    GatewayAction(
        "jira.list_issue_comments",
        "workflow.list",
        "low",
        False,
        {
            "type": "object",
            "properties": {
                "issueIdOrKey": {"type": "string", "minLength": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                # Jira's startAt paging, not an opaque cursor -- the pattern is
                # OpenConnector's own and rejects anything but a digit string.
                "cursor": {"type": "string", "pattern": r"^\d+$"},
                "expand": _EXPAND,
            },
            "additionalProperties": False,
            "required": ["issueIdOrKey"],
        },
    ),
    GatewayAction(
        "jira.get_project",
        "workflow.get_project",
        "low",
        False,
        {
            "type": "object",
            "properties": {
                "projectIdOrKey": {"type": "string", "minLength": 1},
                "expand": _EXPAND,
            },
            "additionalProperties": False,
            "required": ["projectIdOrKey"],
        },
    ),
    GatewayAction(
        "jira.list_projects",
        "workflow.list_projects",
        "low",
        False,
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "cursor": {"type": "string", "pattern": r"^\d+$"},
                "expand": _EXPAND,
            },
            "additionalProperties": False,
        },
    ),
    GatewayAction(
        "jira.create_issue",
        "workflow.create_issue",
        "medium",
        True,
        {
            "type": "object",
            "properties": {
                "projectKey": {"type": "string", "minLength": 1},
                "projectId": {"type": "string", "minLength": 1},
                "issueTypeId": {"type": "string", "minLength": 1},
                "issueTypeName": {"type": "string", "minLength": 1},
                "summary": {"type": "string", "minLength": 1},
                "descriptionText": {"type": "string", "minLength": 1},
                "description": _ADF_DOC,
                "labels": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "minItems": 1,
                },
                "assigneeAccountId": {"type": "string", "minLength": 1},
                "parentIssueKey": {"type": "string", "minLength": 1},
                "priorityId": {"type": "string", "minLength": 1},
                "dueDate": {"type": "string", "format": "date"},
                # `extraFields` is OpenConnector's open escape hatch onto the
                # raw Jira create payload. Deliberately NOT declared: it accepts
                # arbitrary field ids, so an agent could set anything the
                # project schema exposes while the action still reads as a
                # plain "create issue" at the gate.
            },
            "additionalProperties": False,
            "required": ["summary"],
        },
    ),
    GatewayAction(
        "jira.add_comment",
        "workflow.comment",
        "medium",
        True,
        {
            "type": "object",
            "properties": {
                "issueIdOrKey": {"type": "string", "minLength": 1},
                "bodyText": {"type": "string", "minLength": 1},
                "body": _ADF_DOC,
            },
            "additionalProperties": False,
            "required": ["issueIdOrKey"],
        },
    ),
)

JIRA = GatewayProvider(
    provider_id="jira",
    server_name="atlassian",
    display_name="Jira",
    oauth_credential_key="atlassian",
    actions=JIRA_ACTIONS,
)
