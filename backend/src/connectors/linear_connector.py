"""Linear connector — polls for issues and supports write actions (MCP fallback)."""

import logging
from datetime import datetime, timezone

from src.connectors.base import BaseConnector, ConnectorHealth, register_connector
from src.services.event_processor import RawEvent

logger = logging.getLogger(__name__)

LINEAR_API_URL = "https://api.linear.app/graphql"


@register_connector("linear")
class LinearConnector(BaseConnector):
    """Polls Linear GraphQL API for issue changes. MCP server is the primary write path."""

    cursor_type: str = "since_timestamp"
    supports_actions: bool = True
    available_actions: list[str] = ["create_issue", "update_issue", "comment"]

    async def poll(
        self, user_id: str, cursor: str | None, credentials: dict
    ) -> tuple[list[RawEvent], str | None]:
        """Poll Linear for issues updated since cursor (ISO timestamp)."""
        import httpx

        access_token = credentials.get("access_token", "")
        if not access_token:
            return [], cursor

        events: list[RawEvent] = []
        filter_clause = ""
        if cursor:
            filter_clause = f', filter: {{ updatedAt: {{ gt: "{cursor}" }} }}'

        query = f"""
        query {{
            issues(first: 50, orderBy: updatedAt{filter_clause}) {{
                nodes {{
                    id
                    identifier
                    title
                    description
                    state {{ name }}
                    assignee {{ name email }}
                    updatedAt
                    createdAt
                    priority
                    team {{ name }}
                }}
            }}
        }}
        """

        new_cursor = cursor
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    LINEAR_API_URL,
                    json={"query": query},
                    headers={
                        "Authorization": access_token,
                        "Content-Type": "application/json",
                    },
                    timeout=15,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    nodes = data.get("data", {}).get("issues", {}).get("nodes", [])
                    for node in nodes:
                        event = self._normalize_issue(node)
                        if event:
                            events.append(event)
                    if nodes:
                        new_cursor = nodes[-1].get("updatedAt", cursor)
        except Exception:
            logger.warning("Linear poll failed for user %s", user_id, exc_info=True)

        logger.info("Linear poll: %d events", len(events))
        return events, new_cursor

    async def test(self, credentials: dict) -> ConnectorHealth:
        """Test Linear connection."""
        import httpx

        access_token = credentials.get("access_token", "")
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    LINEAR_API_URL,
                    json={"query": "query { viewer { id name } }"},
                    headers={"Authorization": access_token},
                    timeout=10,
                )
                if resp.status_code == 200 and resp.json().get("data", {}).get("viewer"):
                    return ConnectorHealth(
                        provider="linear",
                        status="healthy",
                        last_poll_at=datetime.now(timezone.utc),
                    )
                return ConnectorHealth(
                    provider="linear",
                    status="down",
                    last_poll_at=None,
                    error=f"HTTP {resp.status_code}",
                )
        except Exception as e:
            return ConnectorHealth(
                provider="linear", status="down", last_poll_at=None, error=str(e)
            )

    async def get_auth_url(self, scopes: list[str] | None = None) -> str:
        return "/v1/auth/oauth/linear/authorize"

    async def execute_action(self, action: str, params: dict, credentials: dict) -> dict:
        """Execute a Linear write action (fallback when MCP is unavailable)."""
        if action not in self.available_actions:
            return {"status": "error", "error": f"Unknown action: {action}"}

        access_token = credentials.get("access_token", "")
        if not access_token:
            return {"status": "error", "error": "No access token"}

        dispatch = {
            "create_issue": self._action_create_issue,
            "update_issue": self._action_update_issue,
            "comment": self._action_comment,
        }
        return await dispatch[action](params, access_token)

    async def _action_create_issue(self, params: dict, access_token: str) -> dict:
        import httpx

        team_id = params.get("team_id", "")
        title = params.get("title", "")
        if not team_id or not title:
            return {"status": "error", "error": "team_id and title required"}

        mutation = """
        mutation($input: IssueCreateInput!) {
            issueCreate(input: $input) {
                success
                issue { id identifier title url }
            }
        }
        """
        variables = {
            "input": {
                "teamId": team_id,
                "title": title,
                "description": params.get("description", ""),
            }
        }
        if params.get("priority"):
            variables["input"]["priority"] = params["priority"]
        if params.get("assignee_id"):
            variables["input"]["assigneeId"] = params["assignee_id"]

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                LINEAR_API_URL,
                json={"query": mutation, "variables": variables},
                headers={"Authorization": access_token},
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json().get("data", {}).get("issueCreate", {})
                if data.get("success"):
                    issue = data.get("issue", {})
                    return {
                        "status": "ok",
                        "issue_id": issue.get("id"),
                        "identifier": issue.get("identifier"),
                        "url": issue.get("url"),
                    }
            return {"status": "error", "error": f"HTTP {resp.status_code}"}

    async def _action_update_issue(self, params: dict, access_token: str) -> dict:
        import httpx

        issue_id = params.get("issue_id", "")
        if not issue_id:
            return {"status": "error", "error": "issue_id required"}

        mutation = """
        mutation($id: String!, $input: IssueUpdateInput!) {
            issueUpdate(id: $id, input: $input) {
                success
                issue { id identifier title }
            }
        }
        """
        update_input: dict = {}
        for key in ("title", "description", "priority"):
            if params.get(key):
                update_input[key] = params[key]
        if params.get("state_id"):
            update_input["stateId"] = params["state_id"]
        if params.get("assignee_id"):
            update_input["assigneeId"] = params["assignee_id"]

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                LINEAR_API_URL,
                json={
                    "query": mutation,
                    "variables": {"id": issue_id, "input": update_input},
                },
                headers={"Authorization": access_token},
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json().get("data", {}).get("issueUpdate", {})
                if data.get("success"):
                    return {"status": "ok", "issue_id": issue_id}
            return {"status": "error", "error": f"HTTP {resp.status_code}"}

    async def _action_comment(self, params: dict, access_token: str) -> dict:
        import httpx

        issue_id = params.get("issue_id", "")
        body = params.get("body", "")
        if not issue_id or not body:
            return {"status": "error", "error": "issue_id and body required"}

        mutation = """
        mutation($input: CommentCreateInput!) {
            commentCreate(input: $input) {
                success
                comment { id }
            }
        }
        """
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                LINEAR_API_URL,
                json={
                    "query": mutation,
                    "variables": {"input": {"issueId": issue_id, "body": body}},
                },
                headers={"Authorization": access_token},
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json().get("data", {}).get("commentCreate", {})
                if data.get("success"):
                    return {"status": "ok", "comment_id": data.get("comment", {}).get("id")}
            return {"status": "error", "error": f"HTTP {resp.status_code}"}

    @staticmethod
    def _normalize_issue(node: dict) -> RawEvent | None:
        state = node.get("state", {}).get("name", "")
        assignee = node.get("assignee", {}) or {}
        team = node.get("team", {}).get("name", "")

        created = node.get("createdAt", "")
        updated = node.get("updatedAt", "")
        event_type = "issue_created" if created == updated else "issue_updated"

        occurred_at = None
        if updated:
            try:
                occurred_at = datetime.fromisoformat(updated.replace("Z", "+00:00"))
            except ValueError:
                pass

        return RawEvent(
            source="linear",
            source_account_id="linear_primary",
            event_type=event_type,
            entity_type="issue",
            entity_id=node.get("id", ""),
            occurred_at=occurred_at,
            title=f"[{node.get('identifier', '')}] {node.get('title', '')}",
            summary=f"{state} — {team} — {node.get('title', '')}",
            actor={
                "type": "person",
                "name": assignee.get("name", ""),
                "email": assignee.get("email", ""),
            },
            raw_payload={
                "identifier": node.get("identifier"),
                "state": state,
                "priority": node.get("priority"),
                "team": team,
            },
        )
