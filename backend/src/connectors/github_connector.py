"""GitHub connector — polls for notifications and events."""

import logging
import re
from datetime import datetime, timezone

from src.connectors.base import BaseConnector, ConnectorHealth, register_connector
from src.connectors.poll_result import PollResult, _classify_http_status
from src.services.event_processor import RawEvent

logger = logging.getLogger(__name__)

# Defensive page cap for a single notifications poll. GitHub paginates via the
# RFC5988 Link header (``rel="next"``); a misbehaving provider that always
# returns a next link would otherwise loop forever. On truncation we warn so
# silent data loss is visible, consistent with the gmail/calendar connectors.
MAX_PAGES = 50

# Matches one RFC5988 Link header segment, e.g. <https://...>; rel="next".
_LINK_SEGMENT_RE = re.compile(r'<(?P<url>[^>]+)>\s*;\s*rel="(?P<rel>[^"]+)"')


def _next_page_url(link_header: str | None) -> str | None:
    """Extract the ``rel="next"`` URL from an RFC5988 Link header, if present."""
    if not link_header:
        return None
    for match in _LINK_SEGMENT_RE.finditer(link_header):
        if match.group("rel") == "next":
            return match.group("url")
    return None


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
        # GitHub's cursor is the ISO-8601 ``since`` timestamp. Advance it to the
        # MAX updated_at across all returned notifications — NOT wall-clock now().
        # Advancing to now() would skip any notification updated between the last
        # item and now() forever. ``since`` is inclusive, so the boundary item may
        # re-appear next poll; that's fine — EventProcessor dedups on entity_id.
        max_updated_at: str | None = None
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.github+json",
        }

        try:
            async with httpx.AsyncClient() as client:
                # First request carries the query params (`since`, filters); follow-up
                # requests target the absolute rel="next" URL, which already encodes
                # the pagination state, so they pass no params.
                params: dict = {"all": "false", "participating": "true"}
                if cursor:
                    params["since"] = cursor

                next_url: str | None = "https://api.github.com/notifications"
                first_request = True
                pages_fetched = 0
                truncated = False

                while next_url:
                    if first_request:
                        resp = await client.get(
                            next_url, params=params, headers=headers, timeout=15
                        )
                        first_request = False
                    else:
                        resp = await client.get(next_url, headers=headers, timeout=15)

                    if resp.status_code != 200:
                        # GitHub returns 403 (not 429) for rate limits: the primary
                        # limit sets X-RateLimit-Remaining: 0, the secondary/abuse
                        # limit sets Retry-After. These are recoverable rate limits,
                        # NOT auth failures — the shared helper can't see headers, so
                        # discriminate here before falling back to status-only mapping.
                        if resp.status_code == 403 and (
                            resp.headers.get("X-RateLimit-Remaining") == "0"
                            or resp.headers.get("Retry-After")
                        ):
                            error_class = "rate_limited"
                        else:
                            error_class = _classify_http_status(resp.status_code)
                        logger.warning(
                            "GitHub notifications API returned %d for user %s",
                            resp.status_code,
                            user_id,
                        )
                        # Cursor never advances on error.
                        return PollResult(events=[], cursor=cursor, error_class=error_class)

                    notifications = resp.json()
                    for notif in notifications:
                        event = self._normalize_notification(notif)
                        if event:
                            events.append(event)
                        updated_at = notif.get("updated_at")
                        if updated_at and (max_updated_at is None or updated_at > max_updated_at):
                            max_updated_at = updated_at

                    pages_fetched += 1
                    next_url = _next_page_url(resp.headers.get("Link"))
                    if not next_url:
                        break
                    if pages_fetched >= MAX_PAGES:
                        truncated = True
                        break

                if truncated:
                    logger.warning(
                        "GitHub notifications poll truncated at %d pages for user %s; "
                        "remaining pages were not drained this poll",
                        MAX_PAGES,
                        user_id,
                    )

        except Exception:
            logger.warning("GitHub poll failed for user %s", user_id, exc_info=True)
            return PollResult(events=[], cursor=cursor, error_class="transient")

        # Advance the cursor to the max updated_at; if nothing was returned (or
        # updated_at was missing), keep the incoming cursor — never jump to now().
        new_cursor = max_updated_at if max_updated_at is not None else cursor

        logger.info("GitHub poll: %d events, cursor %s -> %s", len(events), cursor, new_cursor)
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
