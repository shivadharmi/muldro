"""Notion actions served through the OpenConnector gateway.

Action ids, parameter names, and input schemas are transcribed verbatim from a
live OpenConnector v1.3.5 catalog (`GET /api/actions`, filtered to
``service == "notion"`` — 25 actions). ``spike-findings-perception.md`` Q7
recorded only that the service EXISTS; the schemas below were read off the same
running container that serves them.

**Curated, not exhaustive.** Seven of the 25 are declared, chosen to cover the
capabilities the retired stdio installation advertised. One of those does not
survive the move: OpenConnector's notion service exposes no comment action, so
``doc.comment`` is dropped rather than declared against an id that would 404.
The database/data-source half of the catalog is likewise omitted — nothing in
Muldro drives it yet, and an unused write action is authority handed out for
free.

**Notion is a pure gateway provider.** Unlike GitHub it declares its perception
source here, because ``notion.search`` is a real data path the gateway can
serve — see :class:`src.connectors.notion_connector.NotionConnector`. The
provider therefore holds exactly one credential, which is the whole point of
retiring its native OAuth: the stdio server it replaced authenticated by having
``NOTION_TOKEN`` resolved out of the process environment and handed to an
``npx`` child, leaving the secret readable in ``ps aux`` for the life of the
process.
"""

from __future__ import annotations

from src.integrations.gateway_actions._types import GatewayAction, GatewayProvider

# Free-form Notion API objects appear in several schemas under the same shape.
# OpenConnector types them as an open object whose values it will not inspect;
# declaring the shape once keeps a future drift correction to a single edit.
_NOTION_OBJECT: dict = {
    "type": "object",
    "additionalProperties": {"description": "A Notion API field value."},
    "description": "A Notion API object.",
}

_NOTION_OBJECT_ARRAY: dict = {
    "type": "array",
    "items": _NOTION_OBJECT,
}

NOTION_ACTIONS: tuple[GatewayAction, ...] = (
    GatewayAction(
        "notion.search",
        "doc.search",
        "low",
        False,
        {
            "type": "object",
            "properties": {
                # Required by OpenConnector, but declared with NO minLength --
                # verified against the live catalog. That is what makes the
                # empty query the connector sends a legal "everything" request;
                # slack.search_messages sets minLength 1 and so cannot do this.
                "query": {"type": "string", "description": "The search query text."},
                "filter": {
                    **_NOTION_OBJECT,
                    "description": "The filter object to narrow results.",
                },
                "sort": {**_NOTION_OBJECT, "description": "The sort object to order results."},
                "pageSize": {"type": "integer", "minimum": 1, "maximum": 100},
                "startCursor": {"type": "string", "description": "The cursor for pagination."},
            },
            "additionalProperties": False,
            "required": ["query"],
        },
    ),
    GatewayAction(
        # Declared for the connector's health probe as much as for agents: it is
        # the only notion read taking no id and no query, and READ_ACTION must
        # resolve through this registry like any other call.
        "notion.list_users",
        "doc.get_users",
        "low",
        False,
        {
            "type": "object",
            "properties": {
                "pageSize": {"type": "integer", "minimum": 1, "maximum": 100},
                "startCursor": {"type": "string"},
            },
            "additionalProperties": False,
        },
    ),
    GatewayAction(
        "notion.retrieve_page",
        "doc.get",
        "low",
        False,
        {
            "type": "object",
            "properties": {"pageId": {"type": "string", "minLength": 1}},
            "additionalProperties": False,
            "required": ["pageId"],
        },
    ),
    GatewayAction(
        "notion.retrieve_page_markdown",
        "doc.get",
        "low",
        False,
        {
            "type": "object",
            "properties": {
                "pageId": {"type": "string", "minLength": 1},
                "includeTranscript": {"type": "boolean"},
            },
            "additionalProperties": False,
            "required": ["pageId"],
        },
    ),
    GatewayAction(
        "notion.list_block_children",
        "doc.get_children",
        "low",
        False,
        {
            "type": "object",
            "properties": {
                "blockId": {"type": "string", "minLength": 1},
                "pageSize": {"type": "integer", "minimum": 1, "maximum": 100},
                "startCursor": {"type": "string"},
            },
            "additionalProperties": False,
            "required": ["blockId"],
        },
    ),
    GatewayAction(
        "notion.create_page",
        "doc.create",
        "medium",
        True,
        {
            "type": "object",
            "properties": {
                # OpenConnector accepts EITHER the official nested `parent`
                # object or the flattened `parentId` + `title` pair, and marks
                # neither required. Both are declared so a caller is not forced
                # into the verbose form.
                "parent": _NOTION_OBJECT,
                "parentId": {"type": "string"},
                "title": {"type": "string"},
                "properties": _NOTION_OBJECT,
                "children": _NOTION_OBJECT_ARRAY,
                "markdown": {"type": "string"},
                "icon": _NOTION_OBJECT,
                "cover": _NOTION_OBJECT,
            },
            "additionalProperties": False,
        },
    ),
    GatewayAction(
        "notion.update_page",
        "doc.update",
        "medium",
        True,
        {
            "type": "object",
            "properties": {
                "pageId": {"type": "string", "minLength": 1},
                "title": {"type": "string"},
                "properties": _NOTION_OBJECT,
                "icon": _NOTION_OBJECT,
                "cover": _NOTION_OBJECT,
                # `in_trash` is a delete in everything but name, and
                # `erase_content` is unrecoverable. They stay declared because
                # OpenConnector accepts them and omitting them from the schema
                # would not stop a caller sending them -- the guard that
                # matters is `requires_approval`, which covers the whole action.
                "in_trash": {"type": "boolean"},
                "is_locked": {"type": "boolean"},
                "erase_content": {"type": "boolean"},
            },
            "additionalProperties": False,
            "required": ["pageId"],
        },
    ),
    GatewayAction(
        "notion.append_block_children",
        "doc.append",
        "medium",
        True,
        {
            "type": "object",
            "properties": {
                "blockId": {"type": "string", "minLength": 1},
                "children": _NOTION_OBJECT_ARRAY,
                "position": _NOTION_OBJECT,
            },
            "additionalProperties": False,
            "required": ["blockId", "children"],
        },
    ),
)

NOTION = GatewayProvider(
    provider_id="notion",
    server_name="notion",
    display_name="Notion",
    oauth_credential_key="notion",
    actions=NOTION_ACTIONS,
    # The source name and the OC provider id coincide here, unlike
    # googlecalendar -> "calendar". Declared anyway rather than left to an
    # identity fallback: `gateway_provider_for_source` resolves by MEMBERSHIP in
    # this index, so an undeclared source is treated as NOT gateway-backed and
    # the poller would look for an OAuthManager token that no longer exists.
    perception_sources=("notion",),
)
