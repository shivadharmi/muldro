"""Jira connector — polls for issue updates and supports write actions (MCP fallback)."""

import logging
from datetime import datetime, timezone

from src.connectors.base import BaseConnector, ConnectorHealth, register_connector
from src.services.event_processor import RawEvent

logger = logging.getLogger(__name__)


@register_connector("jira")
class JiraConnector(BaseConnector):
    """Polls Jira REST API v3 for issue changes. MCP server is the primary write path."""

    supports_actions: bool = True
    available_actions: list[str] = ["create_issue", "update_issue", "transition", "comment"]

    def _base_url(self, cloud_id: str = "") -> str:
        """Build base API URL. Prefers cloud_id from settings/config."""
        cid = cloud_id or (getattr(self._settings, "jira_cloud_id", "") if self._settings else "")
        if cid:
            return f"https://api.atlassian.com/ex/jira/{cid}/rest/api/3"
        jira_url = getattr(self._settings, "jira_url", "") if self._settings else ""
        if jira_url:
            return f"{jira_url.rstrip('/')}/rest/api/3"
        return ""

    async def poll(
        self, user_id: str, cursor: str | None, credentials: dict
    ) -> tuple[list[RawEvent], str | None]:
        """Poll Jira for issues updated since cursor (datetime string)."""
        import httpx

        access_token = credentials.get("access_token", "")
        base = self._base_url()
        if not access_token or not base:
            return [], cursor

        events: list[RawEvent] = []
        new_cursor = cursor

        jql = "ORDER BY updated DESC"
        if cursor:
            jql = f'updated >= "{cursor}" ORDER BY updated ASC'

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{base}/search",
                    params={
                        "jql": jql,
                        "maxResults": 50,
                        "fields": (
                            "summary,status,assignee,updated,created,priority,issuetype,project"
                        ),
                    },
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Accept": "application/json",
                    },
                    timeout=15,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    for issue in data.get("issues", []):
                        event = self._normalize_issue(issue)
                        if event:
                            events.append(event)
                        updated = issue.get("fields", {}).get("updated", "")
                        if updated and (not new_cursor or updated > new_cursor):
                            new_cursor = updated
        except Exception:
            logger.warning("Jira poll failed for user %s", user_id, exc_info=True)

        logger.info("Jira poll: %d events", len(events))
        return events, new_cursor

    async def test(self, credentials: dict) -> ConnectorHealth:
        import httpx

        access_token = credentials.get("access_token", "")
        base = self._base_url()
        if not base:
            return ConnectorHealth(
                provider="jira", status="down", last_poll_at=None, error="No Jira URL configured"
            )

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{base}/myself",
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=10,
                )
                if resp.status_code == 200:
                    return ConnectorHealth(
                        provider="jira", status="healthy", last_poll_at=datetime.now(timezone.utc)
                    )
                return ConnectorHealth(
                    provider="jira",
                    status="down",
                    last_poll_at=None,
                    error=f"HTTP {resp.status_code}",
                )
        except Exception as e:
            return ConnectorHealth(provider="jira", status="down", last_poll_at=None, error=str(e))

    async def get_auth_url(self, scopes: list[str] | None = None) -> str:
        return "/v1/auth/oauth/jira/authorize"

    async def execute_action(self, action: str, params: dict, credentials: dict) -> dict:
        if action not in self.available_actions:
            return {"status": "error", "error": f"Unknown action: {action}"}

        access_token = credentials.get("access_token", "")
        if not access_token:
            return {"status": "error", "error": "No access token"}

        dispatch = {
            "create_issue": self._action_create_issue,
            "update_issue": self._action_update_issue,
            "transition": self._action_transition,
            "comment": self._action_comment,
        }
        return await dispatch[action](params, access_token)

    async def _action_create_issue(self, params: dict, access_token: str) -> dict:
        import httpx

        project_key = params.get("project_key", "")
        summary = params.get("summary", "")
        issue_type = params.get("issue_type", "Task")
        if not project_key or not summary:
            return {"status": "error", "error": "project_key and summary required"}

        base = self._base_url()
        body = {
            "fields": {
                "project": {"key": project_key},
                "summary": summary,
                "issuetype": {"name": issue_type},
            }
        }
        if params.get("description"):
            body["fields"]["description"] = {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": params["description"]}],
                    }
                ],
            }

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{base}/issue",
                json=body,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                timeout=15,
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                return {"status": "ok", "issue_key": data.get("key"), "issue_id": data.get("id")}
            return {"status": "error", "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}

    async def _action_update_issue(self, params: dict, access_token: str) -> dict:
        import httpx

        issue_key = params.get("issue_key", "")
        if not issue_key:
            return {"status": "error", "error": "issue_key required"}

        base = self._base_url()
        fields: dict = {}
        if params.get("summary"):
            fields["summary"] = params["summary"]
        if params.get("description"):
            fields["description"] = {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": params["description"]}],
                    }
                ],
            }
        if params.get("assignee_id"):
            fields["assignee"] = {"accountId": params["assignee_id"]}

        async with httpx.AsyncClient() as client:
            resp = await client.put(
                f"{base}/issue/{issue_key}",
                json={"fields": fields},
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                timeout=15,
            )
            if resp.status_code in (200, 204):
                return {"status": "ok", "issue_key": issue_key}
            return {"status": "error", "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}

    async def _action_transition(self, params: dict, access_token: str) -> dict:
        import httpx

        issue_key = params.get("issue_key", "")
        transition_id = params.get("transition_id", "")
        if not issue_key or not transition_id:
            return {"status": "error", "error": "issue_key and transition_id required"}

        base = self._base_url()
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{base}/issue/{issue_key}/transitions",
                json={"transition": {"id": transition_id}},
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                timeout=15,
            )
            if resp.status_code in (200, 204):
                return {"status": "ok", "issue_key": issue_key, "transition_id": transition_id}
            return {"status": "error", "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}

    async def _action_comment(self, params: dict, access_token: str) -> dict:
        import httpx

        issue_key = params.get("issue_key", "")
        body_text = params.get("body", "")
        if not issue_key or not body_text:
            return {"status": "error", "error": "issue_key and body required"}

        base = self._base_url()
        body = {
            "body": {
                "type": "doc",
                "version": 1,
                "content": [
                    {"type": "paragraph", "content": [{"type": "text", "text": body_text}]}
                ],
            }
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{base}/issue/{issue_key}/comment",
                json=body,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                timeout=15,
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                return {"status": "ok", "comment_id": data.get("id")}
            return {"status": "error", "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}

    @staticmethod
    def _normalize_issue(issue: dict) -> RawEvent | None:
        fields = issue.get("fields", {})
        summary = fields.get("summary", "")
        status = fields.get("status", {}).get("name", "")
        assignee = fields.get("assignee") or {}
        project = fields.get("project", {}).get("key", "")
        issue_type = fields.get("issuetype", {}).get("name", "")
        priority = fields.get("priority", {}).get("name", "")

        updated = fields.get("updated", "")
        created = fields.get("created", "")
        event_type = "issue_created" if updated == created else "issue_updated"

        occurred_at = None
        if updated:
            try:
                occurred_at = datetime.fromisoformat(updated.replace("Z", "+00:00"))
            except ValueError:
                pass

        return RawEvent(
            source="jira",
            source_account_id="jira_primary",
            event_type=event_type,
            entity_type="issue",
            entity_id=issue.get("key", issue.get("id", "")),
            occurred_at=occurred_at,
            title=f"[{issue.get('key', '')}] {summary}",
            summary=f"{issue_type} — {status} — {priority}",
            actor={
                "type": "person",
                "name": assignee.get("displayName", ""),
                "email": assignee.get("emailAddress", ""),
            },
            raw_payload={
                "issue_key": issue.get("key"),
                "project": project,
                "status": status,
                "issue_type": issue_type,
                "priority": priority,
            },
        )
