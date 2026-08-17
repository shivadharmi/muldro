"""GitHub actions served through the OpenConnector gateway.

Action ids, parameter names, and input schemas are transcribed verbatim from a
live OpenConnector v1.3.5 catalog -- see infra/gateway/spike-findings-multiprovider.md.
GitHub is the cross-vendor case for this increment: its native Jarvis transport
was a remote HTTP MCP server rather than a local process, and its OpenConnector
auth is a real OAuth2 flow (verified in the spike), so the popup-poll connect
flow applies to it unchanged.
"""

from __future__ import annotations

from src.integrations.gateway_actions._types import GatewayAction, GatewayProvider

GITHUB_ACTIONS: tuple[GatewayAction, ...] = (
    GatewayAction(
        "github.list_repository_issues",
        "issue.list",
        "low",
        False,
        {
            "type": "object",
            "properties": {
                "owner": {"type": "string", "minLength": 1},
                "repo": {"type": "string", "minLength": 1},
                "state": {"type": "string", "enum": ["open", "closed", "all"]},
                "labels": {"type": "array", "items": {"type": "string"}},
                "sort": {"type": "string", "enum": ["created", "updated", "comments"]},
                "direction": {"type": "string", "enum": ["asc", "desc"]},
                "since": {"type": "string"},
                "perPage": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "description": "Number of results requested per page. Defaults to 30.",
                    "default": 30,
                },
                "page": {"type": "integer"},
            },
            "additionalProperties": False,
            "required": ["owner", "repo"],
        },
    ),
    GatewayAction(
        "github.search_issues_and_pull_requests",
        "issue.search",
        "low",
        False,
        {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "q": {"type": "string"},
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "state": {"type": "string", "enum": ["open", "closed", "all"]},
                "label": {"type": "string"},
                "author": {"type": "string"},
                "assignee": {"type": "string"},
                "mentions": {"type": "string"},
                "language": {"type": "string"},
                "baseBranch": {"type": "string"},
                "headBranch": {"type": "string"},
                "isMerged": {"type": "boolean"},
                "type": {"type": "string", "enum": ["issue", "pr"]},
                "sort": {
                    "type": "string",
                    "enum": [
                        "comments",
                        "reactions",
                        "reactions-+1",
                        "reactions--1",
                        "reactions-smile",
                        "reactions-thinking_face",
                        "reactions-heart",
                        "reactions-tada",
                        "interactions",
                        "created",
                        "updated",
                    ],
                },
                "order": {"type": "string", "enum": ["asc", "desc"]},
                "perPage": {"type": "integer"},
                "page": {"type": "integer"},
            },
            "additionalProperties": False,
        },
    ),
    GatewayAction(
        "github.create_issue",
        "issue.create",
        "medium",
        True,
        {
            "type": "object",
            "properties": {
                "owner": {"type": "string", "minLength": 1},
                "repo": {"type": "string", "minLength": 1},
                "title": {"type": "string", "minLength": 1},
                "body": {"type": "string"},
                "assignees": {"type": "array", "items": {"type": "string"}},
                "labels": {"type": "array", "items": {"type": "string"}},
                "milestone": {"type": "integer", "minimum": 1},
            },
            "additionalProperties": False,
            "required": ["owner", "repo", "title"],
        },
    ),
    GatewayAction(
        "github.create_issue_comment",
        "issue.comment",
        "medium",
        True,
        {
            "type": "object",
            "properties": {
                "owner": {"type": "string", "minLength": 1},
                "repo": {"type": "string", "minLength": 1},
                "issueNumber": {"type": "integer", "minimum": 1},
                "body": {"type": "string", "minLength": 1},
            },
            "additionalProperties": False,
            "required": ["owner", "repo", "issueNumber", "body"],
        },
    ),
    GatewayAction(
        "github.search_code",
        "repo.search_code",
        "low",
        False,
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1},
                "sort": {"type": "string", "enum": ["indexed", "updated"]},
                "order": {"type": "string", "enum": ["asc", "desc"]},
                "perPage": {"type": "integer"},
                "page": {"type": "integer"},
            },
            "additionalProperties": False,
            "required": ["query"],
        },
    ),
    GatewayAction(
        "github.search_repositories",
        "repo.search_repos",
        "low",
        False,
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1},
                "sort": {
                    "type": "string",
                    "enum": ["stars", "forks", "help-wanted-issues", "updated"],
                },
                "order": {"type": "string", "enum": ["asc", "desc"]},
                "perPage": {"type": "integer"},
                "page": {"type": "integer"},
            },
            "additionalProperties": False,
            "required": ["query"],
        },
    ),
    GatewayAction(
        "github.list_pull_requests",
        "repo.list_prs",
        "low",
        False,
        {
            "type": "object",
            "properties": {
                "owner": {"type": "string", "minLength": 1},
                "repo": {"type": "string", "minLength": 1},
                "state": {"type": "string", "enum": ["open", "closed", "all"]},
                "head": {"type": "string"},
                "base": {"type": "string"},
                "sort": {
                    "type": "string",
                    "enum": ["created", "updated", "popularity", "long-running"],
                },
                "direction": {"type": "string", "enum": ["asc", "desc"]},
                "perPage": {"type": "integer"},
                "page": {"type": "integer"},
            },
            "additionalProperties": False,
            "required": ["owner", "repo"],
        },
    ),
    GatewayAction(
        "github.create_pull_request",
        "repo.create_pr",
        "high",
        True,
        {
            "type": "object",
            "properties": {
                "owner": {"type": "string", "minLength": 1},
                "repo": {"type": "string", "minLength": 1},
                "title": {"type": "string", "minLength": 1},
                "head": {"type": "string", "minLength": 1},
                "base": {"type": "string", "minLength": 1},
                "body": {"type": "string"},
                "draft": {"type": "boolean"},
                "maintainerCanModify": {"type": "boolean"},
            },
            "additionalProperties": False,
            "required": ["owner", "repo", "title", "head", "base"],
        },
    ),
)

GITHUB = GatewayProvider(
    provider_id="github",
    server_name="github",
    display_name="GitHub",
    actions=GITHUB_ACTIONS,
)
