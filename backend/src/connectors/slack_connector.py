"""Slack connector — polls for messages and mentions."""

import json
import logging
from datetime import datetime, timezone

from src.connectors.base import BaseConnector, ConnectorHealth, register_connector
from src.connectors.poll_result import PollErrorClass, PollResult, _classify_http_status
from src.services.event_processor import RawEvent

logger = logging.getLogger(__name__)

# Defensive page cap for a single conversations.list / conversations.history walk.
# Slack paginates via response_metadata.next_cursor; a misbehaving provider that
# always returned a next_cursor would otherwise loop forever. On truncation we warn
# so silent data loss is visible, consistent with the gmail/calendar/github
# connectors. Applied per channel for history and once for the channel list.
MAX_PAGES = 50

# Slack returns HTTP 200 with {"ok": false, "error": ...} for API-level failures.
# Map known error strings to PollErrorClass. Unknown errors default to "transient"
# (fail-safe) — never "permanent", which would open the circuit at threshold 1 and
# permanently disable Slack perception on a recoverable error (e.g. a rate limit).
_SLACK_OK_FALSE_ERROR_CLASS: dict[str, PollErrorClass] = {
    "ratelimited": "rate_limited",
    "account_inactive": "auth_failed",
    "token_revoked": "auth_failed",
    "invalid_auth": "auth_failed",
    "not_authed": "auth_failed",
    "internal_error": "transient",
    "fatal_error": "transient",
    "service_unavailable": "transient",
}


