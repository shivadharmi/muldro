"""Slack connector — polls for messages and mentions."""

import logging
from datetime import datetime, timezone

from src.connectors.base import BaseConnector, ConnectorHealth, register_connector
from src.services.event_processor import RawEvent

logger = logging.getLogger(__name__)


@register_connector("slack")
class SlackConnector(BaseConnector):
    """Polls Slack Web API for messages in configured channels."""

    async def poll(
        self, user_id: str, cursor: str | None, credentials: dict
    ) -> tuple[list[RawEvent], str | None]:
        """Poll Slack for new messages since timestamp cursor."""
        import httpx

        access_token = credentials.get("access_token", "")
        if not access_token:
            return [], cursor

        events = []
        new_cursor = cursor

        try:
            async with httpx.AsyncClient() as client:
                # Get channels the user is in
                channels_resp = await client.get(
                    "https://slack.com/api/conversations.list",
                    params={"types": "public_channel,private_channel,im,mpim", "limit": 20},
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=15,
                )

                if channels_resp.status_code != 200:
                    return [], cursor

                channels_data = channels_resp.json()
                if not channels_data.get("ok"):
                    return [], cursor

                for channel in channels_data.get("channels", [])[:10]:
                    channel_id = channel["id"]
                    channel_name = channel.get("name", channel_id)

                    params = {"channel": channel_id, "limit": 10}
                    if cursor:
                        params["oldest"] = cursor

                    hist_resp = await client.get(
                        "https://slack.com/api/conversations.history",
                        params=params,
                        headers={"Authorization": f"Bearer {access_token}"},
                        timeout=10,
                    )

                    if hist_resp.status_code != 200:
                        continue

                    hist_data = hist_resp.json()
                    if not hist_data.get("ok"):
                        continue

                    for msg in hist_data.get("messages", []):
                        event = self._normalize_message(msg, channel_id, channel_name)
                        if event:
                            events.append(event)
                            ts = msg.get("ts", "")
                            if not new_cursor or ts > new_cursor:
                                new_cursor = ts

        except Exception:
            logger.warning("Slack poll failed for user %s", user_id, exc_info=True)

        logger.info("Slack poll: %d events", len(events))
        return events, new_cursor

    async def test(self, credentials: dict) -> ConnectorHealth:
        """Test Slack connection."""
        import httpx

        access_token = credentials.get("access_token", "")
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://slack.com/api/auth.test",
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=10,
                )
                data = resp.json()
                if data.get("ok"):
                    return ConnectorHealth(
                        provider="slack",
                        status="healthy",
                        last_poll_at=datetime.now(timezone.utc),
                    )
                return ConnectorHealth(
                    provider="slack",
                    status="down",
                    last_poll_at=None,
                    error=data.get("error", "unknown"),
                )
        except Exception as e:
            return ConnectorHealth(provider="slack", status="down", last_poll_at=None, error=str(e))

    supports_actions: bool = True
    available_actions: list[str] = ["post_message", "update_message", "react"]

    async def execute_action(self, action: str, params: dict, credentials: dict) -> dict:
        """Execute a Slack write action."""
        if action not in self.available_actions:
            return {"status": "error", "error": f"Unknown action: {action}"}

        access_token = credentials.get("access_token", "")
        if not access_token:
            return {"status": "error", "error": "No access token"}

        dispatch = {
            "post_message": self._action_post_message,
            "update_message": self._action_update_message,
            "react": self._action_react,
        }
        return await dispatch[action](params, access_token)

    async def _action_post_message(self, params: dict, access_token: str) -> dict:
        """Post a message to a Slack channel."""
        import httpx

        channel = params.get("channel", "")
        text = params.get("text", "")
        if not channel or not text:
            return {"status": "error", "error": "channel and text required"}

        body = {"channel": channel, "text": text}
        if params.get("thread_ts"):
            body["thread_ts"] = params["thread_ts"]

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://slack.com/api/chat.postMessage",
                json=body,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=15,
            )
            data = resp.json()
            if data.get("ok"):
                return {"status": "ok", "ts": data.get("ts"), "channel": data.get("channel")}
            return {"status": "error", "error": data.get("error", "unknown")}

    async def _action_update_message(self, params: dict, access_token: str) -> dict:
        """Update a Slack message."""
        import httpx

        channel = params.get("channel", "")
        ts = params.get("ts", "")
        text = params.get("text", "")
        if not channel or not ts or not text:
            return {"status": "error", "error": "channel, ts, and text required"}

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://slack.com/api/chat.update",
                json={"channel": channel, "ts": ts, "text": text},
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=15,
            )
            data = resp.json()
            if data.get("ok"):
                return {"status": "ok", "ts": data.get("ts")}
            return {"status": "error", "error": data.get("error", "unknown")}

    async def _action_react(self, params: dict, access_token: str) -> dict:
        """Add a reaction to a Slack message."""
        import httpx

        channel = params.get("channel", "")
        ts = params.get("timestamp", "")
        name = params.get("name", "")
        if not channel or not ts or not name:
            return {"status": "error", "error": "channel, timestamp, and name required"}

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://slack.com/api/reactions.add",
                json={"channel": channel, "timestamp": ts, "name": name},
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10,
            )
            data = resp.json()
            if data.get("ok"):
                return {"status": "ok"}
            return {"status": "error", "error": data.get("error", "unknown")}

    async def get_auth_url(self, scopes: list[str] | None = None) -> str:
        return "/v1/auth/oauth/slack/authorize"

    @staticmethod
    def _normalize_message(msg: dict, channel_id: str, channel_name: str) -> RawEvent | None:
        """Convert a Slack message to a RawEvent."""
        subtype = msg.get("subtype", "")
        if subtype in ("channel_join", "channel_leave", "bot_message"):
            return None

        text = msg.get("text", "")
        user_id = msg.get("user", "")
        ts = msg.get("ts", "")

        occurred_at = None
        if ts:
            try:
                occurred_at = datetime.fromtimestamp(float(ts), tz=timezone.utc)
            except (ValueError, OSError):
                pass

        return RawEvent(
            source="slack",
            source_account_id="slack_primary",
            event_type="message_posted",
            entity_type="message_thread",
            entity_id=msg.get("thread_ts", ts),
            occurred_at=occurred_at,
            title=f"#{channel_name}: {text[:100]}",
            summary=text[:500],
            actor={"type": "person", "name": user_id, "slack_id": user_id},
            raw_payload={
                "channel_id": channel_id,
                "channel_name": channel_name,
                "ts": ts,
            },
        )
