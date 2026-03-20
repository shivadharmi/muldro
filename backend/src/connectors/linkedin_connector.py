"""LinkedIn connector — polls for posts and supports write actions (native only)."""

import logging
from datetime import datetime, timezone

from src.connectors.base import BaseConnector, ConnectorHealth, register_connector
from src.services.event_processor import RawEvent

logger = logging.getLogger(__name__)

LINKEDIN_API = "https://api.linkedin.com/v2"


@register_connector("linkedin")
class LinkedInConnector(BaseConnector):
    """LinkedIn REST v2 API connector. No MCP server — native only."""

    supports_actions: bool = True
    available_actions: list[str] = ["create_post", "share_article"]

    async def poll(
        self, user_id: str, cursor: str | None, credentials: dict
    ) -> tuple[list[RawEvent], str | None]:
        """Poll LinkedIn for own posts since cursor (timestamp ms)."""
        import httpx

        access_token = credentials.get("access_token", "")
        if not access_token:
            return [], cursor

        events: list[RawEvent] = []
        new_cursor = cursor

        try:
            async with httpx.AsyncClient() as client:
                # Get user profile URN
                me_resp = await client.get(
                    f"{LINKEDIN_API}/me",
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=10,
                )
                if me_resp.status_code != 200:
                    return [], cursor
                person_urn = f"urn:li:person:{me_resp.json().get('id', '')}"

                # Fetch recent posts
                params = {"q": "authors", "authors": f"List({person_urn})", "count": 20}
                resp = await client.get(
                    f"{LINKEDIN_API}/ugcPosts",
                    params=params,
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=15,
                )
                if resp.status_code == 200:
                    for post in resp.json().get("elements", []):
                        created_ts = post.get("created", {}).get("time", 0)
                        if cursor and created_ts <= int(cursor):
                            continue
                        event = self._normalize_post(post, me_resp.json())
                        if event:
                            events.append(event)
                        ts_str = str(created_ts)
                        if not new_cursor or ts_str > new_cursor:
                            new_cursor = ts_str

        except Exception:
            logger.warning("LinkedIn poll failed for user %s", user_id, exc_info=True)

        logger.info("LinkedIn poll: %d events", len(events))
        return events, new_cursor

    async def test(self, credentials: dict) -> ConnectorHealth:
        import httpx

        access_token = credentials.get("access_token", "")
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{LINKEDIN_API}/me",
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=10,
                )
                if resp.status_code == 200:
                    return ConnectorHealth(
                        provider="linkedin",
                        status="healthy",
                        last_poll_at=datetime.now(timezone.utc),
                    )
                return ConnectorHealth(
                    provider="linkedin",
                    status="down",
                    last_poll_at=None,
                    error=f"HTTP {resp.status_code}",
                )
        except Exception as e:
            return ConnectorHealth(
                provider="linkedin", status="down", last_poll_at=None, error=str(e)
            )

    async def get_auth_url(self, scopes: list[str] | None = None) -> str:
        return "/v1/auth/oauth/linkedin/authorize"

    async def execute_action(self, action: str, params: dict, credentials: dict) -> dict:
        if action not in self.available_actions:
            return {"status": "error", "error": f"Unknown action: {action}"}

        access_token = credentials.get("access_token", "")
        if not access_token:
            return {"status": "error", "error": "No access token"}

        dispatch = {
            "create_post": self._action_create_post,
            "share_article": self._action_share_article,
        }
        return await dispatch[action](params, access_token)

    async def _action_create_post(self, params: dict, access_token: str) -> dict:
        import httpx

        text = params.get("text", "")
        if not text:
            return {"status": "error", "error": "text required"}

        async with httpx.AsyncClient() as client:
            # Get author URN
            me = await client.get(
                f"{LINKEDIN_API}/me",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10,
            )
            if me.status_code != 200:
                return {"status": "error", "error": "Failed to get LinkedIn profile"}
            author_urn = f"urn:li:person:{me.json().get('id', '')}"

            body = {
                "author": author_urn,
                "lifecycleState": "PUBLISHED",
                "specificContent": {
                    "com.linkedin.ugc.ShareContent": {
                        "shareCommentary": {"text": text},
                        "shareMediaCategory": "NONE",
                    }
                },
                "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
            }

            resp = await client.post(
                f"{LINKEDIN_API}/ugcPosts",
                json=body,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "X-Restli-Protocol-Version": "2.0.0",
                },
                timeout=15,
            )
            if resp.status_code in (200, 201):
                post_id = resp.headers.get("x-restli-id", resp.json().get("id", ""))
                return {"status": "ok", "post_id": post_id}
            return {"status": "error", "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}

    async def _action_share_article(self, params: dict, access_token: str) -> dict:
        import httpx

        url = params.get("url", "")
        text = params.get("text", "")
        title = params.get("title", "")
        if not url:
            return {"status": "error", "error": "url required"}

        async with httpx.AsyncClient() as client:
            me = await client.get(
                f"{LINKEDIN_API}/me",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10,
            )
            if me.status_code != 200:
                return {"status": "error", "error": "Failed to get LinkedIn profile"}
            author_urn = f"urn:li:person:{me.json().get('id', '')}"

            body = {
                "author": author_urn,
                "lifecycleState": "PUBLISHED",
                "specificContent": {
                    "com.linkedin.ugc.ShareContent": {
                        "shareCommentary": {"text": text},
                        "shareMediaCategory": "ARTICLE",
                        "media": [
                            {
                                "status": "READY",
                                "originalUrl": url,
                                "title": {"text": title or url},
                            }
                        ],
                    }
                },
                "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
            }

            resp = await client.post(
                f"{LINKEDIN_API}/ugcPosts",
                json=body,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "X-Restli-Protocol-Version": "2.0.0",
                },
                timeout=15,
            )
            if resp.status_code in (200, 201):
                post_id = resp.headers.get("x-restli-id", "")
                return {"status": "ok", "post_id": post_id}
            return {"status": "error", "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}

    @staticmethod
    def _normalize_post(post: dict, profile: dict) -> RawEvent | None:
        created_ts = post.get("created", {}).get("time", 0)
        share_content = post.get("specificContent", {}).get("com.linkedin.ugc.ShareContent", {})
        text = share_content.get("shareCommentary", {}).get("text", "")

        occurred_at = None
        if created_ts:
            try:
                occurred_at = datetime.fromtimestamp(created_ts / 1000, tz=timezone.utc)
            except (ValueError, OSError):
                pass

        return RawEvent(
            source="linkedin",
            source_account_id="linkedin_primary",
            event_type="post_created",
            entity_type="post",
            entity_id=post.get("id", ""),
            occurred_at=occurred_at,
            title=f"LinkedIn post: {text[:80]}",
            summary=text[:500],
            actor={
                "type": "person",
                "name": (
                    f"{profile.get('localizedFirstName', '')} "
                    f"{profile.get('localizedLastName', '')}"
                ).strip(),
            },
            raw_payload={"post_id": post.get("id")},
        )