@register_connector("slack")
class SlackConnector(BaseConnector):
    """Polls Slack Web API for messages in configured channels."""

    # Cursor is a JSON map {channel_id: last_ts} serialized into the opaque cursor
    # string. Each channel keeps its own high-watermark so a chatty channel's
    # watermark can never skip a quiet channel's older messages.
    cursor_type: str = "per_channel_ts"

    @staticmethod
    def _parse_cursor(cursor: str | None) -> dict[str, str]:
        """Deserialize the per-channel cursor map from the opaque cursor string.

        Tolerates a legacy bare-string ``oldest_ts`` cursor (pre-Task-3.4) and any
        malformed value by starting fresh (empty map). A one-time re-scan per
        channel is acceptable; EventProcessor dedups on entity_id so no duplicates
        are surfaced downstream.
        """
        if not cursor:
            return {}
        try:
            parsed = json.loads(cursor)
        except (json.JSONDecodeError, TypeError):
            return {}
        if not isinstance(parsed, dict):
            return {}
        # Keep only str->str entries; ignore anything malformed.
        return {str(k): str(v) for k, v in parsed.items() if isinstance(v, (str, int, float))}

    async def poll(self, user_id: str, cursor: str | None, credentials: dict) -> PollResult:
        """Poll Slack for new messages, per-channel since each channel's watermark."""
        import httpx

        access_token = credentials.get("access_token", "")
        if not access_token:
            return PollResult(events=[], cursor=cursor, error_class="auth_failed")

        incoming_map = self._parse_cursor(cursor)
        events: list[RawEvent] = []
        # Start from the incoming watermarks; on a fully-clean poll each channel
        # that drains successfully overwrites its own entry. On ANY channel error
        # we discard this map entirely and return the INCOMING cursor unchanged.
        new_map: dict[str, str] = dict(incoming_map)

        try:
            async with httpx.AsyncClient() as client:
                channels = await self._list_channels(client, access_token, user_id)
                if isinstance(channels, PollResult):
                    # conversations.list failed outright — abort with the INCOMING
                    # cursor (never advance on error); _list_channels can't see it.
                    return PollResult(events=[], cursor=cursor, error_class=channels.error_class)

                for channel in channels:
                    channel_id = channel["id"]
                    channel_name = channel.get("name", channel_id)
                    oldest = incoming_map.get(channel_id)

                    drained, max_ts, channel_error = await self._poll_channel(
                        client,
                        access_token,
                        user_id,
                        channel_id,
                        channel_name,
                        oldest,
                    )

                    if channel_error is not None:
                        # Per-channel error → fail the whole poll with empty events
                        # and the INCOMING cursor UNCHANGED. The consumer discards
                        # events on any failure and never advances the cursor on a
                        # failing poll, so returning partial events / a partially-
                        # advanced channel map would be silently dropped. Be honest
                        # about that pipeline invariant: nothing advances on error.
                        logger.warning(
                            "Slack poll: channel %s errored (class=%s) for user %s; "
                            "returning empty events and unchanged cursor",
                            channel_id,
                            channel_error,
                            user_id,
                        )
                        return PollResult(events=[], cursor=cursor, error_class=channel_error)

                    events.extend(drained)

                    # Channel drained cleanly: advance ITS OWN watermark to its max ts.
                    if max_ts is not None:
                        prior = new_map.get(channel_id)
                        if prior is None or max_ts > prior:
                            new_map[channel_id] = max_ts

        except Exception:
            logger.warning("Slack poll failed for user %s", user_id, exc_info=True)
            return PollResult(events=[], cursor=cursor, error_class="transient")

        # Clean poll across ALL channels: per-channel isolation preserved — each
        # successfully-drained channel advanced its OWN watermark independently.
        new_cursor = json.dumps(new_map) if new_map else cursor
        logger.info("Slack poll: %d events", len(events))
        return PollResult(events=events, cursor=new_cursor)

    async def _list_channels(
        self, client, access_token: str, user_id: str
    ) -> list[dict] | PollResult:
        """Enumerate ALL channels, following response_metadata.next_cursor.

        Returns the accumulated channel list, or a failing PollResult (with the
        INCOMING cursor untouched) if conversations.list fails — a channel-listing
        failure aborts the whole poll, since we can't know which channels to drain.
        """
        channels: list[dict] = []
        next_cursor: str | None = None
        pages_fetched = 0
        truncated = False

        while True:
            params: dict = {
                "types": "public_channel,private_channel,im,mpim",
                "limit": 200,
            }
            if next_cursor:
                params["cursor"] = next_cursor

            resp = await client.get(
                "https://slack.com/api/conversations.list",
                params=params,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=15,
            )

            if resp.status_code != 200:
                error_class = _classify_http_status(resp.status_code)
                logger.warning(
                    "Slack conversations.list returned %d for user %s",
                    resp.status_code,
                    user_id,
                )
                return PollResult(events=[], cursor=None, error_class=error_class)

            data = resp.json()
            if not data.get("ok"):
                # Slack returns HTTP 200 with {"ok": false, "error": ...}. Map known
                # errors; default transient (never permanent → no threshold-1 open).
                slack_error = data.get("error", "unknown")
                error_class = _SLACK_OK_FALSE_ERROR_CLASS.get(slack_error, "transient")
                logger.warning(
                    "Slack conversations.list error=%s (class=%s) for user %s",
                    slack_error,
                    error_class,
                    user_id,
                )
                return PollResult(events=[], cursor=None, error_class=error_class)

            channels.extend(data.get("channels", []))

            pages_fetched += 1
            next_cursor = (data.get("response_metadata") or {}).get("next_cursor") or None
            if not next_cursor:
                break
            if pages_fetched >= MAX_PAGES:
                truncated = True
                break

        if truncated:
            logger.warning(
                "Slack conversations.list truncated at %d pages for user %s; "
                "remaining channels were not enumerated this poll",
                MAX_PAGES,
                user_id,
            )

        return channels

    async def _poll_channel(
        self,
        client,
        access_token: str,
        user_id: str,
        channel_id: str,
        channel_name: str,
        oldest: str | None,
    ) -> tuple[list[RawEvent], str | None, PollErrorClass | None]:
        """Drain one channel's history, following response_metadata.next_cursor.

        Returns ``(events, max_ts, error_class)``. ``error_class`` is ``None`` on
        success. On error, the partial events drained before the failure are still
        returned, but ``max_ts`` is ``None`` so the caller does NOT advance this
        channel's watermark (cursor-never-advance-on-error, per channel).
        """
        events: list[RawEvent] = []
        max_ts: str | None = None
        next_cursor: str | None = None
        pages_fetched = 0
        truncated = False

        while True:
            params: dict = {"channel": channel_id, "limit": 200}
            if next_cursor:
                params["cursor"] = next_cursor
            elif oldest:
                params["oldest"] = oldest

            resp = await client.get(
                "https://slack.com/api/conversations.history",
                params=params,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10,
            )

            if resp.status_code != 200:
                error_class = _classify_http_status(resp.status_code)
                logger.warning(
                    "Slack conversations.history returned %d for channel %s "
                    "(user %s, class=%s); keeping this channel's prior watermark",
                    resp.status_code,
                    channel_id,
                    user_id,
                    error_class,
                )
                return events, None, error_class

            data = resp.json()
            if not data.get("ok"):
                slack_error = data.get("error", "unknown")
                error_class = _SLACK_OK_FALSE_ERROR_CLASS.get(slack_error, "transient")
                logger.warning(
                    "Slack conversations.history error=%s (class=%s) for channel %s "
                    "(user %s); keeping this channel's prior watermark",
                    slack_error,
                    error_class,
                    channel_id,
                    user_id,
                )
                return events, None, error_class

            for msg in data.get("messages", []):
                ts = msg.get("ts", "")
                if ts and (max_ts is None or ts > max_ts):
                    max_ts = ts
                event = self._normalize_message(msg, channel_id, channel_name)
                if event:
                    events.append(event)

            pages_fetched += 1
            next_cursor = (data.get("response_metadata") or {}).get("next_cursor") or None
            if not next_cursor:
                break
            if pages_fetched >= MAX_PAGES:
                truncated = True
                break

        if truncated:
            # Slack conversations.history returns newest-first; next_cursor pages
            # walk BACKWARD in time toward `oldest`. On truncation we've drained the
            # newest pages but NOT the older ones near the prior watermark. Advancing
            # to max_ts (the newest) would skip those undrained older messages
            # forever. Keep the channel's prior watermark (return max_ts=None) so the
            # gap is re-fetched next poll; EventProcessor dedups the already-ingested
            # newer messages on entity_id.
            logger.warning(
                "Slack conversations.history truncated at %d pages for channel %s "
                "(user %s); keeping prior watermark so undrained older messages "
                "are re-fetched next poll",
                MAX_PAGES,
                channel_id,
                user_id,
            )
            return events, None, None

        return events, max_ts, None

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
