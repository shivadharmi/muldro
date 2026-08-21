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
    def _occurred_at_from_updated_at(value: object) -> datetime | None:
        """Parse a notification's ``updated_at`` into a tz-aware UTC datetime.

        GitHub sends ISO-8601 with a trailing ``Z``. Without this every GitHub
        event carried ``occurred_at=None``, and the two consumers of that None
        disagree: the frame builder falls back to ``now()`` while the feed
        grouper sorts a missing timestamp at ``datetime.min`` - so one event
        would render "just now" on a card the feed had ordered at year-1.

        Always aware, never naive: a naive value raises on any comparison
        against an aware one downstream. Always total, never raising: a bad
        timestamp must cost this one event its time, not take down the whole
        poll for the source.
        """
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _entity_id_from_subject_url(url: str | None, fallback: str) -> str:
        """Derive a durable "owner/repo#number" id from a subject API URL.

        A notification is an occurrence; the pull request or issue is the
        thing. Keying on the notification meant one PR collecting three
        review comments minted three identities and three cards.

        A wrong id is worse than the fallback - it silently merges two
        different things onto one card - so this only accepts the exact
        ``.../repos/{owner}/{repo}/{kind}/{number}`` shape and falls back on
        everything else. In particular the number must sit in the number
        POSITION: ``.../issues/comments/12345`` is a comment, not issue
        12345, and matching on trailing digits would collapse every comment
        thread in a repo onto a single identity.
        """
        if not isinstance(url, str):
            return fallback
        path = url.split("?", 1)[0].split("#", 1)[0]
        parts = [p for p in path.split("/") if p]
        # The API prefix is always the first "repos" segment, so an owner or
        # repo of that name (.../repos/repos/repos/pulls/7) still resolves.
        if "repos" not in parts:
            return fallback
        i = parts.index("repos")
        if len(parts) != i + 5 or not parts[i + 4].isdigit():
            return fallback
        return f"{parts[i + 1]}/{parts[i + 2]}#{parts[i + 4]}"

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

        notification_id = notif.get("id", "")
        return RawEvent(
            source="github",
            source_account_id="github_primary",
            event_type=event_type,
            entity_type=notif_type.lower() if notif_type else "notification",
            entity_id=GitHubConnector._entity_id_from_subject_url(
                subject.get("url"), notification_id
            ),
            occurred_at=GitHubConnector._occurred_at_from_updated_at(notif.get("updated_at")),
            # The repo travels in actor now, so the title is the bare subject
            # and the frame composes the headline from the two.
            title=title,
            summary=f"{reason}: {title} in {repo}",
            # The commenter is not in the notifications payload. Marking this
            # a repository rather than "system" stops the headline builder
            # presenting a repo name as though it were a person.
            actor={"type": "repository", "name": repo},
            raw_payload={
                "notification_id": notification_id,
                "reason": reason,
                "repo": repo,
                "url": subject.get("url"),
            },
        )
