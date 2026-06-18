"""GitHub connector — polls for notifications and events."""

import logging
from datetime import datetime, timezone

from src.connectors.base import BaseConnector, ConnectorHealth, register_connector
from src.connectors.poll_result import PollErrorClass, PollResult
from src.services.event_processor import RawEvent

logger = logging.getLogger(__name__)


def _classify_http_status(status_code: int) -> PollErrorClass:
    """Map an HTTP status code to a PollErrorClass."""
    if status_code in (401, 403):
        return "auth_failed"
    if status_code == 429:
        return "rate_limited"
    if status_code >= 500:
        return "transient"
    if status_code >= 400:
        return "permanent"
    return "none"


@register_connector("github")
class GitHubConnector(BaseConnector):
    """Polls GitHub API for notifications and events."""

    cursor_type: str = "since_timestamp"

    async def poll(self, user_id: str, cursor: str | None, credentials: dict) -> PollResult:
        """Poll GitHub notifications since timestamp cursor."""
        import httpx

        access_token = credentials.get("access_token", "")
        if not access_token:
            return PollResult(events=[], cursor=cursor, error_class="auth_failed")

        events: list[RawEvent] = []
        new_cursor = cursor

        try:
            async with httpx.AsyncClient() as client:
                params: dict = {"all": "false", "participating": "true"}
                if cursor:
                    params["since"] = cursor

                resp = await client.get(
                    "https://api.github.com/notifications",
                    params=params,
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Accept": "application/vnd.github+json",
                    },
                    timeout=15,
                )

                if resp.status_code == 200:
                    notifications = resp.json()
                    for notif in notifications:
                        event = self._normalize_notification(notif)
                        if event:
                            events.append(event)

                    # Update cursor to latest
                    if notifications:
                        new_cursor = datetime.now(timezone.utc).isoformat()
                else:
                    error_class = _classify_http_status(resp.status_code)
                    logger.warning(
                        "GitHub notifications API returned %d for user %s",
                        resp.status_code,
                        user_id,
                    )
                    return PollResult(events=[], cursor=cursor, error_class=error_class)

        except Exception:
            logger.warning("GitHub poll failed for user %s", user_id, exc_info=True)
            return PollResult(events=[], cursor=cursor, error_class="transient")

        logger.info("GitHub poll: %d events", len(events))
        return PollResult(events=events, cursor=new_cursor)

    async def test(self, credentials: dict) -> ConnectorHealth:
        """Test GitHub connection."""
        import httpx

        access_token = credentials.get("access_token", "")
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://api.github.com/user",
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=10,
                )
                status = "healthy" if resp.status_code == 200 else "down"
                return ConnectorHealth(
                    provider="github",
                    status=status,
                    last_poll_at=datetime.now(timezone.utc) if status == "healthy" else None,
                    error=None if status == "healthy" else f"HTTP {resp.status_code}",
                )
        except Exception as e:
            return ConnectorHealth(
                provider="github", status="down", last_poll_at=None, error=str(e)
            )

    supports_actions: bool = True
    available_actions: list[str] = ["create_issue", "comment", "create_pr"]

    async def execute_action(self, action: str, params: dict, credentials: dict) -> dict:
        """Execute a GitHub write action."""
        if action not in self.available_actions:
            return {"status": "error", "error": f"Unknown action: {action}"}

        access_token = credentials.get("access_token", "")
        if not access_token:
            return {"status": "error", "error": "No access token"}

        dispatch = {
            "create_issue": self._action_create_issue,
            "comment": self._action_comment,
            "create_pr": self._action_create_pr,
        }
        return await dispatch[action](params, access_token)

    async def _action_create_issue(self, params: dict, access_token: str) -> dict:
        """Create a GitHub issue."""
        import httpx

        owner = params.get("owner", "")
        repo = params.get("repo", "")
        if not owner or not repo:
            return {"status": "error", "error": "owner and repo required"}

        body = {"title": params.get("title", ""), "body": params.get("body", "")}
        if params.get("labels"):
            body["labels"] = params["labels"]
        if params.get("assignees"):
            body["assignees"] = params["assignees"]

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://api.github.com/repos/{owner}/{repo}/issues",
                json=body,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github+json",
                },
                timeout=15,
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                return {
                    "status": "ok",
                    "issue_number": data.get("number"),
                    "html_url": data.get("html_url"),
                }
            return {"status": "error", "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}

    async def _action_comment(self, params: dict, access_token: str) -> dict:
        """Add a comment to an issue or PR."""
        import httpx

        owner = params.get("owner", "")
        repo = params.get("repo", "")
        number = params.get("number")
        if not owner or not repo or not number:
            return {"status": "error", "error": "owner, repo, and number required"}

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://api.github.com/repos/{owner}/{repo}/issues/{number}/comments",
                json={"body": params.get("body", "")},
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github+json",
                },
                timeout=15,
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                return {
                    "status": "ok",
                    "comment_id": data.get("id"),
                    "html_url": data.get("html_url"),
                }
            return {"status": "error", "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}

    async def _action_create_pr(self, params: dict, access_token: str) -> dict:
        """Create a pull request."""
        import httpx

        owner = params.get("owner", "")
        repo = params.get("repo", "")
        if not owner or not repo:
            return {"status": "error", "error": "owner and repo required"}

        body = {
            "title": params.get("title", ""),
            "body": params.get("body", ""),
            "head": params.get("head", ""),
            "base": params.get("base", "main"),
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://api.github.com/repos/{owner}/{repo}/pulls",
                json=body,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github+json",
                },
                timeout=15,
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                return {
                    "status": "ok",
                    "pr_number": data.get("number"),
                    "html_url": data.get("html_url"),
                }
            return {"status": "error", "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}

    async def get_auth_url(self, scopes: list[str] | None = None) -> str:
        return "/v1/auth/oauth/github/authorize"

    @staticmethod
    def _normalize_notification(notif: dict) -> RawEvent | None:
        """Convert a GitHub notification to a RawEvent."""
        subject = notif.get("subject", {})
        notif_type = subject.get("type", "")
        title = subject.get("title", "")
        repo = notif.get("repository", {}).get("full_name", "")
        reason = notif.get("reason", "")

        type_map = {
            "PullRequest": "pr_updated",
            "Issue": "issue_updated",
            "Release": "release_published",
            "Discussion": "discussion_updated",
            "Commit": "commit_commented",
        }
        event_type = type_map.get(notif_type, "github_notification")

        return RawEvent(
            source="github",
            source_account_id="github_primary",
            event_type=event_type,
            entity_type=notif_type.lower() if notif_type else "notification",
            entity_id=notif.get("id", ""),
            title=f"[{repo}] {title}",
            summary=f"{reason}: {title} in {repo}",
            actor={"type": "system", "name": repo},
            raw_payload={
                "notification_id": notif.get("id"),
                "reason": reason,
                "repo": repo,
                "url": subject.get("url"),
            },
        )
