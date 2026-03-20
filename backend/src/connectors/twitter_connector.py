"""Twitter/X connector — polls for mentions and supports write actions (native only)."""

import logging
from datetime import datetime, timezone

from src.connectors.base import BaseConnector, ConnectorHealth, register_connector
from src.services.event_processor import RawEvent

logger = logging.getLogger(__name__)

TWITTER_API = "https://api.twitter.com/2"


@register_connector("twitter")
class TwitterConnector(BaseConnector):
    """Twitter/X REST v2 API connector. No mature MCP server — native only."""

    supports_actions: bool = True
    available_actions: list[str] = ["create_tweet", "reply", "retweet"]

    async def poll(
        self, user_id: str, cursor: str | None, credentials: dict
    ) -> tuple[list[RawEvent], str | None]:
        """Poll Twitter for mentions since cursor (since_id)."""
        import httpx

        access_token = credentials.get("access_token", "")
        if not access_token:
            return [], cursor

        events: list[RawEvent] = []
        new_cursor = cursor

        try:
            async with httpx.AsyncClient() as client:
                # Get authenticated user ID
                me_resp = await client.get(
                    f"{TWITTER_API}/users/me",
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=10,
                )
                if me_resp.status_code != 200:
                    return [], cursor
                twitter_user_id = me_resp.json().get("data", {}).get("id", "")

                params: dict = {
                    "tweet.fields": "created_at,author_id,conversation_id,text",
                    "max_results": 50,
                }
                if cursor:
                    params["since_id"] = cursor

                resp = await client.get(
                    f"{TWITTER_API}/users/{twitter_user_id}/mentions",
                    params=params,
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=15,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    for tweet in data.get("data", []):
                        event = self._normalize_tweet(tweet)
                        if event:
                            events.append(event)
                    # newest_id is the highest ID in results
                    meta = data.get("meta", {})
                    if meta.get("newest_id"):
                        new_cursor = meta["newest_id"]

        except Exception:
            logger.warning("Twitter poll failed for user %s", user_id, exc_info=True)

        logger.info("Twitter poll: %d events", len(events))
        return events, new_cursor

    async def test(self, credentials: dict) -> ConnectorHealth:
        import httpx

        access_token = credentials.get("access_token", "")
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{TWITTER_API}/users/me",
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=10,
                )
                if resp.status_code == 200:
                    return ConnectorHealth(
                        provider="twitter",
                        status="healthy",
                        last_poll_at=datetime.now(timezone.utc),
                    )
                return ConnectorHealth(
                    provider="twitter",
                    status="down",
                    last_poll_at=None,
                    error=f"HTTP {resp.status_code}",
                )
        except Exception as e:
            return ConnectorHealth(
                provider="twitter", status="down", last_poll_at=None, error=str(e)
            )

    async def get_auth_url(self, scopes: list[str] | None = None) -> str:
        return "/v1/auth/oauth/twitter/authorize"

    async def execute_action(self, action: str, params: dict, credentials: dict) -> dict:
        if action not in self.available_actions:
            return {"status": "error", "error": f"Unknown action: {action}"}

        access_token = credentials.get("access_token", "")
        if not access_token:
            return {"status": "error", "error": "No access token"}

        dispatch = {
            "create_tweet": self._action_create_tweet,
            "reply": self._action_reply,
            "retweet": self._action_retweet,
        }
        return await dispatch[action](params, access_token)

    async def _action_create_tweet(self, params: dict, access_token: str) -> dict:
        import httpx

        text = params.get("text", "")
        if not text:
            return {"status": "error", "error": "text required"}

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{TWITTER_API}/tweets",
                json={"text": text},
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                timeout=15,
            )
            if resp.status_code in (200, 201):
                data = resp.json().get("data", {})
                return {"status": "ok", "tweet_id": data.get("id"), "text": data.get("text")}
            return {"status": "error", "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}

    async def _action_reply(self, params: dict, access_token: str) -> dict:
        import httpx

        text = params.get("text", "")
        reply_to = params.get("reply_to_tweet_id", "")
        if not text or not reply_to:
            return {"status": "error", "error": "text and reply_to_tweet_id required"}

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{TWITTER_API}/tweets",
                json={"text": text, "reply": {"in_reply_to_tweet_id": reply_to}},
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                timeout=15,
            )
            if resp.status_code in (200, 201):
                data = resp.json().get("data", {})
                return {"status": "ok", "tweet_id": data.get("id")}
            return {"status": "error", "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}

    async def _action_retweet(self, params: dict, access_token: str) -> dict:
        import httpx

        tweet_id = params.get("tweet_id", "")
        if not tweet_id:
            return {"status": "error", "error": "tweet_id required"}

        async with httpx.AsyncClient() as client:
            # Get user ID for retweet endpoint
            me = await client.get(
                f"{TWITTER_API}/users/me",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10,
            )
            if me.status_code != 200:
                return {"status": "error", "error": "Failed to get user"}
            user_id = me.json().get("data", {}).get("id", "")

            resp = await client.post(
                f"{TWITTER_API}/users/{user_id}/retweets",
                json={"tweet_id": tweet_id},
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                timeout=15,
            )
            if resp.status_code == 200:
                return {"status": "ok", "retweeted": True}
            return {"status": "error", "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}

    @staticmethod
    def _normalize_tweet(tweet: dict) -> RawEvent | None:
        text = tweet.get("text", "")
        tweet_id = tweet.get("id", "")
        author_id = tweet.get("author_id", "")
        created_at = tweet.get("created_at", "")

        occurred_at = None
        if created_at:
            try:
                occurred_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            except ValueError:
                pass

        return RawEvent(
            source="twitter",
            source_account_id="twitter_primary",
            event_type="mention_received",
            entity_type="tweet",
            entity_id=tweet_id,
            occurred_at=occurred_at,
            title=f"@mention: {text[:80]}",
            summary=text[:500],
            actor={"type": "person", "twitter_id": author_id},
            raw_payload={
                "tweet_id": tweet_id,
                "author_id": author_id,
                "conversation_id": tweet.get("conversation_id"),
            },
        )
