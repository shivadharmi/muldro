"""GitHub connector — polls for notifications and events."""

import logging
from datetime import datetime, timezone

from src.connectors.base import BaseConnector, ConnectorHealth, register_connector
from src.services.event_processor import RawEvent

logger = logging.getLogger(__name__)


@register_connector("github")
class GitHubConnector(BaseConnector):
    """Polls GitHub API for notifications and events."""

    async def poll(
        self, user_id: str, cursor: str | None, credentials: dict
    ) -> tuple[list[RawEvent], str | None]:
        """Poll GitHub notifications since timestamp cursor."""
        import httpx

        access_token = credentials.get("access_token", "")
        if not access_token:
            return [], cursor

        events = []
        new_cursor = cursor

        try:
            async with httpx.AsyncClient() as client:
                params = {"all": "false", "participating": "true"}
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

        except Exception:
            logger.warning("GitHub poll failed for user %s", user_id, exc_info=True)

        logger.info("GitHub poll: %d events", len(events))
        return events, new_cursor

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
